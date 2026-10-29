"""Child-process entry for pipeline.run (segfault isolation)."""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import traceback
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--inputs", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--result", required=True)
    args = p.parse_args(argv)

    result_path = Path(args.result)
    try:
        from pipeline.config import load_config
        from pipeline.errors import PipelineError
        from pipeline.run import run

        cfg = load_config(json.loads(Path(args.config).read_text(encoding="utf-8")))
        inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
        pkl = inputs.pop("weather_fixtures_pkl", None)
        if pkl and Path(pkl).exists():
            with open(pkl, "rb") as f:
                inputs["weather_fixtures"] = pickle.load(f)

        def report(step: str, pct: int, msg: str) -> None:
            print(json.dumps({"step": step, "pct": pct, "msg": msg}), flush=True)

        outs = run(cfg, inputs, args.workdir, report=report)
        payload = {
            "ok": True,
            "workdir": str(outs.workdir),
            "shp_zip": str(outs.shp_zip) if outs.shp_zip else None,
            "gpkg": str(outs.gpkg) if outs.gpkg else None,
            "zones_tif": str(outs.zones_tif) if outs.zones_tif else None,
            "preview_png": str(outs.preview_png) if outs.preview_png else None,
            "report_json": str(outs.report_json) if outs.report_json else None,
            "params_json": str(outs.params_json) if outs.params_json else None,
            "zones_geojson": None,
        }
        if outs.zones_gdf is not None:
            gdf = outs.zones_gdf.to_crs(4326)
            payload["zones_geojson"] = json.loads(gdf.to_json())
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        return 0
    except Exception as e:
        from pipeline.errors import PipelineError

        msg = e.message if isinstance(e, PipelineError) else "internal error"
        details = e.details if isinstance(e, PipelineError) else {}
        if not isinstance(e, PipelineError):
            details = {"traceback": traceback.format_exc()[-4000:]}
            msg = str(e) or msg
        result_path.write_text(
            json.dumps({"ok": False, "error": msg, "details": details}),
            encoding="utf-8",
        )
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
