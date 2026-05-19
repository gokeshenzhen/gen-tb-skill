#!/usr/bin/env python3
"""Phase 3 helper: emit behavior.md and parse_report.md skeletons."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def _refmodel_decision(intake_yaml: Path | None) -> tuple[str, str, str]:
    if intake_yaml is None or not intake_yaml.exists():
        return "skip", "spec_derived_basic", "heuristic"
    intake = yaml.safe_load(intake_yaml.read_text()) or {}
    language = intake.get("ref_model_language", "skip")
    if language in {"c_dpi", "py_dpi", "sv"}:
        return language, "user_provided", "golden"
    return language, "spec_derived_basic", "heuristic"


def emit_behavior_and_report(
    ip_name: str,
    spec_dir: Path,
    out_dir: Path,
    intake_yaml: Path | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_files = sorted(
        p for p in spec_dir.glob("*")
        if p.suffix.lower() in {".pdf", ".docx", ".md", ".xlsx", ".csv", ".xml"}
    )
    source_lines = "\n".join(f"- {p.name}" for p in spec_files) or "- none"
    ref_lang, ref_source, ref_trust = _refmodel_decision(intake_yaml)

    behavior = f"""# Behavior Summary

## Classification
- unknown

## Control Flow
- Not inferred by the lightweight parser.

## Data Path
- Not inferred by the lightweight parser.

## Reference Model
- language: {ref_lang}
- source: {ref_source}
- trust: {ref_trust}
- notes: Register-level behavior is represented by the generated RAL/reg block.
"""
    report = f"""# Parse Report

## Inputs
{source_lines}

## Selected Sources
- Register table selected by parse_regs.py when present.

## Assumptions
- Lightweight parse_spec.py did not infer algorithmic behavior for {ip_name}.

## Conflicts
- none recorded

## Reset value mismatches
- none recorded

## Alias decisions
- handled from registers.yaml

## Array folding
- handled from registers.yaml / RAL generation

## Side-effect inference
- none recorded

## Reference model decision
- language: {ref_lang}
- source: {ref_source}
- trust: {ref_trust}
- Register-level expected behavior belongs in the generated RAL/reg block.

## Warnings
- behavior.md is a conservative skeleton.
"""
    (out_dir / "behavior.md").write_text(behavior)
    (out_dir / "parse_report.md").write_text(report)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip-name", required=True)
    ap.add_argument("--spec-dir", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--intake-yaml", type=Path)
    args = ap.parse_args()
    emit_behavior_and_report(args.ip_name, args.spec_dir, args.out_dir, args.intake_yaml)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
