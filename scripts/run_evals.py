#!/usr/bin/env python3
"""
gen-tb eval harness — runs scaffold.py against each fixture and checks
the assertions in evals/evals.json. Writes a benchmark.json with per-eval
pass/fail + timing.

Usage:
    python3 scripts/run_evals.py [--with-skill | --baseline]
                                  [--out evals/iteration-N/]
                                  [--filter <eval_name>]

This harness mirrors skill-creator's eval framework but is lightweight —
no subagent spawn, no LLM grading. For gen-tb the work is mechanical:

  with_skill:  cd to a fresh workspace, copy the fixture's spec/RTL,
               write a hand-crafted intake.yaml (since intake interview
               is done by the model not by this harness), run scaffold.py,
               compile, run sanity, check assertions.

  baseline:    skip scaffold.py; emit nothing. Compile & sanity fail
               (no makefile generated). This baseline isn't very useful
               for v1.1; it'll be more interesting once we have a
               with-skill-but-vanilla-Claude comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from compile_and_sanity import read_log, run_make
from discover_inputs import emit_rtl_discovery
from parse_regs import parse_xlsx_to_yaml
from parse_spec import emit_behavior_and_report

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "evals" / "fixtures"


def _fixture_hash(fixture_dir: Path) -> str:
    """Hash all regular files in the fixture to detect pollution."""
    h = hashlib.sha256()
    for p in sorted(fixture_dir.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(fixture_dir)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _prepare_workspace(eval_def: dict, scratch_root: Path) -> Path:
    fixture = FIXTURES / eval_def["fixture"]
    ip_root = scratch_root / eval_def["fixture"]
    if ip_root.exists():
        shutil.rmtree(ip_root)
    ip_root.mkdir(parents=True)
    # Symlink the read-only inputs from the fixture
    (ip_root / "spec").symlink_to(fixture / "spec")
    (ip_root / "rtl").symlink_to(fixture / "rtl")
    if (fixture / "ref_model").exists():
        (ip_root / "ref_model").symlink_to(fixture / "ref_model")
    if (fixture / "vip").exists():
        (ip_root / "vip").symlink_to(fixture / "vip")
    (ip_root / ".prj_top").touch()
    return ip_root


def _emit_audit_inputs(eval_def: dict, ip_root: Path):
    """Hand-craft the audit inputs that the model would normally produce
    during Phase 1-3. The harness uses the canonical inputs captured per
    fixture under evals/fixtures/<name>/expected/."""
    audit = ip_root / "work" / "_gen_audit"
    norm = audit / "spec_normalized"
    norm.mkdir(parents=True, exist_ok=True)

    name = eval_def["fixture"]
    fixture_root = FIXTURES / name
    expected = fixture_root / "expected"

    # intake.yaml — re-root any references to the source ip_root
    intake_name = eval_def.get("intake_yaml", "intake.yaml")
    intake_content = (expected / intake_name).read_text()
    # rewrite ip_root references so paths point to this run's ip_root
    intake_content = re.sub(r"ip_root:.*", f"ip_root: {ip_root}", intake_content)
    intake_content = re.sub(
        r"\$PROJ_DIR/(ref_model_src|ref_model)/",
        "$PROJ_DIR/ref_model/",
        intake_content,
    )
    (audit / "intake.yaml").write_text(intake_content)

    # rtl_discovery.yaml — re-emit with current paths
    emit_rtl_discovery(ip_root, name, audit / "rtl_discovery.yaml")

    # registers.yaml — parse from the fixture xlsx
    xlsx_candidates = list((fixture_root / "spec").glob("*_regs.xlsx"))
    if xlsx_candidates:
        parse_xlsx_to_yaml(xlsx_candidates[0], norm / "registers.yaml", norm / "parse_report.md")
    emit_behavior_and_report(name, fixture_root / "spec", norm, audit / "intake.yaml")


def _run_scaffold(ip_root: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scaffold.py"),
         "--ip-root", str(ip_root), "--force"],
        capture_output=True, text=True,
    )
    return result.returncode == 0, result.stdout + result.stderr


def _check_assertion(a: dict, ctx: dict) -> tuple[bool, str]:
    """Returns (passed, evidence)."""
    kind = a["kind"]
    ip_root = ctx["ip_root"]
    eval_def = ctx["eval_def"]

    if kind == "files_exist":
        files_txt = ROOT / a["files_txt"]
        missing = []
        for line in files_txt.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t") if "\t" in line else line.split(None, 1)
            path = parts[0]
            kind_label = parts[1] if len(parts) > 1 else "nonempty"
            target = ip_root / path
            if not target.exists():
                missing.append(f"{path} ({kind_label})")
        if missing:
            return False, f"missing: {', '.join(missing[:5])}{'...' if len(missing)>5 else ''}"
        return True, "all expected files present"

    if kind == "compile_exit_zero":
        rc = ctx["compile"]["rc"]
        return rc == 0, f"make comp rc={rc}"

    if kind == "compile_no_warnings":
        log = read_log(ip_root, ctx["sanity_test"], "comp")
        ignore = a.get("ignore_patterns", [])
        warns = []
        for line in log.splitlines():
            if "warning" in line.lower() or "Warning" in line:
                if any(re.search(p, line) for p in ignore):
                    continue
                warns.append(line.strip())
        if warns:
            return False, f"{len(warns)} warning(s); first: {warns[0][:120]}"
        return True, "0 warnings"

    if kind == "compile_log_contains":
        log = read_log(ip_root, ctx["sanity_test"], "comp")
        # also look at compile stdout in case log isn't there
        text = log + ctx["compile"]["stdout"] + ctx["compile"]["stderr"]
        return (a["needle"] in text), f"needle={a['needle']!r}"

    if kind == "sim_passes":
        run = ctx["sim_runs"].get(a["test"], {})
        if not run:
            return False, "test not run"
        if run["rc"] != 0:
            return False, f"make rc={run['rc']}"
        log = read_log(ip_root, a["test"], "sim")
        m_err = re.search(r"UVM_ERROR\s*:\s*(\d+)", log)
        m_fat = re.search(r"UVM_FATAL\s*:\s*(\d+)", log)
        if m_err and int(m_err.group(1)) > 0:
            return False, f"UVM_ERROR={m_err.group(1)}"
        if m_fat and int(m_fat.group(1)) > 0:
            return False, f"UVM_FATAL={m_fat.group(1)}"
        return True, "UVM_ERROR=0 UVM_FATAL=0"

    if kind == "log_contains":
        log = read_log(ip_root, a["test"], "sim")
        return (a["needle"] in log), f"needle={a['needle']!r}"

    if kind == "fixture_unchanged":
        before = ctx["fixture_hash_before"]
        after = _fixture_hash(FIXTURES / eval_def["fixture"])
        return before == after, "hash match" if before == after else "fixture modified!"

    return False, f"unknown assertion kind: {kind}"


def run_one(eval_def: dict, scratch_root: Path, mode: str) -> dict:
    t0 = time.time()
    fixture_dir = FIXTURES / eval_def["fixture"]
    ctx = {
        "eval_def": eval_def,
        "fixture_hash_before": _fixture_hash(fixture_dir),
        "sim_runs": {},
    }

    # 1. Set up workspace
    ip_root = _prepare_workspace(eval_def, scratch_root)
    ctx["ip_root"] = ip_root

    if mode == "with-skill":
        _emit_audit_inputs(eval_def, ip_root)
        scaffold_ok, scaffold_log = _run_scaffold(ip_root)
        if not scaffold_ok:
            return {
                "id": eval_def["id"], "name": eval_def["name"], "mode": mode,
                "passed": False, "stage": "scaffold", "log": scaffold_log[-1000:],
                "duration_s": round(time.time() - t0, 2),
            }

    # 2. Identify the sanity test name (best-effort)
    ctx["sanity_test"] = f"{eval_def['fixture']}_sanity_test"

    # 3. Compile
    ctx["compile"] = run_make(ip_root, "comp", ctx["sanity_test"])
    # 4. Run each test referenced by assertions
    tests_to_run = sorted({a["test"] for a in eval_def["assertions"] if "test" in a})
    for t in tests_to_run:
        ctx["sim_runs"][t] = run_make(ip_root, "all", t)

    # 5. Check assertions
    results = []
    for a in eval_def["assertions"]:
        passed, evidence = _check_assertion(a, ctx)
        results.append({"text": a["name"], "passed": passed, "evidence": evidence})

    overall = all(r["passed"] for r in results)
    return {
        "id": eval_def["id"], "name": eval_def["name"], "mode": mode,
        "passed": overall,
        "expectations": results,
        "compile_rc": ctx["compile"]["rc"],
        "compile_duration_s": ctx["compile"]["duration_s"],
        "sim_durations_s": {t: r["duration_s"] for t, r in ctx["sim_runs"].items()},
        "duration_s": round(time.time() - t0, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["with-skill", "baseline"], default="with-skill")
    ap.add_argument("--out", type=Path, default=ROOT / "evals" / "iteration-1")
    ap.add_argument("--filter", help="run only this eval name")
    ap.add_argument("--scratch", type=Path, default=Path("/tmp/gen-tb-evals"))
    args = ap.parse_args()

    if args.scratch.exists():
        shutil.rmtree(args.scratch)
    args.scratch.mkdir(parents=True)
    args.out.mkdir(parents=True, exist_ok=True)

    spec = json.loads((ROOT / "evals" / "evals.json").read_text())
    runs = []
    for eval_def in spec["evals"]:
        if args.filter and eval_def["name"] != args.filter:
            continue
        print(f"  [run] {eval_def['name']} ({args.mode})")
        r = run_one(eval_def, args.scratch, args.mode)
        print(f"     → {'PASS' if r['passed'] else 'FAIL'} ({r['duration_s']}s)")
        for e in r.get("expectations", []):
            mark = "✓" if e["passed"] else "✗"
            print(f"        {mark} {e['text']:35s} {e['evidence']}")
        runs.append(r)

    bench = {
        "skill": spec["skill_name"],
        "mode": args.mode,
        "n_evals": len(runs),
        "n_passed": sum(1 for r in runs if r["passed"]),
        "runs": runs,
    }
    out = args.out / f"benchmark-{args.mode}.json"
    out.write_text(json.dumps(bench, indent=2))
    print(f"\nbenchmark saved: {out}")
    print(f"summary: {bench['n_passed']}/{bench['n_evals']} passed")
    return 0 if bench["n_passed"] == bench["n_evals"] else 1


if __name__ == "__main__":
    sys.exit(main())
