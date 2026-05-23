#!/usr/bin/env python3
"""Phase 1 helper: discover RTL inputs and emit rtl_discovery.yaml.

This is intentionally small today. It extracts the logic that the eval
harness used to inline so the real skill and the harness can share one
implementation.
"""

from __future__ import annotations

import argparse
import re
from collections import OrderedDict
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
    exposes full-AXI burst/ID signals despite an AXI-Lite-shaped bus.
    Tolerates direction-suffixed port names (`awlen_i` matches `awlen`)
    the same way the rest of the discovery layer does."""
    region = _top_port_region(top_file)
    ports = _port_names(region)
    normalized = {_norm_port(p) for p in ports}
    if not any(sig in normalized for sig in _AXI_FULL_SIGNALS):
        return False, 1
    # Derive ID width from an awid/arid declaration like `[3:0] awid` —
    # accept an optional `_i`/`_o`/`_in`/`_out` suffix on the port name.
    id_w = 1
    m = re.search(
        r"\[\s*(\d+)\s*:\s*0\s*\]\s*(?:ar|aw)id(?:_i|_o|_in|_out)?\b",
        region, flags=re.IGNORECASE)
    if m:
        id_w = int(m.group(1)) + 1
    return True, id_w


# All full-AXI sideband signals that the degraded-mode top may need to wire.
# The detector above can fire on any one of `_AXI_FULL_SIGNALS`; this list is
# what scaffold.py actually consults when generating DUT connections, so we
# must report *which* sidebands are present rather than assuming the full set.
_AXI_FULL_PORT_ROLES = (
    "awlen", "awsize", "awburst", "awid",
    "arlen", "arsize", "arburst", "arid",
    "bid", "rid", "wlast", "rlast",
)


def detect_axi_full_signals(top_file: Path) -> dict[str, str]:
    """Return {canonical_role: exact_RTL_name} for the subset of
    `_AXI_FULL_PORT_ROLES` actually present on the top module. scaffold.py
    needs the *exact* port name when wiring `.<dut_port>(axi.<role>)`; a DUT
    that names sidebands with direction suffixes (e.g. `awlen_i`) matched
    via `_norm_port` would otherwise get `.awlen(...)` and fail compile."""
    ports = _parse_ports(top_file)
    present: dict[str, str] = {}
    for canon in _AXI_FULL_PORT_ROLES:
        for p in ports:
            lp = p["name"].lower()
            if lp == canon or _norm_port(p["name"]) == canon:
                present[canon] = p["name"]
                break
    return present


def candidate_signals(top_file: Path) -> list[str]:
    """For an unknown bus: the request/response-looking port group.
    Heuristic — any port whose name hints at a handshake or data path."""
    ports = sorted(_port_names(_top_port_region(top_file)))
    hint = ("valid", "ready", "ack", "req", "stb", "data", "addr", "wr",
            "rd", "en", "sel", "cyc", "last", "strb")
    return [p for p in ports if any(h in p for h in hint)]


# ---------------------------------------------------------------------------
# Exact port discovery (G: rtl_discovery records exact RTL names/widths)
# ---------------------------------------------------------------------------
# Canonical bus role -> common RTL port-name variants used ONLY for matching.
# Matching is case-insensitive and tolerates _i/_o/_in/_out direction suffixes
# and vendor prefixes (`s_axi_awaddr`); the emitted rtl_discovery.yaml always
# carries the EXACT RTL port name and width, never the canonical form.
_PORT_VARIANTS: dict[str, dict[str, list[str]]] = {
    "apb": {
        "pclk":    ["clk", "apb_clk", "clk_in"],
        "presetn": ["preset_n", "rst_n", "resetn", "rstn", "areset_n"],
        "psel":    ["sel", "apb_sel"],
        "penable": ["enable", "apb_enable"],
        "pwrite":  ["write", "wr", "apb_write"],
        "paddr":   ["addr", "apb_addr", "address"],
        "pwdata":  ["wdata", "apb_wdata", "wr_data", "write_data", "writedata"],
        "prdata":  ["rdata", "apb_rdata", "rd_data", "read_data", "readdata"],
        "pready":  ["ready", "apb_ready"],
        "pslverr": ["slverr", "error", "err", "apb_slverr"],
    },
    "ahb": {
        "hclk":    ["clk", "ahb_clk"],
        "hresetn": ["hreset_n", "rst_n", "resetn", "rstn"],
        "hsel":    ["sel", "ahb_hsel"],
        "haddr":   ["addr", "ahb_addr", "address"],
        "htrans":  ["trans"],
        "hwrite":  ["write", "wr"],
        "hsize":   ["size"],
        "hburst":  ["burst"],
        "hprot":   ["prot"],
        "hwdata":  ["wdata", "ahb_wdata", "write_data"],
        "hrdata":  ["rdata", "ahb_rdata", "read_data"],
        "hready":  ["ready", "ahb_ready"],
        "hresp":   ["resp", "error"],
    },
    "axi_lite": {
        "aclk":    ["clk", "axi_clk"],
        "aresetn": ["areset_n", "rst_n", "resetn", "rstn"],
        "awvalid": ["aw_valid"], "awready": ["aw_ready"],
        "awaddr":  ["aw_addr"],  "awprot":  ["aw_prot"],
        "wvalid":  ["w_valid"],  "wready":  ["w_ready"],
        "wdata":   ["w_data"],   "wstrb":   ["w_strb", "wstb"],
        "bvalid":  ["b_valid"],  "bready":  ["b_ready"],
        "bresp":   ["b_resp"],
        "arvalid": ["ar_valid"], "arready": ["ar_ready"],
        "araddr":  ["ar_addr"],  "arprot":  ["ar_prot"],
        "rvalid":  ["r_valid"],  "rready":  ["r_ready"],
        "rdata":   ["r_data"],   "rresp":   ["r_resp"],
    },
}

# Per-bus role order for emitting the *_interface block.
_BUS_ROLES: dict[str, tuple[str, ...]] = {
    "apb": ("pclk", "presetn", "psel", "penable", "pwrite", "paddr",
            "pwdata", "prdata", "pready", "pslverr"),
    "ahb": ("hclk", "hresetn", "hsel", "haddr", "htrans", "hwrite", "hsize",
            "hburst", "hprot", "hwdata", "hrdata", "hready", "hresp"),
    "axi_lite": ("aclk", "aresetn", "awvalid", "awready", "awaddr", "awprot",
                 "wvalid", "wready", "wdata", "wstrb", "bvalid", "bready",
                 "bresp", "arvalid", "arready", "araddr", "arprot", "rvalid",
                 "rready", "rdata", "rresp"),
}

# Roles emitted as `{name, width}`; the value is the fallback width used only
# when the RTL width could not be resolved. All other roles emit a bare name.
_WIDTH_ROLES: dict[str, int] = {
    "paddr": 12, "pwdata": 32, "prdata": 32,
    "haddr": 12, "hwdata": 32, "hrdata": 32,
    "awaddr": 12, "araddr": 12, "wdata": 32, "rdata": 32, "wstrb": 4,
}

_DIR_SUFFIX = re.compile(r"_(i|o|in|out)$")


def _module_text(top_file: Path) -> str:
    if not top_file.exists():
        return ""
    text = top_file.read_text(errors="ignore")
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _verilog_int(tok: str) -> int | None:
    t = tok.strip().replace("_", "")
    for prefix, base in (("h", 16), ("b", 2), ("d", 10)):
        m = re.match(rf"^(?:\d+)?'[{prefix}{prefix.upper()}]([0-9a-fA-F]+)$", t)
        if m:
            try:
                return int(m.group(1), base)
            except ValueError:
                return None
    return int(t) if re.fullmatch(r"\d+", t) else None


def _resolve_params(text: str) -> dict[str, int]:
    """Collect `parameter`/`localparam` NAME = <int> bindings for width math."""
    params: dict[str, int] = {}
    for m in re.finditer(r"\b(?:parameter|localparam)\b([^;]*)", text):
        for pm in re.finditer(r"\b([A-Za-z_]\w*)\s*=\s*([0-9a-fA-F'_]+)", m.group(1)):
            val = _verilog_int(pm.group(2))
            if val is not None:
                params[pm.group(1)] = val
    return params


def _eval_dim(expr: str, params: dict[str, int]) -> int | None:
    e = expr.strip()
    for name, val in sorted(params.items(), key=lambda kv: -len(kv[0])):
        e = re.sub(rf"\b{re.escape(name)}\b", str(val), e)
    if not e or not re.fullmatch(r"[0-9\s+\-*/()]+", e):
        return None
    try:
        return int(eval(e, {"__builtins__": {}}, {}))  # guarded: digits/ops only
    except Exception:
        return None


def _width_of(dim: str | None, params: dict[str, int]) -> int | None:
    """Resolve a `[hi:lo]` packed dimension to a bit width, or None."""
    if not dim:
        return 1
    inner = dim.strip().strip("[]")
    if ":" not in inner:
        return 1
    hi_s, lo_s = inner.split(":", 1)
    hi, lo = _eval_dim(hi_s, params), _eval_dim(lo_s, params)
    if hi is None or lo is None:
        return None
    return abs(hi - lo) + 1


def _parse_ports(top_file: Path) -> list[dict]:
    """Best-effort parse of the top module's ports -> [{name, dir, width}].
    Handles ANSI headers and non-ANSI body declarations alike."""
    text = _module_text(top_file)
    if not text:
        return []
    params = _resolve_params(text)
    ports: "OrderedDict[str, dict]" = OrderedDict()
    pat = re.compile(
        r"\b(input|output|inout)\b\s*"
        r"(?:wire|reg|logic|var|tri|bit)?\s*(?:signed\s*)?"
        r"(\[[^\]]+\])?\s*"
        r"([A-Za-z_]\w*(?:\s*,\s*(?!input\b|output\b|inout\b)[A-Za-z_]\w*)*)"
    )
    for m in pat.finditer(text):
        direction = {"input": "in", "output": "out", "inout": "inout"}[m.group(1)]
        width = _width_of(m.group(2), params)
        for nm in re.split(r"\s*,\s*", m.group(3).strip()):
            if nm and nm not in ports:
                ports[nm] = {"name": nm, "dir": direction, "width": width}
    return list(ports.values())


def _norm_port(name: str) -> str:
    """Lowercase and drop a trailing _i/_o/_in/_out direction suffix."""
    return _DIR_SUFFIX.sub("", name.lower())


def _match_one(canon: str, variants: list[str],
               ports: list[dict], used: set[str]) -> dict | None:
    cands = [canon, *variants]
    best, best_score = None, 0
    for p in ports:
        if p["name"] in used:
            continue
        lp = p["name"].lower()
        npn = _norm_port(p["name"])
        score = 0
        for c in cands:
            if lp == c:
                score = max(score, 4)
            elif npn == c:
                score = max(score, 3)
            elif lp.endswith("_" + c):
                score = max(score, 2)
            elif npn.endswith("_" + c):
                score = max(score, 1)
        if score > best_score:
            best, best_score = p, score
    return best


def match_bus_ports(bus: str, top_file: Path) -> dict[str, dict]:
    """Map each canonical bus role to the EXACT discovered RTL port
    ({name, dir, width}). Roles with no confident match are omitted so the
    caller can fall back to the canonical name and flag low confidence."""
    variants = _PORT_VARIANTS.get(bus)
    if not variants:
        return {}
    ports = _parse_ports(top_file)
    used: set[str] = set()
    matched: dict[str, dict] = {}
    for role, vs in variants.items():
        hit = _match_one(role, vs, ports, used)
        if hit is not None:
            used.add(hit["name"])
            matched[role] = hit
    return matched


def _port_line(role: str, matched: dict[str, dict]) -> str:
    """Render one `*_interface` entry with the exact RTL name (and width)."""
    hit = matched.get(role)
    name = hit["name"] if hit else role
    if role in _WIDTH_ROLES:
        width = (hit["width"] if hit and hit.get("width") else None) \
            or _WIDTH_ROLES[role]
        return f"  {role}: {{name: {name}, width: {width}}}"
    return f"  {role}: {name}"


def _ordered_rtl_files(rtl_files: list[Path], top: str, ip_root: Path) -> list[Path]:
    order = [path for path in rtl_files if path.stem != top]
    if top:
        # Use the actual top file (.v or .sv) so a SystemVerilog top doesn't
        # get a nonexistent `<top>.v` appended to design.f. Fall back to .v
        # only when no matching file exists yet (stub-mode pre-generation).
        top_path = next((p for p in rtl_files if p.stem == top), None)
        order.append(top_path if top_path is not None
                     else ip_root / "rtl" / f"{top}.v")
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
            present = detect_axi_full_signals(top_file)
            entries = ", ".join(f"{role}: {name}" for role, name in present.items())
            lines.append("axi_full_signals: {" + entries + "}")

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
    # Interface block — exact RTL port names/widths matched from the top
    # module. Roles with no confident match fall back to the canonical name
    # (see _port_line); unmatched roles are recorded so a reviewer can check.
    matched = match_bus_ports(bus, top_file)
    section = {"apb": "apb_interface", "ahb": "ahb_interface",
               "axi_lite": "axi_lite_interface"}[bus]
    lines.append(f"{section}:")
    if bus in ("ahb", "axi_lite"):
        lines.append(f"  direction: {direction}")
    for role in _BUS_ROLES[bus]:
        lines.append(_port_line(role, matched))
    unmatched = [r for r in _BUS_ROLES[bus] if r not in matched]
    if unmatched:
        lines.append("  unmatched_roles: [" + ", ".join(unmatched) + "]")
    lines += [*_default_other_pads(ip_name)]
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
