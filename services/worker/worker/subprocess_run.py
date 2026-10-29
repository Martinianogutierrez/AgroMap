from __future__ import annotations

import json
import logging
import os
import pickle
import subprocess
import sys
from pathlib import Path
from typing import Any

from pipeline.errors import PipelineError

log = logging.getLogger(__name__)


def _run_inline(
    config: dict[str, Any],
    inputs: dict[str, Any],
    workdir: Path,
) -> dict[str, Any]:
    """In-process fallback when OS blocks child processes (some Windows hosts)."""
    from pipeline.config import load_config
    from pipeline.run import run

    inputs_local = dict(inputs)
    pkl = inputs_local.pop("weather_fixtures_pkl", None)
    if pkl and Path(pkl).exists():
        with open(pkl, "rb") as f:
            inputs_local["weather_fixtures"] = pickle.load(f)

    cfg = load_config(config)
    out_dir = workdir / "pipeline_out"
    outs = run(cfg, inputs_local, out_dir)
    payload: dict[str, Any] = {
        "ok": True,
        "workdir": str(outs.workdir),
        "shp_zip": str(outs.shp_zip) if outs.shp_zip else None,
        "gpkg": str(outs.gpkg) if outs.gpkg else None,
        "zones_tif": str(outs.zones_tif) if outs.zones_tif else None,
        "preview_png": str(outs.preview_png) if outs.preview_png else None,
        "report_json": str(outs.report_json) if outs.report_json else None,
        "params_json": str(outs.params_json) if outs.params_json else None,
        "zones_geojson": None,
        "inline": True,
    }
    if outs.zones_gdf is not None:
        gdf = outs.zones_gdf.to_crs(4326)
        payload["zones_geojson"] = json.loads(gdf.to_json())
    return payload


def run_pipeline_subprocess(
    *,
    config: dict[str, Any],
    inputs: dict[str, Any],
    workdir: Path | str,
    timeout_sec: int | None = None,
    memory_mb: int | None = None,
) -> dict[str, Any]:
    """
    Run pipeline in a child process so QGIS segfaults don't kill the worker.
    Falls back to in-process if FPZ_PIPELINE_INLINE=1 or child spawn fails (WinError 50).
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    timeout_sec = int(
        timeout_sec
        if timeout_sec is not None
        else os.environ.get("JOB_TIMEOUT_SEC", "1800")
    )
    _ = int(
        memory_mb if memory_mb is not None else os.environ.get("JOB_MEMORY_MB", "0")
    )

    inputs_ser = dict(inputs)
    wf = inputs_ser.pop("weather_fixtures", None)
    if wf is not None:
        fixtures_path = workdir / "weather_fixtures.pkl"
        with fixtures_path.open("wb") as f:
            pickle.dump(wf, f)
        inputs_ser["weather_fixtures_pkl"] = str(fixtures_path)

    if os.environ.get("FPZ_PIPELINE_INLINE", "").lower() in ("1", "true", "yes"):
        return _run_inline(config, inputs_ser, workdir)

    cfg_path = workdir / "_job_config.json"
    in_path = workdir / "_job_inputs.json"
    out_path = workdir / "_job_result.json"
    stdout_path = workdir / "_job_stdout.log"
    stderr_path = workdir / "_job_stderr.log"
    cfg_path.write_text(json.dumps(config), encoding="utf-8")
    in_path.write_text(json.dumps(inputs_ser), encoding="utf-8")

    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")

    # Prefer real interpreter over Windows Store alias stubs
    exe = sys.executable
    for candidate in (
        os.environ.get("FPZ_PYTHON"),
        str(Path(sys.prefix) / "python.exe"),
        str(Path(sys.base_prefix) / "python.exe"),
    ):
        if candidate and Path(candidate).is_file():
            exe = candidate
            break

    cmd = [
        exe,
        "-m",
        "worker.pipeline_entry",
        "--config",
        str(cfg_path),
        "--inputs",
        str(in_path),
        "--workdir",
        str(workdir / "pipeline_out"),
        "--result",
        str(out_path),
    ]

    try:
        with stdout_path.open("w", encoding="utf-8") as out_f, stderr_path.open(
            "w", encoding="utf-8"
        ) as err_f:
            proc = subprocess.run(
                cmd,
                env=env,
                stdout=out_f,
                stderr=err_f,
                timeout=timeout_sec,
                cwd=root,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except subprocess.TimeoutExpired as e:
        raise PipelineError(
            f"pipeline timed out after {timeout_sec}s; "
            "try fewer seasons or coarser resolution_m"
        ) from e
    except OSError as e:
        log.warning("subprocess spawn failed (%s); falling back to in-process", e)
        return _run_inline(config, inputs_ser, workdir)

    stderr_txt = (
        stderr_path.read_text(encoding="utf-8", errors="replace")
        if stderr_path.exists()
        else ""
    )
    stdout_txt = (
        stdout_path.read_text(encoding="utf-8", errors="replace")
        if stdout_path.exists()
        else ""
    )

    if proc.returncode != 0:
        err = (stderr_txt or stdout_txt or "").strip()[-2000:]
        if out_path.exists():
            try:
                payload = json.loads(out_path.read_text(encoding="utf-8"))
                if payload.get("error"):
                    raise PipelineError(
                        payload["error"], details=payload.get("details")
                    )
            except PipelineError:
                raise
            except Exception:
                pass
        raise PipelineError(err or f"pipeline exited {proc.returncode}")

    if not out_path.exists():
        raise PipelineError("pipeline produced no result file")
    result = json.loads(out_path.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise PipelineError(
            result.get("error") or "pipeline failed",
            details=result.get("details"),
        )
    return result
