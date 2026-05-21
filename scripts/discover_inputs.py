#!/usr/bin/env python3
"""Phase 1 helper: discover RTL inputs and emit rtl_discovery.yaml.

This is intentionally small today. It extracts the logic that the eval
harness used to inline so the real skill and the harness can share one
implementation.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def _find_top_module(rtl_files: list[Path], bus: str) -> str:
    instantiated: set[str] = set()
    for path in rtl_files:
        text = path.read_text(errors="ignore")
        for other in rtl_files:
            mod = other.stem
            if mod != path.stem and re.search(rf"\b{re.escape(mod)}\s+\w+\s*\(", text):
                instantiated.add(mod)
    candidates = [path.stem for path in rtl_files if path.stem not in instantiated]
    wrap_hint = f"{bus}_wrap"
    return next((c for c in candidates if wrap_hint in c), candidates[0] if candidates else "")


def _ordered_rtl_files(rtl_files: list[Path], top: str, ip_root: Path) -> list[Path]:
    order = [path for path in rtl_files if path.stem != top]
    if top:
        order.append(ip_root / "rtl" / f"{top}.v")
    return order


def _default_other_pads(ip_name: str) -> list[str]:
    if ip_name != "uart16550":
        return ["other_pads: []"]
    return [
        "other_pads:",
        "  - {name: irq,       dir: out, role: interrupt}",
        "  - {name: stx_pad_o, dir: out, role: serial_tx}",
        "  - {name: srx_pad_i, dir: in,  role: serial_rx}",
        "  - {name: rts_pad_o, dir: out, role: flow_ctrl}",
        "  - {name: cts_pad_i, dir: in,  role: flow_ctrl}",
        "  - {name: dtr_pad_o, dir: out, role: modem}",
        "  - {name: dsr_pad_i, dir: in,  role: modem}",
        "  - {name: ri_pad_i,  dir: in,  role: modem}",
        "  - {name: dcd_pad_i, dir: in,  role: modem}",
    ]


def render_rtl_discovery(
    ip_root: Path,
    ip_name: str,
    bus: str = "apb",
    direction: str = "slave",
) -> str:
    rtl_files = sorted((ip_root / "rtl").glob("*.v"))
    top = _find_top_module(rtl_files, bus)
    ordered = _ordered_rtl_files(rtl_files, top, ip_root)

    lines = [
        "mode: scan",
        f"ip_name: {ip_name}",
        f"ip_root: {ip_root}",
        "rtl_dir: $PROJ_DIR/rtl",
        "filelist_origin: generated",
        "top_module:",
        f"  name: {top}",
        f"  file: $PROJ_DIR/rtl/{top}.v",
        "  confidence: medium",
        "  method: regex_topology",
        "files:",
    ]
    for i, path in enumerate(ordered, 1):
        role = "top" if path.stem == top else "leaf"
        lines.append(f"  - {{path: $PROJ_DIR/rtl/{path.name}, role: {role}, order: {i}}}")

    if bus == "generic":
        # No standard interface section — bus details live in bus_handshake.yaml.
        # rtl_discovery still records the module + files so design.f and tb_top
        # have the DUT port info they need.
        lines += [
            "generic_interface:",
            "  note: ports described in work/_gen_audit/bus_handshake.yaml",
            *_default_other_pads(ip_name),
        ]
        return "\n".join(lines) + "\n"
    if bus == "axi_lite":
        lines += [
            "axi_lite_interface:",
            f"  direction: {direction}",
            "  aclk: aclk",
            "  aresetn: aresetn",
            "  awvalid: awvalid",
            "  awready: awready",
            "  awaddr:  {name: awaddr,  width: 12}",
            "  awprot:  awprot",
            "  wvalid:  wvalid",
            "  wready:  wready",
            "  wdata:   {name: wdata,  width: 32}",
            "  wstrb:   {name: wstrb,  width: 4}",
            "  bvalid:  bvalid",
            "  bready:  bready",
            "  bresp:   bresp",
            "  arvalid: arvalid",
            "  arready: arready",
            "  araddr:  {name: araddr,  width: 12}",
            "  arprot:  arprot",
            "  rvalid:  rvalid",
            "  rready:  rready",
            "  rdata:   {name: rdata,  width: 32}",
            "  rresp:   rresp",
            *_default_other_pads(ip_name),
        ]
    elif bus == "ahb":
        lines += [
            "ahb_interface:",
            f"  direction: {direction}",
            "  hclk: hclk",
            "  hresetn: hresetn",
            "  hsel: hsel",
            "  haddr:  {name: haddr,  width: 12}",
            "  htrans: htrans",
            "  hwrite: hwrite",
            "  hsize: hsize",
            "  hburst: hburst",
            "  hprot: hprot",
            "  hwdata: {name: hwdata, width: 32}",
            "  hrdata: {name: hrdata, width: 32}",
            "  hready: hready",
            "  hresp: hresp",
            *_default_other_pads(ip_name),
        ]
    else:
        lines += [
            "apb_interface:",
            "  pclk: pclk",
            "  presetn: presetn",
            "  psel: psel",
            "  penable: penable",
            "  pwrite: pwrite",
            "  paddr:  {name: paddr,  width: 12}",
            "  pwdata: {name: pwdata, width: 32}",
            "  prdata: {name: prdata, width: 32}",
            "  pready: pready",
            "  pslverr: pslverr",
            *_default_other_pads(ip_name),
        ]
    return "\n".join(lines) + "\n"


def emit_rtl_discovery(
    ip_root: Path,
    ip_name: str,
    out_yaml: Path,
    bus: str = "apb",
    direction: str = "slave",
) -> None:
    out_yaml.parent.mkdir(parents=True, exist_ok=True)
    out_yaml.write_text(render_rtl_discovery(ip_root, ip_name, bus, direction))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip-root", required=True, type=Path)
    ap.add_argument("--ip-name", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--bus", choices=["apb", "ahb", "axi_lite", "generic"], default="apb")
    ap.add_argument("--direction", choices=["slave", "master"], default="slave")
    args = ap.parse_args()
    emit_rtl_discovery(args.ip_root, args.ip_name, args.out, args.bus, args.direction)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
