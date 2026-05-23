#!/usr/bin/env python3
"""Pipeline hooks for gen-tb.

Convention:
  Look for <ip_root>/.gen_tb_hooks/<phase>[.strict].{py,sh} and run it
  after the named phase finishes writing its audit artifacts.

  - No hook file present → no-op, zero cost.
  - Non-strict hook fails  → log under work/_gen_audit/hooks/<phase>.log
                              and continue (warn).
  - `.strict.` in filename → non-zero exit re-raised as RuntimeError so
                              the phase aborts.

Hook execution contract:
  cwd     = ip_root
  env     += GEN_TB_PHASE, GEN_TB_IP_ROOT, GEN_TB_AUDIT_DIR
  stdin   = JSON payload (may be empty `{}`)
  stdout/stderr captured into work/_gen_audit/hooks/<phase>.log

Known phases:
  discover, intake, normalize, scaffold, compile_fix, sanity, handoff
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PHASES = (
    "discover", "intake", "normalize", "scaffold",
    "compile_fix", "sanity", "handoff",
)


def _resolve(ip_root: Path, phase: str) -> tuple[Path, bool] | None:
    hooks_dir = ip_root / ".gen_tb_hooks"
    if not hooks_dir.is_dir():
        return None
    for strict in (True, False):
        stem = f"{phase}.strict" if strict else phase
        for ext in (".py", ".sh"):
            candidate = hooks_dir / f"{stem}{ext}"
            if candidate.is_file():
                return candidate, strict
    return None


def run_hook(phase: str, ip_root: Path, payload: dict | None = None) -> dict:
    """Run the hook for `phase` if present. Returns a result record."""
    if phase not in PHASES:
        raise ValueError(f"unknown phase: {phase!r} (expected one of {PHASES})")

    ip_root = Path(ip_root).resolve()
    resolved = _resolve(ip_root, phase)
    if resolved is None:
        return {"phase": phase, "status": "skipped", "reason": "no hook"}

    hook_path, strict = resolved
    audit_dir = ip_root / "work" / "_gen_audit" / "hooks"
    audit_dir.mkdir(parents=True, exist_ok=True)
    log_path = audit_dir / f"{phase}.log"

    env = os.environ.copy()
    env["GEN_TB_PHASE"] = phase
    env["GEN_TB_IP_ROOT"] = str(ip_root)
    env["GEN_TB_AUDIT_DIR"] = str(ip_root / "work" / "_gen_audit")

    if hook_path.suffix == ".py":
        cmd = [sys.executable, str(hook_path)]
    else:
        cmd = ["bash", str(hook_path)]

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=ip_root, env=env,
            input=json.dumps(payload or {}),
            capture_output=True, text=True, timeout=120,
        )
        rc, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as e:
        rc, stdout, stderr, timed_out = 124, e.stdout or "", e.stderr or "", True

    duration = time.time() - t0
    with log_path.open("a") as fh:
        fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} "
                 f"phase={phase} hook={hook_path.name} strict={strict} "
                 f"rc={rc} duration={duration:.2f}s timed_out={timed_out}\n")
        if stdout:
            fh.write("[stdout]\n" + stdout + ("\n" if not stdout.endswith("\n") else ""))
        if stderr:
            fh.write("[stderr]\n" + stderr + ("\n" if not stderr.endswith("\n") else ""))

    result = {
        "phase": phase, "hook": str(hook_path), "strict": strict,
        "rc": rc, "duration_s": round(duration, 2),
        "log": str(log_path), "timed_out": timed_out,
    }
    if rc != 0:
        result["status"] = "failed"
        if strict:
            raise RuntimeError(
                f"strict hook for phase {phase!r} failed (rc={rc}); see {log_path}"
            )
    else:
        result["status"] = "ok"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a gen-tb pipeline hook.")
    ap.add_argument("phase", choices=PHASES)
    ap.add_argument("ip_root", type=Path)
    ap.add_argument("--payload", type=str, default=None,
                    help="JSON string passed to hook on stdin")
    args = ap.parse_args()

    payload = json.loads(args.payload) if args.payload else None
    try:
        result = run_hook(args.phase, args.ip_root, payload)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
