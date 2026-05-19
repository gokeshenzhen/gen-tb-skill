#!/usr/bin/env python3
"""Phase 5/6 helper: run generated make targets and read logs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


def run_make(ip_root: Path, target: str, sv_case: str | None = None) -> dict:
    env = os.environ.copy()
    env["PROJ_DIR"] = str(ip_root)
    env["WORK_DIR"] = str(ip_root / "work")
    if env.get("VCS_HOME") and not env.get("UVM_HOME"):
        env["UVM_HOME"] = env["VCS_HOME"] + "/etc/uvm-1.2"

    cmd = ["make", "-f", str(ip_root / "script" / "makefile"), target]
    if sv_case:
        cmd.append(f"SV_CASE={sv_case}")
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ip_root)
    return {
        "rc": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_s": round(time.time() - t0, 2),
    }


def read_log(ip_root: Path, sv_case: str, kind: str) -> str:
    fname = "run.log" if kind == "sim" else "comp.log"
    path = ip_root / "work" / f"work_{sv_case}_" / fname
    return path.read_text() if path.exists() else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip-root", required=True, type=Path)
    ap.add_argument("--target", required=True)
    ap.add_argument("--sv-case")
    args = ap.parse_args()
    print(json.dumps(run_make(args.ip_root, args.target, args.sv_case), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
