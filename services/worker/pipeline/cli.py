from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.config import config_json_schema, load_config
from pipeline.run import run


def cmd_schema(_: argparse.Namespace) -> int:
    json.dump(config_json_schema(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_synth(args: argparse.Namespace) -> int:
    from tests.synthetic import write_synthetic_field

    workdir = Path(args.workdir)
    write_synthetic_field(workdir, n_seasons=args.seasons, seed=args.seed)
    print(f"synthetic field written to {workdir}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(json.loads(Path(args.config).read_text(encoding="utf-8")))
    inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
    # resolve relative paths against inputs file dir
    base = Path(args.inputs).parent
    if "boundary" in inputs and isinstance(inputs["boundary"], str):
        p = Path(inputs["boundary"])
        inputs["boundary"] = str(p if p.is_absolute() else base / p)
    for s in inputs.get("seasons") or []:
        if "yield_path" in s:
            p = Path(s["yield_path"])
            s["yield_path"] = str(p if p.is_absolute() else base / p)

    def report(step: str, pct: int, msg: str) -> None:
        print(f"[{pct:3d}%] {step}: {msg}")

    outs = run(cfg, inputs, args.workdir, report=report)
    print(json.dumps({"shp_zip": str(outs.shp_zip), "report": str(outs.report_json)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_schema = sub.add_parser("schema", help="print JSON Schema")
    p_schema.set_defaults(func=cmd_schema)

    p_synth = sub.add_parser("synth", help="write synthetic field fixtures")
    p_synth.add_argument("--workdir", required=True)
    p_synth.add_argument("--seasons", type=int, default=3)
    p_synth.add_argument("--seed", type=int, default=42)
    p_synth.set_defaults(func=cmd_synth)

    p_run = sub.add_parser("run", help="run pipeline")
    p_run.add_argument("config")
    p_run.add_argument("--inputs", required=True)
    p_run.add_argument("--workdir", required=True)
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
