#!/usr/bin/env python3
"""Dispatcher for make e2e (works on Windows without bash)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    r = subprocess.run([sys.executable, str(ROOT / "e2e_local.py")], check=False)
    if r.returncode != 0:
        return r.returncode
    if os.environ.get("DATABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        return subprocess.run(
            [sys.executable, str(ROOT / "e2e_supabase.py")], check=False
        ).returncode
    print("skip e2e_supabase (set DATABASE_URL and SUPABASE_SERVICE_ROLE_KEY)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
