#!/usr/bin/env python3
"""Phase 5/6 helper: run generated make targets and read logs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path


def run_make(ip_root: Path, target: str, sv_case: str | None = None,
             timeout: int = 600) -> dict:
    env = os.environ.copy()
    env["PROJ_DIR"] = str(ip_root)
    env["WORK_DIR"] = str(ip_root / "work")
    if env.get("VCS_HOME") and not env.get("UVM_HOME"):
        env["UVM_HOME"] = env["VCS_HOME"] + "/etc/uvm-1.2"

    cmd = ["make", "-f", str(ip_root / "script" / "makefile"), target]
    if sv_case:
        cmd.append(f"SV_CASE={sv_case}")
    t0 = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env,
                                cwd=ip_root, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # A hung simulation (no testbench watchdog) must not run unbounded.
        # rc=124 follows the conventional `timeout(1)` exit code.
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode(errors="ignore")
        return {
            "rc": 124,
            "stdout": out,
            "stderr": f"TIMEOUT: `make {target}` exceeded {timeout}s and was killed",
            "duration_s": round(time.time() - t0, 2),
        }
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


def _judge_sim(rc: int, log: str) -> tuple[bool, str]:
    """Pass criterion for a sanity run — mirrors run_evals.py sim_passes:
    rc 0 and no non-zero UVM_ERROR / UVM_FATAL counts in run.log."""
    if rc != 0:
        return False, f"make rc={rc}"
    m_err = re.search(r"UVM_ERROR\s*:\s*(\d+)", log)
    m_fat = re.search(r"UVM_FATAL\s*:\s*(\d+)", log)
    if m_err and int(m_err.group(1)) > 0:
        return False, f"UVM_ERROR={m_err.group(1)}"
    if m_fat and int(m_fat.group(1)) > 0:
        return False, f"UVM_FATAL={m_fat.group(1)}"
    return True, "UVM_ERROR=0 UVM_FATAL=0"


def run_sanity(ip_root: Path, timeout: int = 600) -> dict:
    """Phase 6: compile, run every test in test/sv_list, and return a
    result dict. The caller persists it to work/_gen_audit/sanity_result.json."""
    comp = run_make(ip_root, "comp", timeout=timeout)
    result: dict = {
        "ip_name": ip_root.name,
        "generated_at_phase": 6,
        "compile": {
            "rc": comp["rc"],
            "duration_s": comp["duration_s"],
            "passed": comp["rc"] == 0,
        },
        "tests": [],
    }
    sv_list = ip_root / "test" / "sv_list"
    cases = (
        [ln.strip() for ln in sv_list.read_text().splitlines() if ln.strip()]
        if sv_list.exists() else []
    )
    if comp["rc"] == 0:
        for case in cases:
            run = run_make(ip_root, "all", case, timeout=timeout)
            passed, reason = _judge_sim(run["rc"], read_log(ip_root, case, "sim"))
            result["tests"].append({
                "name": case,
                "rc": run["rc"],
                "duration_s": run["duration_s"],
                "passed": passed,
                "reason": reason,
            })
    else:
        result["tests"] = [
            {"name": c, "rc": None, "duration_s": 0,
             "passed": False, "reason": "skipped — compile failed"}
            for c in cases
        ]
    result["all_passed"] = (
        result["compile"]["passed"]
        and bool(result["tests"])
        and all(t["passed"] for t in result["tests"])
    )
    return result


def write_sanity_result(ip_root: Path, timeout: int = 600) -> dict:
    result = run_sanity(ip_root, timeout)
    out = ip_root / "work" / "_gen_audit" / "sanity_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip-root", required=True, type=Path)
    ap.add_argument("--target", help="single make target to run")
    ap.add_argument("--sv-case")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--sanity", action="store_true",
                    help="Phase 6: compile + run sv_list, write sanity_result.json")
    args = ap.parse_args()
    if args.sanity:
        result = write_sanity_result(args.ip_root, args.timeout)
        print(json.dumps(result, indent=2))
        return 0 if result["all_passed"] else 1
    if not args.target:
        ap.error("either --target or --sanity is required")
    print(json.dumps(
        run_make(args.ip_root, args.target, args.sv_case, args.timeout), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
