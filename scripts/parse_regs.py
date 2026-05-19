#!/usr/bin/env python3
"""Phase 3 helper: normalize register tables into registers.yaml."""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_xlsx_to_yaml(xlsx: Path, out_yaml: Path) -> None:
    """Parse the fixture-compatible xlsx schema into registers.yaml."""
    from openpyxl import load_workbook

    wb = load_workbook(xlsx, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    regs: dict[tuple[str, int], dict] = {}
    for row in rows:
        if row[0] is None:
            continue
        name, off, width, access, reset, field_name, field_bits, field_access, field_reset, desc = row
        key = (name, int(off))
        if key not in regs:
            regs[key] = {
                "name": name,
                "offset": int(off),
                "width": int(width),
                "access": access,
                "reset": int(reset),
                "fields": [],
            }
        regs[key]["fields"].append({
            "name": field_name,
            "bits": field_bits,
            "access": field_access,
            "reset": int(field_reset) if field_reset is not None else 0,
            "desc": desc or "",
        })

    out = ["registers:"]
    for reg in regs.values():
        out.append(f"  - name: {reg['name']}")
        out.append(f"    offset: 0x{reg['offset']:02X}")
        out.append(f"    width: {reg['width']}")
        out.append(f"    access: {reg['access']}")
        out.append(f"    reset: 0x{reg['reset']:X}")
        out.append("    fields:")
        for field in reg["fields"]:
            out.append(f"      - name: {field['name']}")
            out.append(f"        bits: \"{field['bits']}\"")
            out.append(f"        access: {field['access']}")
            out.append(f"        reset: 0x{field['reset']:X}")
    out_yaml.parent.mkdir(parents=True, exist_ok=True)
    out_yaml.write_text("\n".join(out) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    parse_xlsx_to_yaml(args.xlsx, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
