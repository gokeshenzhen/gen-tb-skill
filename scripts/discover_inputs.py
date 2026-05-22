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


# Port-name signatures used to classify a bus from the top module header.
_BUS_SIGNATURES = {
    "apb":      {"psel", "penable", "pwrite", "paddr", "pwdata", "prdata"},
    "ahb":      {"htrans", "hwrite", "haddr", "hwdata", "hrdata", "hready"},
    "axi_lite": {"awvalid", "awready", "awaddr", "wvalid", "arvalid", "rdata"},
}
# Full-AXI signals that, if present, mean this is NOT plain AXI4-Lite.
_AXI_FULL_SIGNALS = ("awlen", "arlen", "awburst", "arburst", "awid", "arid")


def _top_port_region(top_file: Path) -> str:
    """Return the text of the top module's port list (module header up to
    the first `);`). Best-effort — used only for port-name detection."""
    if not top_file.exists():
        return ""
    text = top_file.read_text(errors="ignore")
    # Strip line + block comments so commented-out ports don't false-trigger.
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    m = re.search(r"\bmodule\b.*?\((.*?)\)\s*;", text, flags=re.DOTALL)
    return m.group(1) if m else text


def _port_names(port_region: str) -> set[str]:
    """Lowercased identifiers appearing in a module port region."""
    return {tok.lower() for tok in re.findall(r"\b[A-Za-z_]\w*\b", port_region)}


def classify_bus(top_file: Path) -> str:
    """Classify the bus from the top module's port signature.
    Returns apb | ahb | axi_lite | unknown. AXI-full signals still
    classify as axi_lite here (degraded mode is decided downstream)."""
    ports = _port_names(_top_port_region(top_file))
    best, best_hits = "unknown", 0
    for bus, sig in _BUS_SIGNATURES.items():
        hits = len(sig & ports)
        # Require a clear majority of the signature to claim a match.
        if hits >= max(4, len(sig) - 1) and hits > best_hits:
            best, best_hits = bus, hits
    return best


def detect_axi_full(top_file: Path) -> tuple[bool, int]:
    """Return (axi_full_signature, id_width). True when the top module
    exposes full-AXI burst/ID signals despite an AXI-Lite-shaped bus."""
    region = _top_port_region(top_file)
    ports = _port_names(region)
    if not any(sig in ports for sig in _AXI_FULL_SIGNALS):
        return False, 1
    # Derive ID width from an awid/arid declaration like `[3:0] awid`.
    id_w = 1
    m = re.search(r"\[\s*(\d+)\s*:\s*0\s*\]\s*(?:ar|aw)id\b", region, flags=re.IGNORECASE)
    if m:
        id_w = int(m.group(1)) + 1
    return True, id_w


def candidate_signals(top_file: Path) -> list[str]:
    """For an unknown bus: the request/response-looking port group.
    Heuristic — any port whose name hints at a handshake or data path."""
    ports = sorted(_port_names(_top_port_region(top_file)))
    hint = ("valid", "ready", "ack", "req", "stb", "data", "addr", "wr",
            "rd", "en", "sel", "cyc", "last", "strb")
    return [p for p in ports if any(h in p for h in hint)]


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
    rtl_files = sorted((ip_root / "rtl").glob("*.v")) + \
                sorted((ip_root / "rtl").glob("*.sv"))
    rtl_files = sorted(set(rtl_files))
    top = _find_top_module(rtl_files, bus)
    ordered = _ordered_rtl_files(rtl_files, top, ip_root)
    top_file = (ip_root / "rtl" / f"{top}.v")
    if not top_file.exists():
        top_file = (ip_root / "rtl" / f"{top}.sv")

    lines = [
        "mode: scan",
        f"ip_name: {ip_name}",
        f"ip_root: {ip_root}",
        "rtl_dir: $PROJ_DIR/rtl",
        "filelist_origin: generated",
        "top_module:",
        f"  name: {top}",
        f"  file: $PROJ_DIR/rtl/{top_file.name}",
        "  confidence: medium",
        "  method: regex_topology",
        "files:",
    ]
    for i, path in enumerate(ordered, 1):
        role = "top" if path.stem == top else "leaf"
        lines.append(f"  - {{path: $PROJ_DIR/rtl/{path.name}, role: {role}, order: {i}}}")

    # Phase 1 bus classification — record what the detector saw so the
    # skill (or a reviewer) can cross-check the bus the user declared.
    classified = classify_bus(top_file)
    lines.append(f"classified_bus: {classified}")
    if bus == "axi_lite":
        axi_full, id_w = detect_axi_full(top_file)
        if axi_full:
            lines.append("axi_full_signature: true")
            lines.append(f"axi_full_id_width: {id_w}")

    if bus == "generic":
        # No standard interface section — bus details live in bus_handshake.yaml.
        # rtl_discovery still records the module + files so design.f and tb_top
        # have the DUT port info they need. When the classifier could not name
        # the bus, surface the request/response-looking ports as candidates.
        lines += [
            "generic_interface:",
            "  note: ports described in work/_gen_audit/bus_handshake.yaml",
        ]
        if classified == "unknown":
            cands = candidate_signals(top_file)
            if cands:
                lines.append("  candidate_signals: [" + ", ".join(cands) + "]")
        lines += [*_default_other_pads(ip_name)]
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
