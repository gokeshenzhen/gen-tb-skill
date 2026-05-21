#!/usr/bin/env python3
"""
gen-tb eval harness — runs scaffold.py against each fixture and checks
the assertions in evals/evals.json.

Output layout (per invocation):

    <out>/                                # e.g. evals/iteration-1/
        benchmark-<mode>.json             # roll-up summary index
        <eval-name>/
            outputs/                      # snapshot of generated artifacts
            transcript.md                 # human/agent-readable run log
            assertions_result.json        # mechanical assertion verdicts

Downstream grader/comparator/analyzer agents (M2+) read from
`<eval-name>/` and write `grading.json` etc. alongside.

Usage:
    python3 scripts/run_evals.py [--mode with-skill|baseline]
                                  [--out evals/iteration-N/]
                                  [--filter <eval_name>]

This harness mirrors skill-creator's eval framework but is lightweight —
no subagent spawn, no LLM grading at this layer. For gen-tb the work is
mechanical:

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
import os
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
    bus_match = re.search(r"^bus_protocol:\s*(\w+)", intake_content, re.MULTILINE)
    bus = bus_match.group(1) if bus_match else "apb"
    dir_match = re.search(r"^bus_direction:\s*(\w+)", intake_content, re.MULTILINE)
    direction = dir_match.group(1) if dir_match else "slave"
    emit_rtl_discovery(ip_root, name, audit / "rtl_discovery.yaml", bus=bus, direction=direction)

    # Generic mode: copy pre-baked bus_handshake.yaml into audit dir.
    if bus == "generic":
        prebaked_hs = expected / "bus_handshake.yaml"
        if not prebaked_hs.exists():
            sys.exit(f"FATAL: fixture {name} is bus_protocol: generic but missing expected/bus_handshake.yaml")
        (audit / "bus_handshake.yaml").write_text(prebaked_hs.read_text())

    # registers.yaml — parse from the fixture xlsx, or copy a pre-baked one,
    # or fall back to an empty register set when the fixture has no registers.
    xlsx_candidates = list((fixture_root / "spec").glob("*_regs.xlsx"))
    prebaked = expected / "registers.yaml"
    if xlsx_candidates:
        parse_xlsx_to_yaml(xlsx_candidates[0], norm / "registers.yaml", norm / "parse_report.md")
    elif prebaked.exists():
        (norm / "registers.yaml").write_text(prebaked.read_text())
    else:
        (norm / "registers.yaml").write_text(
            f"version: 1\nip_name: {name}\nregisters: []\n"
        )
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

    if kind == "sub_agent_exit_zero":
        sub = ctx.get("sub_agent")
        if sub is None:
            return False, "sub-agent not invoked"
        if "skipped" in sub:
            return False, f"sub-agent skipped: {sub['skipped']}"
        rc = sub.get("rc", 1)
        return rc == 0, f"sub-agent rc={rc}"

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


_GENERATED_TOPLEVEL = ("tb", "test", "top", "script", "work", "CLAUDE.md", ".prj_top")


def _rel_or_abs(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p.resolve())


def _snapshot_outputs(ip_root: Path, outputs_dir: Path) -> None:
    """Copy generated artifacts from ip_root to outputs_dir.

    Skips the symlinked inputs (spec/, rtl/, ref_model/, vip/) which point
    back at fixtures and would be redundant or pollute the snapshot.
    """
    if outputs_dir.exists():
        shutil.rmtree(outputs_dir)
    outputs_dir.mkdir(parents=True)
    for name in _GENERATED_TOPLEVEL:
        src = ip_root / name
        if not src.exists():
            continue
        dst = outputs_dir / name
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=False, ignore_dangling_symlinks=True)
        else:
            shutil.copy2(src, dst)


def _write_transcript(eval_def: dict, ctx: dict, scaffold_log: str,
                       eval_outdir: Path, mode: str) -> None:
    """Write a human-readable execution transcript for grader/comparator use."""
    lines = []
    lines.append(f"# Eval transcript — {eval_def['name']} ({mode})\n")
    lines.append(f"- fixture: `{eval_def['fixture']}`")
    lines.append(f"- prompt:\n\n> {eval_def['prompt']}\n")
    lines.append(f"- expected_output:\n\n> {eval_def['expected_output']}\n")

    lines.append("## Scaffold\n")
    if mode == "with-skill":
        lines.append("```\n" + (scaffold_log[-4000:] or "(no output)") + "\n```\n")
    else:
        lines.append("(skipped in baseline mode)\n")

    comp = ctx.get("compile") or {}
    lines.append("## Compile\n")
    lines.append(f"- rc: `{comp.get('rc')}`")
    lines.append(f"- duration_s: `{comp.get('duration_s')}`")
    log = comp.get("stdout", "") + comp.get("stderr", "")
    if log:
        lines.append("```\n" + log[-3000:] + "\n```\n")

    for tname, run in ctx.get("sim_runs", {}).items():
        lines.append(f"## Sim: {tname}\n")
        lines.append(f"- rc: `{run.get('rc')}`")
        lines.append(f"- duration_s: `{run.get('duration_s')}`")
        try:
            sim_log = read_log(ctx["ip_root"], tname, "sim")
            if sim_log:
                lines.append("```\n" + sim_log[-3000:] + "\n```\n")
        except Exception as e:
            lines.append(f"(could not read sim log: {e})\n")

    (eval_outdir / "transcript.md").write_text("\n".join(lines))


def run_one(eval_def: dict, scratch_root: Path, mode: str,
             eval_outdir: Path, *, with_generic_sub_agent: bool = False,
             generic_sub_agent_timeout: int = 300,
             compile_fix_budget: int = 0,
             compile_fix_timeout: int = 240) -> dict:
    t0 = time.time()
    fixture_dir = FIXTURES / eval_def["fixture"]
    ctx = {
        "eval_def": eval_def,
        "fixture_hash_before": _fixture_hash(fixture_dir),
        "sim_runs": {},
    }
    eval_outdir.mkdir(parents=True, exist_ok=True)

    # 1. Set up workspace
    ip_root = _prepare_workspace(eval_def, scratch_root)
    ctx["ip_root"] = ip_root

    scaffold_log = ""
    if mode == "with-skill":
        _emit_audit_inputs(eval_def, ip_root)
        scaffold_ok, scaffold_log = _run_scaffold(ip_root)
        if not scaffold_ok:
            _snapshot_outputs(ip_root, eval_outdir / "outputs")
            _write_transcript(eval_def, ctx, scaffold_log, eval_outdir, mode)
            stage_result = {
                "id": eval_def["id"], "name": eval_def["name"], "mode": mode,
                "passed": False, "stage": "scaffold", "log": scaffold_log[-1000:],
                "duration_s": round(time.time() - t0, 2),
            }
            (eval_outdir / "assertions_result.json").write_text(
                json.dumps({"passed": False, "stage": "scaffold",
                            "expectations": []}, indent=2))
            return stage_result

    # 2. Identify the sanity test name (best-effort)
    ctx["sanity_test"] = f"{eval_def['fixture']}_sanity_test"

    # 2a. Generic-mode scaffold sub-agent (gated)
    if mode == "with-skill" and eval_def.get("requires_generic_sub_agent"):
        if not with_generic_sub_agent:
            # Caller-level skip already filters these; defensive no-op.
            return {
                "id": eval_def["id"], "name": eval_def["name"], "mode": mode,
                "passed": True, "skipped": True,
                "skipped_reason": "requires_generic_sub_agent but flag not set",
                "duration_s": round(time.time() - t0, 2),
            }
        sub = _run_generic_sub_agent(ip_root, timeout=generic_sub_agent_timeout)
        ctx["sub_agent"] = sub
        if not sub["ok"] and "skipped" in sub:
            # Environmental skip — don't mark eval failed.
            _snapshot_outputs(ip_root, eval_outdir / "outputs")
            _write_transcript(eval_def, ctx, scaffold_log, eval_outdir, mode)
            (eval_outdir / "assertions_result.json").write_text(json.dumps({
                "passed": True, "skipped": True,
                "skipped_reason": sub.get("skipped"),
                "expectations": [],
            }, indent=2))
            return {
                "id": eval_def["id"], "name": eval_def["name"], "mode": mode,
                "passed": True, "skipped": True,
                "skipped_reason": sub.get("skipped"),
                "stage": "sub_agent_skipped",
                "duration_s": round(time.time() - t0, 2),
            }

    # 3. Compile
    ctx["compile"] = run_make(ip_root, "comp", ctx["sanity_test"])

    # 3a. Compile-fix retry loop (gated on budget > 0)
    fix_attempts: list[dict] = []
    if ctx["compile"]["rc"] != 0 and compile_fix_budget > 0 and mode == "with-skill":
        generic_mode = bool(eval_def.get("requires_generic_sub_agent")) or \
                       (ctx.get("sub_agent", {}).get("ok") is True)
        for n in range(1, compile_fix_budget + 1):
            fix = _run_compile_fix_attempt(
                ip_root, n, ctx["sanity_test"],
                generic_mode=generic_mode, timeout=compile_fix_timeout)
            fix_attempts.append({"attempt": n, **{k: v for k, v in fix.items() if k != "log_tail"}})
            if fix.get("skipped"):
                # CLI missing / timeout — stop retrying, harness will report failure.
                break
            if not fix["ok"]:
                continue  # sub-agent failed, try again
            # Re-compile after each successful fix attempt
            ctx["compile"] = run_make(ip_root, "comp", ctx["sanity_test"])
            if ctx["compile"]["rc"] == 0:
                break
    ctx["compile_fix_attempts"] = fix_attempts

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

    # 6. Persist per-eval artifacts (outputs snapshot, transcript, assertions)
    _snapshot_outputs(ip_root, eval_outdir / "outputs")
    _write_transcript(eval_def, ctx, scaffold_log, eval_outdir, mode)
    assertions_doc = {
        "eval_name": eval_def["name"],
        "fixture": eval_def["fixture"],
        "mode": mode,
        "passed": overall,
        "summary": {
            "passed": sum(1 for r in results if r["passed"]),
            "failed": sum(1 for r in results if not r["passed"]),
            "total": len(results),
        },
        "expectations": results,
    }
    (eval_outdir / "assertions_result.json").write_text(
        json.dumps(assertions_doc, indent=2))

    return {
        "id": eval_def["id"], "name": eval_def["name"], "mode": mode,
        "passed": overall,
        "expectations": results,
        "compile_rc": ctx["compile"]["rc"],
        "compile_duration_s": ctx["compile"]["duration_s"],
        "compile_fix_attempts": ctx.get("compile_fix_attempts", []),
        "sim_durations_s": {t: r["duration_s"] for t, r in ctx["sim_runs"].items()},
        "duration_s": round(time.time() - t0, 2),
        "eval_outdir": _rel_or_abs(eval_outdir),
    }


GRADER_PROMPT_PATH = ROOT / "evals" / "agents" / "grader.md"


GENERIC_SUB_AGENT_CONTRACT = ROOT / "references" / "sub_agent_generic_scaffold.md"
COMPILE_FIX_CONTRACT = ROOT / "references" / "sub_agent_compile_fix.md"


def _run_compile_fix_attempt(ip_root: Path, attempt_n: int, sanity_test: str,
                              *, generic_mode: bool, timeout: int = 240) -> dict:
    """Spawn `claude -p` to fix one compile failure. Saves the attempt log
    and current comp.log under work/_gen_audit/compile_fix_attempts/. Returns
    the same shape as _run_generic_sub_agent."""
    audit = ip_root / "work" / "_gen_audit"
    attempts_dir = audit / "compile_fix_attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)

    comp_log = ip_root / "work" / f"work_{sanity_test}_" / "comp.log"
    # Snapshot the failing log to a numbered attempt artifact.
    snapshot = attempts_dir / f"attempt_{attempt_n}.log"
    if comp_log.exists():
        snapshot.write_text(comp_log.read_text())

    exemplars_note = ""
    if generic_mode:
        exemplars_note = (
            "\n\nBecause this is generic mode, also read for pattern-matching:\n"
            "- `references/generic_bus.md`\n"
            "- `references/apb.md`, `references/ahb.md`, `references/axi_lite.md`\n"
            "If a bus-agent file has structural errors (not just typos), you "
            "may regenerate the whole file from scratch once. Record that in "
            f"`work/_gen_audit/compile_fix_attempts/attempt_{attempt_n}.note.md`. "
            "Never change `tb_api::write/read/expect_reg` task signatures.\n"
        )

    driver_prompt = (
        "You are the gen-tb compile-fix sub-agent. A generated UVM testbench "
        f"under `{ip_root}` failed to compile. Fix the root cause without "
        "editing user RTL, user VIP source, specs, or normalized yaml inputs.\n\n"
        f"Read your contract first: `{COMPILE_FIX_CONTRACT}`.\n\n"
        f"Failing command: `make -f script/makefile comp SV_CASE={sanity_test}`\n"
        f"Log: `{snapshot}` (also live at `{comp_log}`)\n\n"
        "Inputs you may read:\n"
        "- work/_gen_audit/intake.yaml\n"
        "- work/_gen_audit/rtl_discovery.yaml\n"
        "- work/_gen_audit/spec_normalized/registers.yaml (may be empty for "
        "register_semantics: no)\n"
        "- work/_gen_audit/bus_handshake.yaml (only when bus_protocol: generic)\n"
        "- script/design.f, script/tb.f\n\n"
        "Editable scope: tb/, top/, test/, script/, work/_gen_audit/.\n"
        "Forbidden: rtl/, ref_model/, vip/, spec/; never touch intake.yaml, "
        "rtl_discovery.yaml, bus_handshake.yaml, or "
        "spec_normalized/registers.yaml.\n\n"
        "Rules:\n"
        "- Fix the first real root cause from the log; don't chase cosmetics.\n"
        "- Do not remove tests or weaken positive checks to get a pass.\n"
        "- Do not replace real RTL/VIP behavior with mocks.\n"
        + exemplars_note +
        "\nWhen done, print a brief root-cause summary and the list of files "
        "you changed. The harness re-runs `make comp` after you return.\n"
    )

    cmd = ["claude", "-p", driver_prompt, "--output-format", "text",
           "--allowedTools", "Read,Edit,Write,Glob,Grep",
           "--permission-mode", "bypassPermissions"]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=env, timeout=timeout, cwd=str(ip_root))
    except FileNotFoundError:
        return {"ok": False, "skipped": "claude CLI not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "skipped": f"compile-fix timed out after {timeout}s"}

    log_tail = (proc.stdout + proc.stderr)[-3000:]
    # Save sub-agent stdout too for forensics.
    (attempts_dir / f"attempt_{attempt_n}.agent.log").write_text(log_tail)
    if proc.returncode != 0:
        return {"ok": False, "rc": proc.returncode, "log_tail": log_tail}
    return {"ok": True, "rc": proc.returncode, "log_tail": log_tail}


def _run_generic_sub_agent(ip_root: Path, *, timeout: int = 300) -> dict:
    """Spawn `claude -p` to run the generic-mode scaffold sub-agent against
    a placeholder skeleton produced by scaffold.py. Returns:
      {"ok": True,  "rc": int, "log_tail": str}  -- agent ran and exited 0
      {"ok": False, "rc": int, "log_tail": str}  -- agent ran but failed
      {"ok": False, "skipped": str, ...}         -- environmental skip
    """
    contract_path = GENERIC_SUB_AGENT_CONTRACT
    prompt_path = ip_root / "work" / "_gen_audit" / "generic_bus_scaffold_prompt.md"
    if not prompt_path.exists():
        return {"ok": False, "skipped":
                "generic_bus_scaffold_prompt.md missing — scaffold did not run in generic mode"}

    driver_prompt = (
        "You are the gen-tb scaffold sub-agent for a generic-mode UVM "
        "testbench. Your workspace is the current working directory "
        f"(`{ip_root}`). \n\n"
        "Read these files in order and follow them:\n"
        f"1. `{contract_path}` — your contract; read it first.\n"
        f"2. `{prompt_path}` — per-run inputs and assumption log.\n"
        "3. The three exemplars listed in the contract "
        "(`references/apb.md`, `references/ahb.md`, `references/axi_lite.md`) "
        "and `references/generic_bus.md`.\n"
        f"4. `work/_gen_audit/bus_handshake.yaml` and "
        f"`work/_gen_audit/rtl_discovery.yaml` for the per-IP details.\n\n"
        "Then edit the placeholder files under `tb/` so the bus described in "
        "`bus_handshake.yaml` is actually driven and sampled. Do NOT compile "
        "or simulate — the harness handles that. Stay within the editable "
        "scope defined in the contract (`tb/`, `top/`, `test/`, `script/`, "
        "`work/_gen_audit/`).\n\n"
        "When done, append your assumption list to "
        f"`{prompt_path}` under the existing `## Assumptions made by sub-agent` "
        "heading, and print a one-line summary of what you changed.\n"
    )

    cmd = ["claude", "-p", driver_prompt, "--output-format", "text",
           "--allowedTools", "Read,Edit,Write,Glob,Grep",
           "--permission-mode", "bypassPermissions"]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=env, timeout=timeout, cwd=str(ip_root))
    except FileNotFoundError:
        return {"ok": False, "skipped": "claude CLI not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "skipped": f"sub-agent timed out after {timeout}s"}

    log_tail = (proc.stdout + proc.stderr)[-3000:]
    if proc.returncode != 0:
        return {"ok": False, "rc": proc.returncode, "log_tail": log_tail}
    return {"ok": True, "rc": proc.returncode, "log_tail": log_tail}


def grade_one(eval_def: dict, eval_outdir: Path, *,
               model: str | None = None, timeout: int = 600) -> dict:
    """Spawn `claude -p` to run the gen-tb quality grader on one eval.

    Writes `grading.json` next to `assertions_result.json`. Returns a
    dict with at least `{"ok": bool, "log_tail": str}`. Skipped (ok=True,
    skipped reason) if the spawn fails for environmental reasons — the
    mechanical assertions are the load-bearing pass/fail gate.
    """
    grading_path = eval_outdir / "grading.json"
    transcript = eval_outdir / "transcript.md"
    assertions = eval_outdir / "assertions_result.json"
    outputs_dir = eval_outdir / "outputs"
    fixture_expected = ROOT / "evals" / "fixtures" / eval_def["fixture"] / "expected"

    grader_md = GRADER_PROMPT_PATH.read_text()

    user_prompt = (
        f"You are running as the gen-tb quality grader for eval "
        f"`{eval_def['name']}` (fixture `{eval_def['fixture']}`).\n\n"
        f"Read the grader contract below and follow it exactly.\n\n"
        f"## Paths for this run\n\n"
        f"- outputs_dir: `{outputs_dir}`\n"
        f"- transcript_path: `{transcript}`\n"
        f"- assertions_result_path: `{assertions}`\n"
        f"- expected_dir (optional reference): `{fixture_expected}`\n"
        f"- grading.json output path: `{grading_path}`\n\n"
        f"## Grader contract\n\n"
        f"{grader_md}\n"
    )

    cmd = ["claude", "-p", user_prompt, "--output-format", "text",
           "--permission-mode", "bypassPermissions"]
    if model:
        cmd.extend(["--model", model])

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=env, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "skipped": "claude CLI not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "skipped": f"grader timed out after {timeout}s"}

    log_tail = (proc.stdout + proc.stderr)[-2000:]
    if not grading_path.exists():
        return {"ok": False, "skipped": "grader did not write grading.json",
                "log_tail": log_tail, "rc": proc.returncode}

    # Validate JSON shape
    try:
        json.loads(grading_path.read_text())
    except json.JSONDecodeError as e:
        return {"ok": False, "skipped": f"grading.json invalid: {e}",
                "log_tail": log_tail}

    return {"ok": True, "rc": proc.returncode, "log_tail": log_tail}


COMPARATOR_PROMPT_PATH = ROOT / "evals" / "agents" / "comparator.md"
_VERSION_TOKEN_RE = re.compile(
    r"iteration-\d+|candidate-[A-Za-z0-9_-]+|\bv\d+(?:\.\d+)*\b"
)


def _scrub_text(text: str) -> str:
    """Best-effort: remove version tokens that would leak side identity."""
    return _VERSION_TOKEN_RE.sub("REDACTED", text)


def _stage_side(src_eval_dir: Path, dst_side: Path) -> None:
    """Copy one eval's artifacts into a blind staging directory.

    Scrubs version tokens from text files. The outputs/ tree is copied
    as-is (file contents may contain version strings but path leaking is
    the main concern; agent is instructed not to weight on tokens).
    """
    dst_side.mkdir(parents=True, exist_ok=True)
    for name in ("transcript.md", "assertions_result.json", "grading.json"):
        src = src_eval_dir / name
        if not src.exists():
            continue
        try:
            content = src.read_text()
            (dst_side / name).write_text(_scrub_text(content))
        except UnicodeDecodeError:
            shutil.copy2(src, dst_side / name)
    src_outputs = src_eval_dir / "outputs"
    if src_outputs.exists():
        shutil.copytree(src_outputs, dst_side / "outputs",
                        symlinks=False, ignore_dangling_symlinks=True)


def compare_one(eval_def: dict, a_dir: Path, b_dir: Path,
                 staging_root: Path, *, model: str | None = None,
                 timeout: int = 900) -> dict:
    """Blind-compare two iteration outputs for a single eval.

    Returns dict with `ok`, optional `skipped`, and on success a
    de-blinded `winner` mapping back to "A" or "B".
    """
    eval_name = eval_def["name"]
    a_eval = a_dir / eval_name
    b_eval = b_dir / eval_name
    if not a_eval.exists() or not b_eval.exists():
        return {"ok": False, "skipped": f"eval missing on one side "
                                          f"(a={a_eval.exists()}, b={b_eval.exists()})"}

    # Randomize left/right assignment so the agent can't infer ordering.
    import random
    swap = random.choice([False, True])
    mapping = {"left": "B" if swap else "A", "right": "A" if swap else "B"}

    eval_staging = staging_root / eval_name
    if eval_staging.exists():
        shutil.rmtree(eval_staging)
    left_src = b_eval if swap else a_eval
    right_src = a_eval if swap else b_eval
    _stage_side(left_src, eval_staging / "left")
    _stage_side(right_src, eval_staging / "right")
    (eval_staging / "mapping.json").write_text(
        json.dumps({"mapping": mapping, "a_dir": str(a_dir),
                    "b_dir": str(b_dir)}, indent=2))

    comparison_blind_path = eval_staging / "comparison_blind.json"
    contract = COMPARATOR_PROMPT_PATH.read_text()
    user_prompt = (
        f"You are running as the gen-tb blind comparator for eval "
        f"`{eval_name}`.\n\n"
        f"## Paths for this run\n\n"
        f"- eval_name: `{eval_name}`\n"
        f"- eval_prompt: {eval_def['prompt']}\n"
        f"- expected_output: {eval_def['expected_output']}\n"
        f"- left_dir: `{eval_staging / 'left'}`\n"
        f"- right_dir: `{eval_staging / 'right'}`\n"
        f"- comparison_path: `{comparison_blind_path}`\n\n"
        f"## Comparator contract\n\n{contract}\n"
    )

    cmd = ["claude", "-p", user_prompt, "--output-format", "text",
           "--permission-mode", "bypassPermissions"]
    if model:
        cmd.extend(["--model", model])
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=env, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "skipped": "claude CLI not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "skipped": f"comparator timed out after {timeout}s"}

    log_tail = (proc.stdout + proc.stderr)[-2000:]
    if not comparison_blind_path.exists():
        return {"ok": False, "skipped": "comparator did not write JSON",
                "log_tail": log_tail, "rc": proc.returncode}

    try:
        blind = json.loads(comparison_blind_path.read_text())
    except json.JSONDecodeError as e:
        return {"ok": False, "skipped": f"comparison JSON invalid: {e}",
                "log_tail": log_tail}

    # De-blind: map left/right verdicts back to A/B.
    def _resolve(side: str) -> str:
        return mapping.get(side, side) if side in ("left", "right") else side

    resolved = dict(blind)
    resolved["winner"] = _resolve(blind.get("winner", "tie"))
    resolved["axes"] = [
        {**ax, "winner": _resolve(ax.get("winner", "tie"))}
        for ax in blind.get("axes", [])
    ]
    resolved["mapping"] = mapping

    return {"ok": True, "blind": blind, "resolved": resolved,
            "rc": proc.returncode, "log_tail": log_tail}


ANALYZER_PROMPT_PATH = ROOT / "evals" / "agents" / "analyzer.md"


def analyze_one(eval_def: dict, comparison_path: Path,
                 a_dir: Path, b_dir: Path, *,
                 skill_root: Path = ROOT, model: str | None = None,
                 timeout: int = 900) -> dict:
    """Spawn the Analyzer on a resolved comparison. Writes analysis.md
    next to comparison_path."""
    try:
        resolved = json.loads(comparison_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {"ok": False, "skipped": f"comparison_path unreadable: {e}"}

    winner = resolved.get("winner", "tie")
    confidence = resolved.get("confidence", "?")
    label_to_dir = {"A": a_dir, "B": b_dir}
    analysis_path = comparison_path.with_name(
        comparison_path.stem.replace(".comparison", "") + ".analysis.md"
    )

    if winner == "tie":
        analysis_path.write_text(
            f"# Analysis — {eval_def['name']}\n\n"
            f"Comparator returned `winner: tie` with confidence "
            f"`{confidence}`. No analysis warranted — neither side "
            f"clearly outperformed the other on the per-axis "
            f"verdicts. See "
            f"`{comparison_path.name}` for details.\n"
        )
        return {"ok": True, "skipped_reason": "tie",
                "analysis_path": str(analysis_path)}

    winner_dir = label_to_dir.get(winner)
    loser_label = "B" if winner == "A" else "A"
    loser_dir = label_to_dir.get(loser_label)
    if winner_dir is None or loser_dir is None:
        return {"ok": False,
                "skipped": f"winner label {winner!r} not in {{A,B}}"}

    contract = ANALYZER_PROMPT_PATH.read_text()
    user_prompt = (
        f"You are running as the gen-tb Analyzer for eval "
        f"`{eval_def['name']}`.\n\n"
        f"## Paths for this run\n\n"
        f"- eval_name: `{eval_def['name']}`\n"
        f"- comparison_path: `{comparison_path}`\n"
        f"- winner_label: `{winner}` (confidence: `{confidence}`)\n"
        f"- loser_label: `{loser_label}`\n"
        f"- winner_dir: `{winner_dir / eval_def['name']}`\n"
        f"- loser_dir: `{loser_dir / eval_def['name']}`\n"
        f"- skill_root: `{skill_root}`\n"
        f"- analysis_path: `{analysis_path}`\n\n"
        f"## Analyzer contract\n\n{contract}\n"
    )

    cmd = ["claude", "-p", user_prompt, "--output-format", "text",
           "--permission-mode", "bypassPermissions"]
    if model:
        cmd.extend(["--model", model])
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=env, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "skipped": "claude CLI not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False,
                "skipped": f"analyzer timed out after {timeout}s"}

    log_tail = (proc.stdout + proc.stderr)[-2000:]
    if not analysis_path.exists():
        return {"ok": False, "skipped": "analyzer did not write analysis.md",
                "log_tail": log_tail, "rc": proc.returncode}

    return {"ok": True, "analysis_path": str(analysis_path),
            "rc": proc.returncode, "log_tail": log_tail}


def cmd_compare(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="run_evals.py compare")
    ap.add_argument("a_dir", type=Path,
                    help="iteration directory A (e.g. evals/iteration-1)")
    ap.add_argument("b_dir", type=Path,
                    help="iteration directory B (e.g. evals/iteration-2)")
    ap.add_argument("--filter", help="compare only this eval name")
    ap.add_argument("--out", type=Path, default=None,
                    help="output directory for comparison.json files "
                         "(default: <b_dir>/_comparison/<a_label>__vs__<b_label>/)")
    ap.add_argument("--staging", type=Path,
                    default=Path("/tmp/gen-tb-compare"))
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--analyze", action="store_true",
                    help="after each non-tie comparison, spawn the Analyzer "
                         "to write analysis.md with root-cause + suggested "
                         "skill changes")
    ap.add_argument("--analyzer-timeout", type=int, default=900)
    args = ap.parse_args(argv)

    a_dir = args.a_dir.resolve()
    b_dir = args.b_dir.resolve()
    if not a_dir.is_dir() or not b_dir.is_dir():
        print(f"error: a_dir or b_dir missing", file=sys.stderr)
        return 2

    out = args.out or (b_dir / "_comparison" / f"{a_dir.name}__vs__{b_dir.name}")
    out.mkdir(parents=True, exist_ok=True)

    if args.staging.exists():
        shutil.rmtree(args.staging)
    args.staging.mkdir(parents=True)

    spec = json.loads((ROOT / "evals" / "evals.json").read_text())
    summary = []
    for eval_def in spec["evals"]:
        if args.filter and eval_def["name"] != args.filter:
            continue
        print(f"  [compare] {eval_def['name']}")
        t0 = time.time()
        r = compare_one(eval_def, a_dir, b_dir, args.staging,
                        model=args.model, timeout=args.timeout)
        elapsed = round(time.time() - t0, 1)
        if not r["ok"]:
            print(f"     ✗ skipped: {r['skipped']} ({elapsed}s)")
            if r.get("log_tail"):
                print("     ---claude -p tail---")
                for line in r["log_tail"].splitlines()[-20:]:
                    print(f"     {line}")
            summary.append({"eval": eval_def["name"], "ok": False,
                            "skipped": r["skipped"],
                            "log_tail": r.get("log_tail", "")[-500:]})
            continue
        resolved = r["resolved"]
        comparison_path = out / f"{eval_def['name']}.comparison.json"
        comparison_path.write_text(json.dumps(resolved, indent=2))
        winner = resolved.get("winner")
        confidence = resolved.get("confidence", "?")
        print(f"     ✓ winner={winner} confidence={confidence} ({elapsed}s)")
        entry = {"eval": eval_def["name"], "ok": True,
                 "winner": winner, "confidence": confidence,
                 "duration_s": elapsed}
        if args.analyze:
            print(f"     [analyze] spawning analyzer…")
            at0 = time.time()
            a = analyze_one(eval_def, comparison_path, a_dir, b_dir,
                            model=args.model, timeout=args.analyzer_timeout)
            a_elapsed = round(time.time() - at0, 1)
            if a["ok"]:
                tag = (a.get("skipped_reason") or "written")
                print(f"        ✓ analysis.md {tag} ({a_elapsed}s)")
            else:
                print(f"        ✗ analyzer skipped: {a.get('skipped','?')} ({a_elapsed}s)")
            entry["analyzer"] = {k: v for k, v in a.items() if k != "log_tail"}
            entry["analyzer_duration_s"] = a_elapsed
        summary.append(entry)

    (out / "summary.json").write_text(json.dumps({
        "a_dir": str(a_dir), "b_dir": str(b_dir),
        "results": summary,
    }, indent=2))
    print(f"\ncomparison summary saved: {out / 'summary.json'}")
    n_ok = sum(1 for s in summary if s.get("ok"))
    print(f"summary: {n_ok}/{len(summary)} comparisons completed")
    return 0


def cmd_run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="run_evals.py run")
    ap.add_argument("--mode", choices=["with-skill", "baseline"], default="with-skill")
    ap.add_argument("--out", type=Path, default=ROOT / "evals" / "iteration-1")
    ap.add_argument("--filter", help="run only this eval name")
    ap.add_argument("--scratch", type=Path, default=Path("/tmp/gen-tb-evals"))
    ap.add_argument("--grade", action="store_true",
                    help="after each eval, spawn `claude -p` to run the quality grader "
                         "(writes grading.json alongside assertions_result.json)")
    ap.add_argument("--grader-model", default=None,
                    help="model passed to `claude -p` for the grader (default: user's configured model)")
    ap.add_argument("--grader-timeout", type=int, default=600,
                    help="seconds before the grader subprocess is killed (default: 600)")
    ap.add_argument("--with-generic-sub-agent", action="store_true",
                    default=bool(os.environ.get("GENTB_EVAL_GENERIC_SUB_AGENT")),
                    help="for evals with requires_generic_sub_agent: true, spawn "
                         "`claude -p` to run the scaffold sub-agent between scaffold "
                         "and compile. Off by default (Claude API cost).")
    ap.add_argument("--generic-sub-agent-timeout", type=int, default=300,
                    help="seconds before the generic-mode sub-agent subprocess is killed")
    ap.add_argument("--compile-fix-budget", type=int, default=None,
                    help="number of compile-fix sub-agent attempts after a failing compile. "
                         "Default: 8 when --with-generic-sub-agent is set, else 0 (no retry). "
                         "Per design: built-in mode budget is 5, generic mode is 8.")
    ap.add_argument("--compile-fix-timeout", type=int, default=240,
                    help="seconds before each compile-fix sub-agent subprocess is killed")
    args = ap.parse_args(argv)
    if args.compile_fix_budget is None:
        args.compile_fix_budget = 8 if args.with_generic_sub_agent else 0

    if args.scratch.exists():
        shutil.rmtree(args.scratch)
    args.scratch.mkdir(parents=True)
    args.out.mkdir(parents=True, exist_ok=True)

    spec = json.loads((ROOT / "evals" / "evals.json").read_text())
    runs = []
    for eval_def in spec["evals"]:
        if args.filter and eval_def["name"] != args.filter:
            continue
        if eval_def.get("requires_generic_sub_agent") and not args.with_generic_sub_agent:
            print(f"  [skip] {eval_def['name']} (requires --with-generic-sub-agent)")
            continue
        print(f"  [run] {eval_def['name']} ({args.mode})")
        eval_outdir = args.out / eval_def["name"]
        r = run_one(eval_def, args.scratch, args.mode, eval_outdir,
                    with_generic_sub_agent=args.with_generic_sub_agent,
                    generic_sub_agent_timeout=args.generic_sub_agent_timeout,
                    compile_fix_budget=args.compile_fix_budget,
                    compile_fix_timeout=args.compile_fix_timeout)
        if r.get("skipped"):
            print(f"     → SKIP ({r.get('skipped_reason')}) ({r['duration_s']}s)")
        else:
            tag = "PASS" if r['passed'] else "FAIL"
            fixes = r.get("compile_fix_attempts") or []
            extra = f", fix-attempts={len(fixes)}" if fixes else ""
            print(f"     → {tag} ({r['duration_s']}s{extra})")
        for e in r.get("expectations", []):
            mark = "✓" if e["passed"] else "✗"
            print(f"        {mark} {e['text']:35s} {e['evidence']}")
        if args.grade and r.get("stage") != "scaffold":
            print(f"     [grade] spawning quality grader…")
            gt0 = time.time()
            g = grade_one(eval_def, eval_outdir, model=args.grader_model,
                          timeout=args.grader_timeout)
            elapsed = round(time.time() - gt0, 1)
            if g["ok"]:
                print(f"        ✓ grading.json written ({elapsed}s)")
            else:
                print(f"        ✗ grader skipped: {g.get('skipped','?')} ({elapsed}s)")
            r["grader"] = g
            r["grader_duration_s"] = elapsed
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


def main() -> int:
    """Subcommand dispatcher. Default subcommand is `run` for back-compat."""
    argv = sys.argv[1:]
    if argv and argv[0] == "compare":
        return cmd_compare(argv[1:])
    if argv and argv[0] == "run":
        return cmd_run(argv[1:])
    # back-compat: no subcommand → run
    return cmd_run(argv)


if __name__ == "__main__":
    sys.exit(main())
