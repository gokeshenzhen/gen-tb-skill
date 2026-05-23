#!/usr/bin/env python3
"""
gen-tb scaffold.py — materialize the UVM testbench tree from
intake.yaml + registers.yaml + rtl_discovery.yaml.

Usage:
    python3 scaffold.py --ip-root <path> [--force]

Reads (relative to <ip-root>):
    work/_gen_audit/intake.yaml
    work/_gen_audit/rtl_discovery.yaml
    work/_gen_audit/spec_normalized/registers.yaml

Writes:
    .prj_top
    script/{makefile, setup.sh, check_env.sh, design.f, tb.f}
    top/<ip>_tb_top.sv
    tb/<bus>_if.sv
    tb/<bus>_agt_top/{<bus>_agent.sv, <bus>_agt_config.sv, <bus>_trans.sv,
                      <bus>_driver.sv, <bus>_monitor.sv, <bus>_sequencer.sv,
                      <bus>_sequence.sv}
    tb/tb_api/{tb_api_pkg.sv, tb_api_primitives.svh}
    tb/dpi/{<ip>_ref_pkg.sv, <ip>_dpi_proto.h}     # if c_dpi
    tb/ral/<ip>_reg_block.sv
    tb/ral/<ip>_<bus>_adapter.sv                    # if generating fresh agent
    test/<ip>_pkg.sv (sanity + reg_access + random_seq + smoke)
    test/sv_list
    CLAUDE.md                                       # first scaffold only
    work/_gen_audit/scaffold_audit.json
    work/_gen_audit/unresolved.md                   # seeded placeholder
    work/_gen_audit/generic_bus_scaffold_diff.patch # generic mode only

Symlink guard: any write target whose realpath escapes ip_root is
refused with a non-zero exit and a message. No writes are performed
if any target fails the guard (atomic).

This file is the v1.3 first-working-draft for APB/AHB agent and RAL
generation; corner cases (multi-clock, py_dpi, full external VIP
drive glue) are still deferred to references.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Symlink guard (G17): refuse to write through a path whose realpath escapes
# the project root. Applied to every Write before materialization.
# ---------------------------------------------------------------------------


def _resolve_safely(target: Path, ip_root: Path) -> Path:
    """Return the resolved target path if it is inside ip_root.
    Raises RuntimeError if the resolved path escapes — including the
    case where `target` itself is a symlink (possibly dangling) pointing
    outside the root, not just when an ancestor directory is."""
    real_root = ip_root.resolve()
    if target.exists() or target.is_symlink():
        # Follow the entire chain on the target itself; .resolve() handles
        # both regular files and (dangling) symlinks per pathlib semantics.
        resolved = target.resolve()
    else:
        # File does not exist yet — resolve the deepest existing ancestor
        # and re-append the not-yet-created tail. Symlinks in the ancestor
        # chain are followed by .resolve(); a missing tail can't be a
        # symlink, so nothing else to follow.
        parent = target.parent
        check = parent
        while not check.exists() and check != check.parent:
            check = check.parent
        real_parent = check.resolve() / parent.relative_to(check)
        resolved = real_parent / target.name
    if not str(resolved).startswith(str(real_root) + os.sep) and resolved != real_root:
        raise RuntimeError(
            f"symlink guard: {target} resolves to {resolved}, outside {real_root}"
        )
    return resolved


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"FATAL: missing required input: {path}")
    with path.open() as f:
        return yaml.safe_load(f)


_WIDTH_KEY = {"apb": "paddr_width", "ahb": "haddr_width", "axi_lite": "axi_addr_width"}
_BUILTIN_BUSES = ("apb", "ahb", "axi_lite")
_SUPPORTED_SIMULATORS = ("vcs", "questa")


def _simulators(intake: dict) -> list[str]:
    """Normalize intake.simulators into an ordered, de-duped list.
    Default is ['vcs'] (backward compat with pre-Questa intake.yaml)."""
    raw = intake.get("simulators")
    if raw is None:
        return ["vcs"]
    if isinstance(raw, str):
        raw = [raw]
    seen: set[str] = set()
    out: list[str] = []
    for s in raw:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _yesno(value: Any, key: str) -> str:
    """YAML 1.1 maps unquoted yes/no to bool. Accept bool or yes/no string."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str) and value.lower() in ("yes", "no"):
        return value.lower()
    sys.exit(f"FATAL: {key} must be yes|no (got {value!r})")


def _validate_intake(intake: dict) -> None:
    required = ["ip_name", "bus_protocol", "ref_model_language", "uvm_version"]
    missing = [k for k in required if k not in intake]
    if missing:
        sys.exit(f"FATAL: intake.yaml missing keys: {missing}")
    bus = intake["bus_protocol"]
    if bus not in _BUILTIN_BUSES and bus != "generic":
        sys.exit(f"FATAL: supported bus_protocol values are apb, ahb, axi_lite, generic (got {bus!r})")
    if bus in _BUILTIN_BUSES:
        width_key = _WIDTH_KEY[bus]
        if width_key not in intake:
            sys.exit(f"FATAL: intake.yaml missing key for {bus}: {width_key}")
    if bus in ("axi_lite", "ahb"):
        direction = intake.get("bus_direction", "slave")
        if direction not in ("slave", "master"):
            sys.exit(f"FATAL: unsupported bus_direction: {direction!r}")
    elif bus in _BUILTIN_BUSES and "bus_direction" in intake and intake["bus_direction"] != "slave":
        sys.exit(f"FATAL: bus_direction is only meaningful for axi_lite or ahb")
    if bus == "generic":
        # Direction for generic comes from bus_handshake.yaml; intake must not set it.
        if "bus_direction" in intake:
            sys.exit("FATAL: bus_direction for generic mode lives in bus_handshake.yaml, not intake.yaml")
    if "register_semantics" in intake:
        intake["register_semantics"] = _yesno(intake["register_semantics"], "register_semantics")
    if "axi4_full_features" in intake and intake["axi4_full_features"] not in ("none", "present"):
        sys.exit("FATAL: axi4_full_features must be none|present "
                 f"(got {intake['axi4_full_features']!r})")
    sims = _simulators(intake)
    if not sims:
        sys.exit("FATAL: intake.yaml simulators must be a non-empty list "
                 f"(allowed values: {list(_SUPPORTED_SIMULATORS)})")
    for s in sims:
        if s not in _SUPPORTED_SIMULATORS:
            sys.exit(f"FATAL: unsupported simulator: {s!r} "
                     f"(allowed: {list(_SUPPORTED_SIMULATORS)})")
    # Item 10: Python-DPI is documented as schema-only — scaffold has no
    # py_dpi emitter, so accepting it would silently produce no working
    # integration. Refuse it loudly at the boundary instead.
    refm = intake.get("ref_model_language", "skip")
    if refm == "py_dpi":
        sys.exit(
            "FATAL: ref_model_language: py_dpi is not implemented in v1 "
            "(refm_dpi.md: 'scaffold will not emit it'). Use ref_model_language: "
            "c_dpi or sv, or remove the ref model with skip."
        )
    if refm not in ("skip", "sv", "c_dpi"):
        sys.exit(f"FATAL: unsupported ref_model_language: {refm!r} "
                 "(allowed: skip|sv|c_dpi)")
    # Accept legacy singular `simulator:` from older fixtures/intakes.
    if "simulators" not in intake and "simulator" in intake:
        intake["simulators"] = intake.pop("simulator")
    sims = intake.get("simulators", ["vcs"])
    if isinstance(sims, str):
        sims = [sims]
    if not isinstance(sims, list) or not sims:
        sys.exit("FATAL: simulators must be a non-empty list (allowed values: vcs, xrun)")
    bad = [s for s in sims if s not in ("vcs", "xrun")]
    if bad:
        sys.exit(f"FATAL: unsupported simulators: {bad!r} (allowed: vcs, xrun)")
    intake["simulators"] = sims
    if bus == "generic":
        # External VIP reuse is not supported in generic mode.
        for key in ("generic_vip_source", "apb_vip_source", "ahb_vip_source", "axi_lite_vip_source"):
            if intake.get(key) == "reuse_my_vip":
                sys.exit(f"FATAL: external VIP reuse is not supported in generic mode ({key} set to reuse_my_vip)")
        return
    vip_source = intake.get(f"{bus}_vip_source", "generate_fresh")
    if vip_source not in ("generate_fresh", "reuse_my_vip"):
        sys.exit(f"FATAL: unsupported {bus}_vip_source: {vip_source!r}")
    if vip_source == "reuse_my_vip" and f"{bus}_vip_path" not in intake:
        sys.exit(f"FATAL: reuse_my_vip requires intake.yaml key: {bus}_vip_path")
    reuse_level = intake.get(f"{bus}_vip_reuse_level", "import_only")
    if reuse_level not in ("import_only", "drive_with_vip"):
        sys.exit(f"FATAL: unsupported {bus}_vip_reuse_level: {reuse_level!r}")
    if vip_source != "reuse_my_vip" and f"{bus}_vip_reuse_level" in intake:
        sys.exit(f"FATAL: {bus}_vip_reuse_level is valid only with reuse_my_vip")
    # Item 9: drive_with_vip requires generated bridge + mandatory VIP
    # read/write smoke test (SKILL.md "drive_with_vip" rule). v1 scaffold
    # only ships the import_only path; refuse drive_with_vip rather than
    # silently treat it as import-only and skip the required smoke test.
    if reuse_level == "drive_with_vip":
        sys.exit(
            f"FATAL: {bus}_vip_reuse_level: drive_with_vip is not "
            "implemented in v1 (no generated bridge / VIP smoke test). "
            f"Use {bus}_vip_reuse_level: import_only, or hand-author the "
            "VIP-driven smoke test against the imported VIP."
        )
    if (
        bus in ("axi_lite", "ahb")
        and intake.get("bus_direction", "slave") == "master"
        and vip_source == "reuse_my_vip"
        and reuse_level == "import_only"
    ):
        sys.exit(
            f"FATAL: bus_direction: master + {bus}_vip_source: reuse_my_vip + "
            "import_only is not supported. The built-in responder_smoke_test "
            "relies on the generated slave responder driver to populate "
            "tb_api state; import-only reuse skips that driver but the "
            "generated test_pkg still references the fresh agent symbols. "
            f"Either use {bus}_vip_reuse_level: drive_with_vip (Phase 5 "
            "generates glue against the user VIP), or switch to "
            f"{bus}_vip_source: generate_fresh."
        )


def _resolve_input_path(raw: str, ip_root: Path) -> Path:
    expanded = raw.replace("$PROJ_DIR", str(ip_root))
    return Path(expanded).expanduser().resolve()


def _uniq_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _expand_vip_vars(raw: str, env: dict[str, str]) -> str:
    out = raw.strip().strip('"').strip("'")
    for _ in range(8):
        prev = out

        def repl_braced(match: re.Match[str]) -> str:
            return env.get(match.group(1), match.group(0))

        out = re.sub(r"\$\{([A-Za-z_]\w*)\}", repl_braced, out)
        out = re.sub(r"\$\(([A-Za-z_]\w*)\)", repl_braced, out)
        out = re.sub(r"\$([A-Za-z_]\w*)", repl_braced, out)
        if out == prev:
            break
    return out


def _vip_env_from_setup(vip_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    for setup in sorted(vip_root.rglob("*setup*.cshrc")):
        for line in setup.read_text(errors="ignore").splitlines():
            match = re.match(r"\s*setenv\s+([A-Za-z_]\w*)\s+(.+?)\s*$", line)
            if not match:
                continue
            key, value = match.groups()
            env[key] = _expand_vip_vars(value, env)
    return env


def _path_from_vip_token(raw: str, env: dict[str, str], base: Path) -> Path | None:
    token = raw.strip()
    if not token or token.startswith("#") or token.startswith("//"):
        return None
    expanded = _expand_vip_vars(token, env)
    if "$" in expanded:
        return None
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _scan_vip_filelists(vip_root: Path, env: dict[str, str]) -> tuple[list[Path], list[Path]]:
    compile_units: list[Path] = []
    incdirs: list[Path] = []

    for mk in sorted(vip_root.rglob("*.mk")):
        for inc in re.findall(r"-incdir\s+(\S+)", mk.read_text(errors="ignore")):
            path = _path_from_vip_token(inc, env, mk.parent)
            if path:
                incdirs.append(path)

    for flist in sorted(vip_root.glob("*.f")):
        for raw_line in flist.read_text(errors="ignore").splitlines():
            line = raw_line.split("//", 1)[0].strip()
            if not line:
                continue
            if line.startswith("+incdir+"):
                path = _path_from_vip_token(line[len("+incdir+"):], env, flist.parent)
                if path:
                    incdirs.append(path)
                continue
            if line.startswith("-incdir"):
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    path = _path_from_vip_token(parts[1], env, flist.parent)
                    if path:
                        incdirs.append(path)
                continue
            if line.startswith("+") or line.startswith("-"):
                continue
            path = _path_from_vip_token(line, env, flist.parent)
            if path:
                compile_units.append(path)

    return _uniq_paths(compile_units), _uniq_paths(incdirs)


def _scan_external_vip(raw_path: str, ip_root: Path, bus: str) -> dict[str, Any]:
    vip_root = _resolve_input_path(raw_path, ip_root)
    if not vip_root.is_dir():
        sys.exit(f"FATAL: {bus}_vip_path is not a directory: {vip_root}")

    sv_files = sorted(vip_root.rglob("*.sv"))
    if not sv_files:
        sys.exit(f"FATAL: no SystemVerilog files found under {bus}_vip_path: {vip_root}")

    svh_files = sorted(vip_root.rglob("*.svh"))
    scanned_files = sv_files + svh_files
    vip_env = _vip_env_from_setup(vip_root)
    filelist_units, filelist_incdirs = _scan_vip_filelists(vip_root, vip_env)

    packages: list[tuple[str, Path]] = []
    agents: list[tuple[str, Path]] = []
    transactions: list[tuple[str, Path]] = []
    configs: list[tuple[str, Path]] = []
    interfaces: list[tuple[str, Path]] = []
    for path in scanned_files:
        text = path.read_text(errors="ignore")
        for name in re.findall(r"\bpackage\s+([A-Za-z_]\w*)\s*;", text):
            packages.append((name, path))
        for name in re.findall(r"\bclass\s+([A-Za-z_]\w*)\s+extends\s+uvm_agent\b", text):
            agents.append((name, path))
        for name in re.findall(
            r"\bclass\s+([A-Za-z_]\w*)\s+extends\s+uvm_sequence_item\b", text
        ):
            transactions.append((name, path))
        for name in re.findall(r"\bclass\s+([A-Za-z_]\w*)\s+extends\s+uvm_object\b", text):
            if re.search(r"(cfg|config)", name, re.IGNORECASE):
                configs.append((name, path))
        for name in re.findall(r"\binterface\s+([A-Za-z_]\w*)\b", text):
            interfaces.append((name, path))

    test_pkg_units = {
        p
        for name, p in packages
        if re.search(r"(^|_)test(_pkg)?$", name, re.IGNORECASE)
        or re.search(r"(^|_)test(_pkg)?\.sv$", p.name, re.IGNORECASE)
    }
    package_units = sorted({p for _, p in packages if p not in test_pkg_units})
    interface_units = sorted({p for _, p in interfaces})
    if filelist_units:
        compile_units = [p for p in filelist_units if p not in test_pkg_units]
    elif package_units:
        compile_units = interface_units + [p for p in package_units if p not in interface_units]
    else:
        compile_units = [p for p in sv_files if p not in test_pkg_units]

    incdirs = sorted({vip_root, *filelist_incdirs, *(p.parent for p in scanned_files)})
    return {
        "root": vip_root,
        "packages": [{"name": n, "path": str(p)} for n, p in packages],
        "agents": [{"name": n, "path": str(p)} for n, p in agents],
        "transactions": [{"name": n, "path": str(p)} for n, p in transactions],
        "configs": [{"name": n, "path": str(p)} for n, p in configs],
        "interfaces": [{"name": n, "path": str(p)} for n, p in interfaces],
        "incdirs": incdirs,
        "compile_units": compile_units,
    }


def _bus(intake: dict) -> str:
    return intake.get("bus_protocol", "apb")


def _bus_prefix(intake: dict, handshake: dict | None) -> str:
    """The string used as filename prefix and class prefix (e.g. `apb`, `wb`)."""
    if _bus(intake) == "generic":
        assert handshake is not None
        return handshake["bus_name"]
    return _bus(intake)


def _addr_width_key(bus: str) -> str:
    return _WIDTH_KEY[bus]


def _addr_width(intake: dict, handshake: dict | None = None) -> int:
    bus = _bus(intake)
    if bus == "generic":
        assert handshake is not None
        addr = handshake.get("addr")
        if addr is None:
            return 0  # addr-less bus
        return int(addr["width"])
    return int(intake.get(_addr_width_key(bus), 12))


def _data_width(intake: dict, handshake: dict | None = None) -> int:
    if _bus(intake) == "generic":
        assert handshake is not None
        return int(handshake["data"]["width"])
    return 32


def _direction(intake: dict, handshake: dict | None = None) -> str:
    if _bus(intake) == "generic":
        assert handshake is not None
        return handshake["direction"]
    return intake.get("bus_direction", "slave")


def _clk_rst_names(bus: str, handshake: dict | None = None) -> tuple[str, str]:
    if bus == "apb":
        return "pclk", "presetn"
    if bus == "ahb":
        return "hclk", "hresetn"
    if bus == "generic":
        assert handshake is not None
        return handshake["clock"]["name"], handshake["reset"]["name"]
    return "aclk", "aresetn"


# Canonical bus role -> generated-interface signal. The generated interface is
# always canonical; only the DUT side carries the exact discovered RTL name.
_DUT_BUS_ROLES: dict[str, tuple[str, ...]] = {
    "apb": ("pclk", "presetn", "psel", "penable", "pwrite", "paddr",
            "pwdata", "prdata", "pready", "pslverr"),
    "ahb": ("hclk", "hresetn", "hsel", "haddr", "htrans", "hwrite", "hsize",
            "hburst", "hprot", "hwdata", "hrdata", "hready", "hresp"),
    "axi_lite": ("aclk", "aresetn", "awvalid", "awready", "awaddr", "awprot",
                 "wvalid", "wready", "wdata", "wstrb", "bvalid", "bready",
                 "bresp", "arvalid", "arready", "araddr", "arprot", "rvalid",
                 "rready", "rdata", "rresp"),
}


def _dut_port_name(rtl: dict, bus: str, role: str) -> str:
    """Exact RTL port name for a canonical bus role, as recorded in
    rtl_discovery.yaml. Falls back to the canonical role name."""
    section = rtl.get(f"{bus}_interface") or {}
    val = section.get(role)
    if isinstance(val, dict):
        return val.get("name", role)
    if isinstance(val, str):
        return val
    return role


def _build_dut_bus(rtl: dict, bus: str, iface: str,
                    clk_name: str, rst_name: str) -> str:
    """DUT bus port connections — `.<exact RTL port>(<canonical iface sig>)`.
    Clock/reset are wired to the top-driven signals, not the interface."""
    clk_role, rst_role = _DUT_BUS_ROLES[bus][0], _DUT_BUS_ROLES[bus][1]
    lines = []
    for role in _DUT_BUS_ROLES[bus]:
        dut = _dut_port_name(rtl, bus, role)
        if role == clk_role:
            rhs = clk_name
        elif role == rst_role:
            rhs = rst_name
        else:
            rhs = f"{iface}.{role}"
        lines.append(f".{dut} ({rhs})")
    return ",\n".join(lines)


def _reset_polarity(handshake: dict | None) -> str:
    if handshake is None:
        return "low"  # built-in buses use active-low presetn/hresetn/aresetn
    return handshake["reset"].get("polarity", "low")


def _bus_has_ral(intake: dict, handshake: dict | None = None) -> bool:
    """RAL+reg_access_test only generated when DUT is slave AND has register semantics."""
    if intake.get("register_semantics", "yes") == "no":
        return False
    if _bus(intake) == "generic":
        if handshake is None:
            return False
        if handshake.get("register_semantics", "yes") == "no":
            return False
        return handshake.get("direction", "slave") == "slave"
    return _direction(intake) == "slave"


def _vip_source(intake: dict) -> str:
    return intake.get(f"{_bus(intake)}_vip_source", "generate_fresh")


def _vip_reuse_level(intake: dict) -> str:
    return intake.get(f"{_bus(intake)}_vip_reuse_level", "import_only")


_HANDSHAKE_KINDS = ("req_ack", "valid_ready", "strobe", "custom")


def _validate_bus_handshake(hs: dict) -> None:
    required = ["bus_name", "direction", "clock", "reset", "data", "handshake", "register_semantics"]
    missing = [k for k in required if k not in hs]
    if missing:
        sys.exit(f"FATAL: bus_handshake.yaml missing keys: {missing}")
    name = hs["bus_name"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        sys.exit(f"FATAL: bus_handshake.yaml.bus_name must be lowercase identifier (got {name!r})")
    if hs["direction"] not in ("slave", "master"):
        sys.exit(f"FATAL: bus_handshake.yaml.direction must be slave|master (got {hs['direction']!r})")
    hs["register_semantics"] = _yesno(hs["register_semantics"], "bus_handshake.yaml.register_semantics")
    clk = hs["clock"]
    if not isinstance(clk, dict) or "name" not in clk:
        sys.exit("FATAL: bus_handshake.yaml.clock must be a mapping with at least `name`")
    rst = hs["reset"]
    if not isinstance(rst, dict) or "name" not in rst or rst.get("polarity") not in ("low", "high"):
        sys.exit("FATAL: bus_handshake.yaml.reset requires `name` and `polarity: low|high`")
    data = hs["data"]
    if not isinstance(data, dict) or "width" not in data:
        sys.exit("FATAL: bus_handshake.yaml.data must include `width`")
    handshake = hs["handshake"]
    if not isinstance(handshake, dict) or handshake.get("kind") not in _HANDSHAKE_KINDS:
        sys.exit(f"FATAL: bus_handshake.yaml.handshake.kind must be one of {_HANDSHAKE_KINDS}")
    addr = hs.get("addr")
    if addr is not None and (not isinstance(addr, dict) or "width" not in addr or "port" not in addr):
        sys.exit("FATAL: bus_handshake.yaml.addr must be null or a mapping with `port` and `width`")
    if addr is None and hs["register_semantics"] == "yes":
        sys.exit("FATAL: bus_handshake.yaml: register_semantics: yes is incompatible with addr: null")


def _load_handshake(audit: Path) -> dict:
    path = audit / "bus_handshake.yaml"
    if not path.exists():
        sys.exit(f"FATAL: bus_protocol: generic requires {path}")
    with path.open() as f:
        hs = yaml.safe_load(f) or {}
    _validate_bus_handshake(hs)
    return hs


def _pick_sanity_target(regs: list[dict]) -> tuple[int, int]:
    """Find the first RO register with non-zero reset for positive sanity."""
    for r in regs:
        if r.get("access") in ("RO", "RW") and int(r["reset"], 16) if isinstance(r["reset"], str) else r["reset"]:
            reset = r["reset"] if isinstance(r["reset"], int) else int(r["reset"], 16)
            if reset != 0:
                off = r["offset"] if isinstance(r["offset"], int) else int(r["offset"], 16)
                return off, reset
    # fallback: register 0 with expected 0 (still proves bus alive)
    if regs:
        r0 = regs[0]
        off = r0["offset"] if isinstance(r0["offset"], int) else int(r0["offset"], 16)
        return off, 0
    return 0, 0


# ---------------------------------------------------------------------------
# File template emitters
# ---------------------------------------------------------------------------


def emit_setup_sh(sims: list[str] | None = None) -> str:
    sims = sims or ["vcs"]
    # UVM_HOME fallback chain: respect any existing value first, otherwise
    # derive from whichever simulator vendor variable is set. The shell
    # short-circuits, so a present VCS_HOME wins over QUESTA_HOME, etc.
    fallbacks: list[str] = []
    if "vcs" in sims:
        fallbacks.append('if [ -z "$UVM_HOME" ] && [ -n "$VCS_HOME" ]; then\n'
                         '    export UVM_HOME="$VCS_HOME/etc/uvm-1.2"\n'
                         'fi')
    if "questa" in sims:
        # Questa ships UVM under <install>/verilog_src/uvm-1.2 (modern installs)
        # or has it preloaded as -L mtiUvm. Set UVM_HOME only if discoverable.
        fallbacks.append('if [ -z "$UVM_HOME" ] && [ -n "$QUESTA_HOME" ] && '
                         '[ -d "$QUESTA_HOME/verilog_src/uvm-1.2" ]; then\n'
                         '    export UVM_HOME="$QUESTA_HOME/verilog_src/uvm-1.2"\n'
                         'fi')
    fallback_block = "\n".join(fallbacks)
    return textwrap.dedent("""\
        #!/bin/bash
        # gen-tb generated. Walks up to find .prj_top, exports PROJ_DIR / WORK_DIR.
        prj_top=".prj_top"
        cur_dir="$PWD"
        pushd . > /dev/null
        while [[ "$(pwd)" != "/" ]]; do
            if [[ -e $prj_top ]]; then
                export PROJ_DIR="$(pwd)"
                echo "PROJ_DIR=$PROJ_DIR"
                popd > /dev/null
                break
            fi
            cd ..
        done
        cd "$cur_dir"
        export WORK_DIR=$PROJ_DIR/work
        mkdir -p "$WORK_DIR"
        """) + fallback_block + textwrap.dedent("""
        echo "WORK_DIR=$WORK_DIR"
        echo "UVM_HOME=$UVM_HOME"
        echo "setup done"
        """)


def emit_check_env_sh(simulators: list[str] | None = None) -> str:
    sims = simulators or ["vcs"]
    head = textwrap.dedent("""\
        #!/bin/bash
        # gen-tb generated. Validates simulator env before compile.
        # Picks the SIM the user is about to invoke (default: first generated).
        err=0
        SIM="${SIM:-%s}"
        [ -n "$PROJ_DIR" ] || { echo "FATAL: PROJ_DIR unset (source script/setup.sh first)"; err=1; }
        """ % sims[0])
    case_lines = ['case "$SIM" in']
    if "vcs" in sims:
        case_lines += [
            "    vcs)",
            '        command -v vcs >/dev/null || { echo "FATAL: vcs not in PATH"; err=1; }',
            '        [ -n "$VCS_HOME" ] || { echo "FATAL: VCS_HOME unset"; err=1; }',
            '        [ -n "$UVM_HOME" ] || { echo "FATAL: UVM_HOME unset (source script/setup.sh first)"; err=1; }',
            '        [ -e "$UVM_HOME/src/uvm.sv" ] || { echo "FATAL: UVM source not found at $UVM_HOME/src/uvm.sv"; err=1; }',
            "        ;;",
        ]
    if "questa" in sims:
        case_lines += [
            "    questa)",
            '        # Questa: only vlog/vsim must resolve; UVM may live in -L mtiUvm rather than $UVM_HOME.',
            '        command -v vlog >/dev/null || { echo "FATAL: vlog not in PATH"; err=1; }',
            '        command -v vsim >/dev/null || { echo "FATAL: vsim not in PATH"; err=1; }',
            "        ;;",
        ]
    if "xrun" in sims:
        case_lines += [
            "    xrun)",
            '        command -v xrun >/dev/null || { echo "FATAL: xrun not in PATH (Cadence Xcelium)"; err=1; }',
            '        [ -n "$XLM_HOME$CDS_INST_DIR" ] || { echo "WARN: neither XLM_HOME nor CDS_INST_DIR set — xrun -uvmhome CDNS-1.2 may still work via xrun defaults"; }',
            "        ;;",
        ]
    case_lines += [
        '    *) echo "FATAL: unsupported SIM=$SIM (expected: %s)"; err=1 ;;' % "|".join(sims),
        "esac",
        "exit $err",
        "",
    ]
    return head + "\n".join(case_lines)


def emit_design_f(rtl: dict) -> str:
    """Generate $PROJ_DIR-prefixed filelist from rtl_discovery.yaml."""
    lines = [f"+incdir+{rtl['rtl_dir']}"]
    for f in rtl["files"]:
        lines.append(f["path"])
    return "\n".join(lines) + "\n"


def emit_makefile(intake: dict, has_dpi: bool, dpi: dict | None) -> str:
    ip = intake["ip_name"]
    uvm_ver = intake.get("uvm_version", "1.2")

    # `$PROJ_DIR` in our yaml is shell/SV-style; Make needs `$(PROJ_DIR)`.
    _make = lambda s: s.replace("$PROJ_DIR", "$(PROJ_DIR)")

    dpi_section = ""
    extra_cmp = ""
    if has_dpi and dpi:
        c_srcs = " \\\n           ".join(_make(p) for p in dpi["c_sources"])
        # auto-derive -I dirs from c_headers
        inc_dirs = sorted({str(Path(h).parent) for h in dpi["c_headers"]})
        inc_dirs += dpi.get("include_dirs", [])
        inc_flags = " ".join(f"-I{_make(d)}" for d in inc_dirs)
        cflags = " ".join(dpi.get("cflags", ["-O2", "-Wall"]))
        dpi_section = textwrap.dedent(f"""
            # === BEGIN gen-tb DPI section (auto-generated from intake.yaml) ===
            C_SRCS    = {c_srcs}
            C_INC     = -CFLAGS "{inc_flags} {cflags}"
            # === END gen-tb DPI section ===
            """)
        extra_cmp = "            $(C_INC) $(C_SRCS) \\\n"

    # Use raw template (no dedent) — Makefiles require literal TABs in
    # recipe lines and textwrap.dedent + embedded multiline {dpi_section}
    # don't compose cleanly.
    return (
        f"# gen-tb generated makefile for {ip}\n"
        f"ifndef PROJ_DIR\n"
        f"$(error PROJ_DIR not set — source script/setup.sh first)\n"
        f"endif\n\n"
        f"VCS       ?= vcs\n"
        f"FLIST     ?= $(PROJ_DIR)/script/design.f\n"
        f"TBLIST    ?= $(PROJ_DIR)/script/tb.f\n"
        f"SV_CASE   ?= {ip}_sanity_test\n"
        f"seed      ?= $(shell date +1%N)\n"
        f"cov       ?= 0\n"
        f"UVM_VER   ?= {uvm_ver}\n\n"
        f"SIM_DIR   = $(PROJ_DIR)/work/work_$(SV_CASE)_\n"
        f"COMP_LOG  = $(SIM_DIR)/comp.log\n"
        f"SIM_LOG   = $(SIM_DIR)/run.log\n"
        f"SIMV      = $(SIM_DIR)/simv\n"
        f"{dpi_section}\n"
        f"CMP_OPTS  = -full64 -sverilog -kdb -timescale=1ns/1ps \\\n"
        f"            -ntb_opts uvm-$(UVM_VER) \\\n"
        f"            +define+UVM_OBJECT_MUST_HAVE_CONSTRUCTOR \\\n"
        f"            -l $(COMP_LOG) \\\n"
        f"            -f $(FLIST) -f $(TBLIST) \\\n"
        f"{extra_cmp}            -debug_access+all \\\n"
        f"            -o $(SIMV)\n\n"
        f"SIM_OPTS  = -l $(SIM_LOG) +ntb_random_seed=$(seed) \\\n"
        f"            +UVM_TESTNAME=$(SV_CASE) +UVM_VERBOSITY=UVM_LOW\n\n"
        f"ifeq ($(cov),1)\n"
        f"CMP_OPTS += -cm tgl+line+cond+fsm+branch -cm_name $(SV_CASE)_$(seed) -cm_dir $(SIM_DIR)/cov\n"
        f"SIM_OPTS += -cm tgl+line+cond+fsm+branch -cm_dir $(SIM_DIR)/cov\n"
        f"endif\n\n"
        f".PHONY: comp run all clean wave merge help\n"
        f"all: comp run\n"
        f"comp:\n"
        f"\t@mkdir -p $(SIM_DIR)\n"
        f"\tcd $(SIM_DIR) && $(VCS) $(CMP_OPTS)\n"
        f"run:\n"
        f"\t@mkdir -p $(SIM_DIR)\n"
        f"\tcd $(SIM_DIR) && $(SIMV) $(SIM_OPTS)\n"
        f"\t@echo \"Done.  seed=$(seed)  log=$(SIM_LOG)\"\n"
        f"clean:\n"
        f"\trm -rf $(PROJ_DIR)/work/work_*\n"
        f"wave:\n"
        f"\tverdi -sv -f $(FLIST) -f $(TBLIST) &\n"
    )


def emit_makefile_questa(intake: dict, has_dpi: bool, dpi: dict | None) -> str:
    """Questa (vlog/vsim) flow mirroring the VCS makefile contract.

    NOTE: gen-tb has no Questa license/install to validate this flow against —
    the recipe is modeled on the uart16550 reference's `makefile_xrun` and the
    Mentor "UVM with Questa" cookbook. Verify locally before relying on it in
    CI. Targets, variable names (SV_CASE, seed, cov, UVM_VER, FLIST, TBLIST,
    SIM_DIR, COMP_LOG, SIM_LOG) match references/makefile_contract.md so
    regression scripts stay portable across SIMs.
    """
    ip = intake["ip_name"]
    uvm_ver = intake.get("uvm_version", "1.2")

    _make = lambda s: s.replace("$PROJ_DIR", "$(PROJ_DIR)")

    dpi_section = ""
    dpi_prereq = ""
    dpi_sim_extra = ""
    dpi_comp_extra = ""
    if has_dpi and dpi:
        c_srcs = " \\\n           ".join(_make(p) for p in dpi["c_sources"])
        inc_dirs = sorted({str(Path(h).parent) for h in dpi["c_headers"]})
        inc_dirs += dpi.get("include_dirs", [])
        inc_flags = " ".join(f"-I{_make(d)}" for d in inc_dirs)
        cflags = " ".join(dpi.get("cflags", ["-O2", "-Wall"]))
        # Questa loads DPI via a precompiled shared object (-sv_lib). VCS
        # uses inline -CFLAGS instead, so we emit a separate gcc rule here.
        # gen-tb has not validated this command line against a real Questa
        # install — please confirm `-I$$QUESTA_HOME/include` and any local
        # gcc/glibc constraints in your environment before relying on this.
        dpi_section = textwrap.dedent(f"""
            # === BEGIN gen-tb DPI section (Questa, auto-generated) ===
            # gcc compiles the C ref-model into a shared lib; vsim loads it via -sv_lib.
            # Static-only: not validated against a live Questa install — verify locally.
            C_SRCS    = {c_srcs}
            C_INC     = {inc_flags}
            C_CFLAGS  = {cflags}
            DPI_LIB   = $(SIM_DIR)/dpi_ref
            # === END gen-tb DPI section ===
            """)
        dpi_prereq = "$(DPI_LIB).so "
        dpi_sim_extra = " -sv_lib $(DPI_LIB)"
        dpi_comp_extra = ""

    dpi_rule = ""
    if has_dpi and dpi:
        dpi_rule = (
            "$(DPI_LIB).so: $(C_SRCS)\n"
            "\t@mkdir -p $(SIM_DIR)\n"
            "\tgcc -shared -fPIC -I$${QUESTA_HOME:-/opt/questa}/include "
            "$(C_INC) $(C_CFLAGS) $(C_SRCS) -o $@\n"
        )

    return (
        f"# gen-tb generated Questa (vlog/vsim) makefile for {ip}.\n"
        f"# Invoke with: make -f makefile_questa all SV_CASE=<test>\n"
        f"#\n"
        f"# NOTE (static-only): gen-tb produced this flow without a Questa\n"
        f"# install to validate against. The recipe follows the Mentor UVM\n"
        f"# cookbook and the uart16550 makefile_xrun reference. Confirm\n"
        f"# locally — in particular UVM library mapping (-L mtiUvm vs.\n"
        f"# +incdir+$$UVM_HOME/src), DPI -sv_lib path conventions, and\n"
        f"# coverage merge tooling — before relying on this in CI.\n"
        f"ifndef PROJ_DIR\n"
        f"$(error PROJ_DIR not set — source script/setup.sh first)\n"
        f"endif\n\n"
        f"VLIB      ?= vlib\n"
        f"VMAP      ?= vmap\n"
        f"VLOG      ?= vlog\n"
        f"VSIM      ?= vsim\n"
        f"FLIST     ?= $(PROJ_DIR)/script/design.f\n"
        f"TBLIST    ?= $(PROJ_DIR)/script/tb.f\n"
        f"SV_CASE   ?= {ip}_sanity_test\n"
        f"seed      ?= $(shell date +1%N)\n"
        f"cov       ?= 0\n"
        f"UVM_VER   ?= {uvm_ver}\n"
        f"TB_TOP    ?= {ip}_tb_top\n\n"
        f"SIM_DIR   = $(PROJ_DIR)/work/work_$(SV_CASE)_\n"
        f"COMP_LOG  = $(SIM_DIR)/comp.log\n"
        f"SIM_LOG   = $(SIM_DIR)/run.log\n"
        f"WORK_LIB  = $(SIM_DIR)/work\n"
        f"{dpi_section}\n"
        f"# Use Questa's preloaded UVM libraries (-L mtiUvm[Ieee]). To compile\n"
        f"# UVM from $$UVM_HOME source instead, drop these and add\n"
        f"# `+incdir+$$UVM_HOME/src $$UVM_HOME/src/uvm_pkg.sv` to CMP_OPTS.\n"
        f"QUESTA_UVM = -L mtiUvm -L mtiUvmIeee\n\n"
        f"CMP_OPTS  = -sv -mfcu +acc=rmb -timescale 1ns/1ps -work $(WORK_LIB) \\\n"
        f"            +define+UVM_OBJECT_MUST_HAVE_CONSTRUCTOR +define+QUESTA \\\n"
        f"            -l $(COMP_LOG) \\\n"
        f"            -f $(FLIST) -f $(TBLIST){dpi_comp_extra}\n\n"
        f"SIM_OPTS  = -c -onfinish exit -do \"run -all; quit -f\" \\\n"
        f"            -sv_seed $(seed) -l $(SIM_LOG) \\\n"
        f"            -lib $(WORK_LIB) $(QUESTA_UVM){dpi_sim_extra} \\\n"
        f"            +UVM_TESTNAME=$(SV_CASE) +UVM_VERBOSITY=UVM_LOW \\\n"
        f"            $(TB_TOP)\n\n"
        f"ifeq ($(cov),1)\n"
        f"CMP_OPTS += +cover=bcestf\n"
        f"# Questa coverage is collected per-run via -coverage; the .ucdb lands\n"
        f"# in $(SIM_DIR)/cov.ucdb so `make merge` can vcover them later.\n"
        f"SIM_OPTS := $(subst -do \"run -all; quit -f\",-coverage -do \"coverage save -onexit $(SIM_DIR)/cov.ucdb; run -all; quit -f\",$(SIM_OPTS))\n"
        f"endif\n\n"
        f".PHONY: comp run all clean wave merge help\n"
        f"all: comp run\n"
        f"comp: {dpi_prereq}\n"
        f"\t@mkdir -p $(SIM_DIR)\n"
        f"\t$(VLIB) $(WORK_LIB)\n"
        f"\tcd $(SIM_DIR) && $(VLOG) $(CMP_OPTS)\n"
        f"{dpi_rule}"
        f"run:\n"
        f"\t@mkdir -p $(SIM_DIR)\n"
        f"\tcd $(SIM_DIR) && $(VSIM) $(SIM_OPTS)\n"
        f"\t@echo \"Done.  seed=$(seed)  log=$(SIM_LOG)\"\n"
        f"clean:\n"
        f"\trm -rf $(PROJ_DIR)/work/work_*\n"
        f"wave:\n"
        f"\t$(VSIM) -gui -lib $(WORK_LIB) $(QUESTA_UVM) "
        f"+UVM_TESTNAME=$(SV_CASE) $(TB_TOP) &\n"
        f"merge:\n"
        f"\t@mkdir -p $(PROJ_DIR)/work/cov_report\n"
        f"\tvcover merge -out $(PROJ_DIR)/work/cov_report/merged.ucdb "
        f"$(PROJ_DIR)/work/work_*/cov.ucdb\n"
        f"\tvcover report -html -htmldir $(PROJ_DIR)/work/cov_report "
        f"$(PROJ_DIR)/work/cov_report/merged.ucdb\n"
        f"help:\n"
        f"\t@echo 'gen-tb Questa makefile — see references/makefile_contract.md'\n"
        f"\t@echo 'targets: comp run all clean wave merge'\n"
        f"\t@echo 'vars:    SV_CASE seed cov UVM_VER VLIB VLOG VSIM TB_TOP FLIST TBLIST'\n"
    )


def emit_makefile_xrun(intake: dict, has_dpi: bool, dpi: dict | None) -> str:
    """xrun (Cadence Xcelium) variant.

    Mirrors the public API in references/makefile_contract.md (SV_CASE,
    seed, cov, UVM_VER, FLIST, TBLIST, dpi section). Invoke as:
        make -f makefile_xrun all SV_CASE=<case>
    """
    ip = intake["ip_name"]
    uvm_ver = intake.get("uvm_version", "1.2")

    _make = lambda s: s.replace("$PROJ_DIR", "$(PROJ_DIR)")

    dpi_section = ""
    extra_cmp = ""
    if has_dpi and dpi:
        c_srcs = " \\\n           ".join(_make(p) for p in dpi["c_sources"])
        inc_dirs = sorted({str(Path(h).parent) for h in dpi["c_headers"]})
        inc_dirs += dpi.get("include_dirs", [])
        inc_flags = " ".join(f"-I{_make(d)}" for d in inc_dirs)
        cflags = " ".join(dpi.get("cflags", ["-O2", "-Wall"]))
        dpi_section = textwrap.dedent(f"""
            # === BEGIN gen-tb DPI section (auto-generated from intake.yaml) ===
            C_SRCS    = {c_srcs}
            C_INC     = -cflags "{inc_flags} {cflags}"
            # === END gen-tb DPI section ===
            """)
        extra_cmp = "            $(C_INC) $(C_SRCS) \\\n"

    return (
        f"# gen-tb generated makefile_xrun for {ip}\n"
        f"# Invoke with: make -f makefile_xrun <target> [SV_CASE=...]\n"
        f"ifndef PROJ_DIR\n"
        f"$(error PROJ_DIR not set — source script/setup.sh first)\n"
        f"endif\n\n"
        f"XRUN      ?= xrun\n"
        f"FLIST     ?= $(PROJ_DIR)/script/design.f\n"
        f"TBLIST    ?= $(PROJ_DIR)/script/tb.f\n"
        f"SV_CASE   ?= {ip}_sanity_test\n"
        f"seed      ?= $(shell date +1%N)\n"
        f"cov       ?= 0\n"
        f"UVM_VER   ?= {uvm_ver}\n\n"
        f"SIM_DIR   = $(PROJ_DIR)/work/work_$(SV_CASE)_\n"
        f"COMP_LOG  = $(SIM_DIR)/comp.log\n"
        f"SIM_LOG   = $(SIM_DIR)/run.log\n"
        f"XMLIBDIR  = $(SIM_DIR)/xcelium.d\n"
        f"{dpi_section}\n"
        f"# xrun runs elab + sim in one invocation; -elaborate stops after elab.\n"
        f"CMP_OPTS  = -64bit -sv -uvmhome CDNS-$(UVM_VER) \\\n"
        f"            -timescale 1ns/1ps -access +rwc \\\n"
        f"            -xmlibdirname xcelium.d \\\n"
        f"            +define+UVM_OBJECT_MUST_HAVE_CONSTRUCTOR \\\n"
        f"            -f $(FLIST) -f $(TBLIST) \\\n"
        f"{extra_cmp}            -l $(COMP_LOG)\n\n"
        f"SIM_OPTS  = -64bit -R -xmlibdirname xcelium.d \\\n"
        f"            -svseed $(seed) +ntb_random_seed=$(seed) \\\n"
        f"            +UVM_TESTNAME=$(SV_CASE) +UVM_VERBOSITY=UVM_LOW \\\n"
        f"            -l $(SIM_LOG)\n\n"
        f"ifeq ($(cov),1)\n"
        f"CMP_OPTS += -coverage all -covoverwrite -covworkdir $(SIM_DIR)/cov_work \\\n"
        f"            -covtest $(SV_CASE)_$(seed)\n"
        f"SIM_OPTS += -covoverwrite -covworkdir $(SIM_DIR)/cov_work \\\n"
        f"            -covtest $(SV_CASE)_$(seed)\n"
        f"endif\n\n"
        f".PHONY: comp run all clean wave merge help\n"
        f"all: comp run\n"
        f"comp:\n"
        f"\t@mkdir -p $(SIM_DIR)\n"
        f"\tcd $(SIM_DIR) && $(XRUN) -elaborate $(CMP_OPTS)\n"
        f"run:\n"
        f"\t@mkdir -p $(SIM_DIR)\n"
        f"\tcd $(SIM_DIR) && $(XRUN) $(SIM_OPTS)\n"
        f"\t@echo \"Done.  seed=$(seed)  log=$(SIM_LOG)\"\n"
        f"clean:\n"
        f"\trm -rf $(PROJ_DIR)/work/work_*\n"
        f"wave:\n"
        f"\tverdi -sv -f $(FLIST) -f $(TBLIST) &\n"
        f"merge:\n"
        f"\tcd $(PROJ_DIR)/work && imc -execcmd \"merge work_*/cov_work/scope/* -overwrite -out merged_results\"\n"
        f"help:\n"
        f"\t@echo 'make -f makefile_xrun all SV_CASE=<case> [seed=N] [cov=1]'\n"
        f"\t@echo 'make -f makefile_xrun comp|run|clean|wave|merge'\n"
    )


def emit_tb_f(ip: str, has_dpi: bool, bus: str, vip_source: str, has_ral: bool = True, handshake: dict | None = None) -> str:
    tb_root = "$PROJ_DIR/tb"
    test_root = "$PROJ_DIR/test"
    top_root = "$PROJ_DIR/top"
    if bus == "generic":
        assert handshake is not None
        prefix = handshake["bus_name"]
        if_name = f"{prefix}_if.sv"
        agt_dir = f"{prefix}_agt_top"
        agent_file = f"{prefix}_agt_pkg.sv"
        adapter_file = f"{ip}_{prefix}_adapter.sv"
    else:
        if_name = {"apb": "apb_if.sv", "ahb": "ahb_if.sv", "axi_lite": "axi_lite_if.sv"}[bus]
        agt_dir = f"{bus}_agt_top"
        agent_file = f"{bus}_agent.sv"
        adapter_file = f"{ip}_{bus}_adapter.sv"
    lines = [
        f"+incdir+{tb_root}",
        f"+incdir+{tb_root}/tb_api",
        f"+incdir+{test_root}",
        f"+incdir+{top_root}",
        f"{tb_root}/{if_name}",
        f"{tb_root}/tb_api/tb_api_pkg.sv",
    ]
    if has_ral:
        lines.insert(2, f"+incdir+{tb_root}/ral")
        lines.append(f"{tb_root}/ral/{ip}_reg_block.sv")
    if vip_source == "generate_fresh":
        lines.insert(1, f"+incdir+{tb_root}/{agt_dir}")
        lines.append(f"{tb_root}/{agt_dir}/{agent_file}")
        if has_ral:
            lines.append(f"{tb_root}/ral/{adapter_file}")
    else:
        lines.append(f"-f {tb_root}/external_vip.f")
    if has_dpi:
        lines.append(f"+incdir+{tb_root}/dpi")
        lines.append(f"{tb_root}/dpi/{ip}_ref_pkg.sv")
    lines.append(f"{test_root}/{ip}_pkg.sv")
    lines.append(f"{top_root}/{ip}_tb_top.sv")
    return "\n".join(lines) + "\n"


def emit_apb_if() -> str:
    return textwrap.dedent("""\
        `ifndef APB_IF_SV
        `define APB_IF_SV
        interface apb_if #(int ADDR_W = 12, int DATA_W = 32) (
            input logic pclk,
            input logic presetn
        );
            logic              psel;
            logic              penable;
            logic              pwrite;
            logic [ADDR_W-1:0] paddr;
            logic [DATA_W-1:0] pwdata;
            logic [DATA_W-1:0] prdata;
            logic              pready;
            logic              pslverr;
        endinterface
        `endif
        """)


def emit_ahb_if() -> str:
    return textwrap.dedent("""\
        `ifndef AHB_IF_SV
        `define AHB_IF_SV
        interface ahb_if #(int ADDR_W = 12, int DATA_W = 32) (
            input logic hclk,
            input logic hresetn
        );
            logic              hsel;
            logic [ADDR_W-1:0] haddr;
            logic [1:0]        htrans;
            logic              hwrite;
            logic [2:0]        hsize;
            logic [2:0]        hburst;
            logic [3:0]        hprot;
            logic [DATA_W-1:0] hwdata;
            logic [DATA_W-1:0] hrdata;
            logic              hready;
            logic              hresp;
        endinterface
        `endif
        """)


def emit_axi_lite_if(axi_full_signature: bool = False, id_w: int = 1) -> str:
    """Emit the AXI4-Lite interface. When `axi_full_signature` is true (the
    user told us the DUT exposes full-AXI signals but only uses single-beat
    transfers — see SKILL.md Phase 2 mandatory question), include the extra
    burst/ID ports that the DUT will drive, so tb_top can wire them cleanly.
    The monitor adds a runtime assertion that AWLEN == 0 && ARLEN == 0."""
    extra = ""
    if axi_full_signature:
        extra_lines = [
            "// Full-AXI signature ports — DUT exposes these; TB master ties them",
            "// to single-beat values. The concurrent assertions below trap any",
            "// burst the DUT issues despite single-beat-only configuration.",
            "logic [7:0]            awlen;",
            "logic [2:0]            awsize;",
            "logic [1:0]            awburst;",
            f"logic [{id_w-1}:0]     awid;",
            "logic [7:0]            arlen;",
            "logic [2:0]            arsize;",
            "logic [1:0]            arburst;",
            f"logic [{id_w-1}:0]     arid;",
            f"logic [{id_w-1}:0]     bid;",
            f"logic [{id_w-1}:0]     rid;",
            "logic                  rlast;",
            "logic                  wlast;",
            "",
            "// AXI4-full degraded-mode guard (axi_full_signature: true).",
            "// Fail loud at sim time if the DUT issues a real burst.",
            "property p_awlen_zero;",
            "    @(posedge aclk) disable iff (!aresetn)",
            "        (awvalid && awready) |-> (awlen == 8'h00);",
            "endproperty",
            "property p_arlen_zero;",
            "    @(posedge aclk) disable iff (!aresetn)",
            "        (arvalid && arready) |-> (arlen == 8'h00);",
            "endproperty",
            'a_awlen_zero: assert property (p_awlen_zero)',
            '    else $fatal(1, "AXI_FULL_DEGRADED: DUT issued AWLEN > 0 but TB is AXI4-Lite single-beat mode");',
            'a_arlen_zero: assert property (p_arlen_zero)',
            '    else $fatal(1, "AXI_FULL_DEGRADED: DUT issued ARLEN > 0 but TB is AXI4-Lite single-beat mode");',
        ]
        extra = "\n" + "\n".join("            " + ln if ln else "" for ln in extra_lines)
    return textwrap.dedent(f"""\
        `ifndef AXI_LITE_IF_SV
        `define AXI_LITE_IF_SV
        interface axi_lite_if #(int ADDR_W = 12, int DATA_W = 32) (
            input logic aclk,
            input logic aresetn
        );
            localparam int STRB_W = DATA_W / 8;
            logic              awvalid;
            logic              awready;
            logic [ADDR_W-1:0] awaddr;
            logic [2:0]        awprot;
            logic              wvalid;
            logic              wready;
            logic [DATA_W-1:0] wdata;
            logic [STRB_W-1:0] wstrb;
            logic              bvalid;
            logic              bready;
            logic [1:0]        bresp;
            logic              arvalid;
            logic              arready;
            logic [ADDR_W-1:0] araddr;
            logic [2:0]        arprot;
            logic              rvalid;
            logic              rready;
            logic [DATA_W-1:0] rdata;
            logic [1:0]        rresp;{extra}
        endinterface
        `endif
        """)


def emit_tb_top(intake: dict, rtl: dict, handshake: dict | None = None,
                 axi_full_signature: bool = False) -> str:
    ip = intake["ip_name"]
    bus = _bus(intake)
    top = rtl["top_module"]["name"]
    addr_w = _addr_width(intake, handshake)
    clk_name, rst_name = _clk_rst_names(bus, handshake)
    rst_cycles = intake.get("reset", {}).get(f"{rst_name}_duration_cycles", 16)
    half_period = intake.get("clock", {}).get(f"{clk_name}_period_ns", 10) // 2
    if bus == "generic" and handshake is not None:
        # Honor freq/polarity from handshake if intake didn't override them.
        freq = handshake.get("clock", {}).get("freq_mhz")
        if freq and f"{clk_name}_period_ns" not in intake.get("clock", {}):
            half_period = max(1, int(1000 / freq) // 2)
        if handshake.get("reset", {}).get("polarity") == "high":
            # tb_top reset polarity follows handshake; default block below assumes low-active.
            pass  # handled in template below

    # Collect non-bus pads from rtl_discovery
    pads = rtl.get("other_pads", []) or []
    pad_decls = []
    pad_connects = []
    for p in pads:
        name = p["name"]
        if p["dir"] == "in":
            # idle-high default for serial/modem RX-like inputs
            default = "1'b1"
            pad_decls.append(f"    logic {name} = {default};")
            pad_connects.append(f"        .{name}({name})")
        else:
            pad_decls.append(f"    wire  {name};")
            pad_connects.append(f"        .{name}({name})")

    pad_decls_str = "\n".join(pad_decls) if pad_decls else "    // (no non-bus pads)"
    pad_connects_str = (",\n" + ",\n".join(pad_connects)) if pad_connects else ""

    if bus == "apb":
        bus_inst = f"apb_if #(.ADDR_W({addr_w}), .DATA_W(32)) apb (.pclk(pclk), .presetn(presetn));"
        dut_bus = _build_dut_bus(rtl, "apb", "apb", clk_name, rst_name)
        config = textwrap.dedent("""\
            uvm_config_db#(tb_api::vif_t)::set(null, "*", "apb_vif", apb);
            tb_api::set_vif(apb);""").rstrip()
    elif bus == "ahb":
        bus_inst = f"ahb_if #(.ADDR_W({addr_w}), .DATA_W(32)) ahb (.hclk(hclk), .hresetn(hresetn));"
        dut_bus = _build_dut_bus(rtl, "ahb", "ahb", clk_name, rst_name)
        config = textwrap.dedent("""\
            uvm_config_db#(tb_api::vif_t)::set(null, "*", "ahb_vif", ahb);
            tb_api::set_vif(ahb);""").rstrip()
    elif bus == "generic":
        assert handshake is not None
        prefix = handshake["bus_name"]
        bus_inst = f"{prefix}_if {prefix}_bus (.{clk_name}({clk_name}), .{rst_name}({rst_name}));"
        # Connect handshake-known ports to interface signals. Other DUT
        # ports are routed via `other_pads`. The sub-agent may augment
        # this wiring through a Phase 5 fix-up if needed.
        connects = [f".{clk_name}({clk_name})", f".{rst_name}({rst_name})"]
        addr_cfg = handshake.get("addr")
        if addr_cfg:
            connects.append(f".{addr_cfg['port']}({prefix}_bus.{addr_cfg['port']})")
        data_cfg = handshake["data"]
        connects.append(f".{data_cfg['write_port']}({prefix}_bus.{data_cfg['write_port']})")
        if data_cfg.get("read_port") and data_cfg["read_port"] != data_cfg["write_port"]:
            connects.append(f".{data_cfg['read_port']}({prefix}_bus.{data_cfg['read_port']})")
        for name, _ in _generic_extra_ports(handshake):
            connects.append(f".{name}({prefix}_bus.{name})")
        dut_bus = ",\n            ".join(connects)
        config = textwrap.dedent(f"""\
            uvm_config_db#(tb_api::vif_t)::set(null, "*", "{prefix}_vif", {prefix}_bus);
            tb_api::set_vif({prefix}_bus);""").rstrip()
    else:  # axi_lite
        bus_inst = f"axi_lite_if #(.ADDR_W({addr_w}), .DATA_W(32)) axi (.aclk(aclk), .aresetn(aresetn));"
        axi_full_extra = ""
        if axi_full_signature:
            # Only wire sidebands actually present on the DUT. The detector
            # can fire on a single full-AXI signal; assuming all 12 sidebands
            # exist would reference nonexistent ports on partial-shape DUTs.
            # Discovery records {role: exact_RTL_name} so DUTs that suffix
            # sidebands (`awlen_i`) get `.awlen_i(axi.awlen)` rather than a
            # `.awlen(...)` that does not exist on the module.
            raw = rtl.get("axi_full_signals")
            if not raw:
                roles = ["awlen", "awsize", "awburst", "awid",
                         "arlen", "arsize", "arburst", "arid",
                         "bid", "rid", "wlast", "rlast"]
                present = {r: r for r in roles}
            elif isinstance(raw, dict):
                present = raw
            else:
                # Legacy list-of-roles form: role and port name coincide.
                present = {r: r for r in raw}
            extras = [f".{name} (axi.{role})" for role, name in present.items()]
            axi_full_extra = ",\n            " + ",\n            ".join(extras)
        dut_bus = _build_dut_bus(rtl, "axi_lite", "axi", clk_name, rst_name) \
            + axi_full_extra
        config = textwrap.dedent("""\
            uvm_config_db#(tb_api::vif_t)::set(null, "*", "axi_lite_vif", axi);
            tb_api::set_vif(axi);""").rstrip()

    dut_bus_block = textwrap.indent(dut_bus, "        ")
    config_block = textwrap.indent(config, "        ")
    rst_polarity = _reset_polarity(handshake) if bus == "generic" else "low"
    rst_asserted = "1'b1" if rst_polarity == "high" else "1'b0"
    rst_released = "1'b0" if rst_polarity == "high" else "1'b1"
    return f"""// gen-tb generated tb top for {ip}.
// Interface receives clk/rst via input ports (no dual-drive).
// Non-bus DUT pads tied to protocol-idle defaults at this level.
`timescale 1ns/1ps

module {ip}_tb_top;
    import uvm_pkg::*;
    `include "uvm_macros.svh"

    // ---- clock & reset (top owns the drive) ----
    logic {clk_name} = 0;
    logic {rst_name} = {rst_asserted};
    always #{half_period} {clk_name} = ~{clk_name};

    // ---- interface ----
    {bus_inst}

    // ---- non-bus DUT pad defaults ----
{pad_decls_str}

    // ---- DUT ----
    {top} u_dut (
{dut_bus_block}{pad_connects_str}
    );

    // ---- reset sequence ----
    initial begin
        {rst_name} = {rst_asserted};
        repeat ({rst_cycles}) @(posedge {clk_name});
        {rst_name} = {rst_released};
    end

    // ---- UVM entry ----
    initial begin
{config_block}
        run_test();
    end
endmodule
"""


def emit_tb_api_pkg(bus: str, addr_w: int, data_w: int = 32, handshake: dict | None = None) -> str:
    if bus == "generic":
        assert handshake is not None
        prefix = handshake["bus_name"]
        if_type = f"{prefix}_if"
        vif_typedef = f"typedef virtual {if_type} vif_t;"
    else:
        if_type = {"apb": "apb_if", "ahb": "ahb_if", "axi_lite": "axi_lite_if"}[bus]
        vif_typedef = f"typedef virtual {if_type} #(.ADDR_W(ADDR_W), .DATA_W(DATA_W)) vif_t;"
    return textwrap.dedent(f"""\
        `ifndef TB_API_PKG_SV
        `define TB_API_PKG_SV
        package tb_api;
            import uvm_pkg::*;
            `include "uvm_macros.svh"
            parameter int  ADDR_W = {addr_w};
            parameter int  DATA_W = {data_w};
            {vif_typedef}
            vif_t vif;
            function automatic void set_vif(vif_t v); vif = v; endfunction
            `include "tb_api_primitives.svh"
        endpackage
        `endif
        """)


def emit_tb_api_primitives(bus: str, direction: str = "slave", handshake: dict | None = None,
                            axi_full_signature: bool = False) -> str:
    if bus == "generic":
        assert handshake is not None
        return _emit_generic_tb_api_primitives(handshake)
    if bus == "axi_lite":
        return _emit_axi_lite_tb_api_primitives(direction, axi_full_signature)
    if bus == "ahb" and direction == "master":
        return _emit_ahb_master_tb_api_primitives()
    if bus == "ahb":
        return textwrap.dedent("""\
            // ====================================================================
            // tb_api primitives — AHB-Lite single-transfer master + status helpers.
            // ====================================================================

            function automatic void _require_vif();
                if (vif == null) `uvm_fatal("TB_API",
                    "vif not set — call tb_api::set_vif(...) in top initial block")
            endfunction

            task automatic _idle();
                vif.hsel    <= 1'b0;
                vif.htrans  <= 2'b00;
                vif.hwrite  <= 1'b0;
                vif.haddr   <= '0;
                vif.hsize   <= 3'b010;
                vif.hburst  <= 3'b000;
                vif.hprot   <= 4'b0011;
                vif.hwdata  <= '0;
            endtask

            task automatic write(input logic [ADDR_W-1:0] addr,
                                 input logic [DATA_W-1:0] data);
                _require_vif();
                @(posedge vif.hclk);
                vif.hsel    <= 1'b1;
                vif.htrans  <= 2'b10;
                vif.hwrite  <= 1'b1;
                vif.haddr   <= addr;
                vif.hsize   <= 3'b010;
                vif.hburst  <= 3'b000;
                vif.hprot   <= 4'b0011;
                vif.hwdata  <= data;
                @(posedge vif.hclk);
                while (vif.hready !== 1'b1) @(posedge vif.hclk);
                _idle();
            endtask

            task automatic read(input  logic [ADDR_W-1:0] addr,
                                output logic [DATA_W-1:0] data);
                _require_vif();
                @(posedge vif.hclk);
                vif.hsel    <= 1'b1;
                vif.htrans  <= 2'b10;
                vif.hwrite  <= 1'b0;
                vif.haddr   <= addr;
                vif.hsize   <= 3'b010;
                vif.hburst  <= 3'b000;
                vif.hprot   <= 4'b0011;
                @(posedge vif.hclk);
                while (vif.hready !== 1'b1) @(posedge vif.hclk);
                #1ps;
                data = vif.hrdata;
                _idle();
            endtask

            task automatic write_array(input logic [ADDR_W-1:0] base,
                                        input int                stride,
                                        input logic [DATA_W-1:0] data[]);
                int i;
                for (i = 0; i < data.size(); i++) write(base + i*stride, data[i]);
            endtask

            task automatic read_array(input  logic [ADDR_W-1:0] base,
                                       input  int                stride,
                                       input  int                count,
                                       output logic [DATA_W-1:0] data[]);
                int i;
                data = new[count];
                for (i = 0; i < count; i++) read(base + i*stride, data[i]);
            endtask

            task automatic expect_reg(input logic [ADDR_W-1:0] addr,
                                       input logic [DATA_W-1:0] expected,
                                       input string             tag = "EXPECT");
                logic [DATA_W-1:0] got;
                read(addr, got);
                if (got !== expected)
                    `uvm_error(tag, $sformatf("@0x%0h got=0x%08h expected=0x%08h",
                                              addr, got, expected))
                else
                    `uvm_info(tag, $sformatf("@0x%0h = 0x%08h", addr, got), UVM_LOW)
            endtask

            task automatic wait_status_flag(
                    input logic [ADDR_W-1:0] status_addr,
                    input int                bit_idx,
                    input bit                expected = 1'b1,
                    input bit                wait_low_first = 1'b0,
                    input int                timeout_polls = 1000,
                    input string             tag = "WAIT");
                int  n;
                logic [DATA_W-1:0] rdata;
                if (wait_low_first) begin
                    n = timeout_polls;
                    do begin
                        read(status_addr, rdata);
                        if (rdata[bit_idx] !== expected) break;
                        if (--n == 0) `uvm_fatal(tag, "timeout waiting for status bit to drop")
                    end while (1);
                end
                n = timeout_polls;
                do begin
                    read(status_addr, rdata);
                    if (rdata[bit_idx] === expected) return;
                    if (--n == 0) `uvm_fatal(tag, $sformatf("timeout waiting for bit %0d == %0d", bit_idx, expected))
                end while (1);
            endtask
            """)

    # Body verbatim from references/tb_api.md
    return textwrap.dedent("""\
        // ====================================================================
        // tb_api primitives — known-good APB master + status helpers.
        // Body verbatim from references/tb_api.md. Do not hand-edit.
        // ====================================================================

        function automatic void _require_vif();
            if (vif == null) `uvm_fatal("TB_API",
                "vif not set — call tb_api::set_vif(...) in top initial block")
        endfunction

        task automatic write(input logic [ADDR_W-1:0] addr,
                             input logic [DATA_W-1:0] data);
            _require_vif();
            @(posedge vif.pclk);
            vif.psel    <= 1'b1; vif.penable <= 1'b0;
            vif.pwrite  <= 1'b1; vif.paddr   <= addr; vif.pwdata <= data;
            @(posedge vif.pclk);
            vif.penable <= 1'b1;
            do @(posedge vif.pclk); while (vif.pready !== 1'b1);
            vif.psel    <= 1'b0; vif.penable <= 1'b0; vif.pwrite <= 1'b0;
        endtask

        task automatic read(input  logic [ADDR_W-1:0] addr,
                            output logic [DATA_W-1:0] data);
            _require_vif();
            @(posedge vif.pclk);
            vif.psel    <= 1'b1; vif.penable <= 1'b0;
            vif.pwrite  <= 1'b0; vif.paddr   <= addr;
            @(posedge vif.pclk);
            vif.penable <= 1'b1;
            do @(posedge vif.pclk); while (vif.pready !== 1'b1);
        `ifdef TB_API_EXTRA_READ_CYCLE
            @(posedge vif.pclk);
        `endif
            data = vif.prdata;
            vif.psel <= 1'b0; vif.penable <= 1'b0;
        endtask

        task automatic write_array(input logic [ADDR_W-1:0] base,
                                    input int                stride,
                                    input logic [DATA_W-1:0] data[]);
            int i;
            for (i = 0; i < data.size(); i++) write(base + i*stride, data[i]);
        endtask

        task automatic read_array(input  logic [ADDR_W-1:0] base,
                                   input  int                stride,
                                   input  int                count,
                                   output logic [DATA_W-1:0] data[]);
            int i;
            data = new[count];
            for (i = 0; i < count; i++) read(base + i*stride, data[i]);
        endtask

        task automatic expect_reg(input logic [ADDR_W-1:0] addr,
                                   input logic [DATA_W-1:0] expected,
                                   input string             tag = "EXPECT");
            logic [DATA_W-1:0] got;
            read(addr, got);
            if (got !== expected)
                `uvm_error(tag, $sformatf("@0x%0h got=0x%08h expected=0x%08h",
                                          addr, got, expected))
            else
                `uvm_info(tag, $sformatf("@0x%0h = 0x%08h", addr, got), UVM_LOW)
        endtask

        task automatic wait_status_flag(
                input logic [ADDR_W-1:0] status_addr,
                input int                bit_idx,
                input bit                expected = 1'b1,
                input bit                wait_low_first = 1'b0,
                input int                timeout_polls = 1000,
                input string             tag = "WAIT");
            int  n;
            logic [DATA_W-1:0] rdata;
            if (wait_low_first) begin
                n = timeout_polls;
                do begin
                    read(status_addr, rdata);
                    if (rdata[bit_idx] !== expected) break;
                    if (--n == 0) `uvm_fatal(tag, "timeout waiting for status bit to drop")
                end while (1);
            end
            n = timeout_polls;
            do begin
                read(status_addr, rdata);
                if (rdata[bit_idx] === expected) return;
                if (--n == 0) `uvm_fatal(tag, $sformatf("timeout waiting for bit %0d == %0d", bit_idx, expected))
            end while (1);
        endtask
        """)


def _emit_axi_lite_tb_api_primitives(direction: str, axi_full_signature: bool = False) -> str:
    if direction == "master":
        # DUT is master; tb_api drives the slave responder helpers.
        return textwrap.dedent("""\
            // ====================================================================
            // tb_api primitives — AXI4-Lite responder helpers (DUT is master).
            // The responder agent owns the live bus handshakes; these helpers
            // expose its memory and observed-transaction signals.
            // ====================================================================

            function automatic void _require_vif();
                if (vif == null) `uvm_fatal("TB_API",
                    "vif not set — call tb_api::set_vif(...) in top initial block")
            endfunction

            // Backing memory; shared between the responder and these helpers.
            logic [DATA_W-1:0] _mem [logic [ADDR_W-1:0]];

            // Observed-transaction counters; bumped by the responder.
            int unsigned writes_observed = 0;
            int unsigned reads_observed  = 0;
            logic [ADDR_W-1:0] last_write_addr;
            logic [DATA_W-1:0] last_write_data;
            logic [ADDR_W-1:0] last_read_addr;

            function automatic void seed_mem(input logic [ADDR_W-1:0] addr,
                                              input logic [DATA_W-1:0] data);
                _mem[addr] = data;
            endfunction

            function automatic logic [DATA_W-1:0] peek_mem(input logic [ADDR_W-1:0] addr);
                if (_mem.exists(addr)) return _mem[addr];
                return '0;
            endfunction

            function automatic void clear_observed();
                writes_observed = 0;
                reads_observed  = 0;
            endfunction

            // Block until at least one write/read has been observed. If one
            // has already happened before the call, return immediately. Use
            // clear_observed() before a fresh wait window.
            task automatic wait_for_write(input int unsigned timeout_cycles = 1000);
                int unsigned n = timeout_cycles;
                _require_vif();
                while (writes_observed == 0) begin
                    @(posedge vif.aclk);
                    if (--n == 0) `uvm_fatal("TB_API",
                        "timeout waiting for DUT-initiated write")
                end
            endtask

            task automatic wait_for_read(input int unsigned timeout_cycles = 1000);
                int unsigned n = timeout_cycles;
                _require_vif();
                while (reads_observed == 0) begin
                    @(posedge vif.aclk);
                    if (--n == 0) `uvm_fatal("TB_API",
                        "timeout waiting for DUT-initiated read")
                end
            endtask

            task automatic expect_observed_write(input logic [ADDR_W-1:0] addr,
                                                  input logic [DATA_W-1:0] data,
                                                  input string             tag = "EXPECT_WR");
                if (writes_observed == 0)
                    `uvm_error(tag, "no writes observed yet")
                else if (last_write_addr !== addr || last_write_data !== data)
                    `uvm_error(tag, $sformatf(
                        "last observed write @0x%0h=0x%08h, expected @0x%0h=0x%08h",
                        last_write_addr, last_write_data, addr, data))
                else
                    `uvm_info(tag, $sformatf("@0x%0h = 0x%08h", addr, data), UVM_LOW)
            endtask
            """)

    # DUT-as-slave: TB master BFM (AXI4-Lite write/read primitives).
    # In degraded-mode (axi_full_signature: true), the TB master must tie
    # AWLEN/ARLEN/AWBURST/ARBURST/AWID/ARID to zero on every transaction
    # so the DUT cannot mistake the access for a burst.
    full_axi_w_tieoffs = textwrap.dedent("""\
        vif.awlen   <= 8'h00;
        vif.awsize  <= 3'b010;
        vif.awburst <= 2'b01;
        vif.awid    <= '0;
        vif.wlast   <= 1'b1;""") if axi_full_signature else ""
    full_axi_r_tieoffs = textwrap.dedent("""\
        vif.arlen   <= 8'h00;
        vif.arsize  <= 3'b010;
        vif.arburst <= 2'b01;
        vif.arid    <= '0;""") if axi_full_signature else ""
    full_axi_idle = textwrap.dedent("""\
        vif.awlen   <= 8'h00;
        vif.awsize  <= 3'b010;
        vif.awburst <= 2'b01;
        vif.awid    <= '0;
        vif.wlast   <= 1'b0;
        vif.arlen   <= 8'h00;
        vif.arsize  <= 3'b010;
        vif.arburst <= 2'b01;
        vif.arid    <= '0;""") if axi_full_signature else ""
    # Indent the tie-off blocks to match the surrounding task bodies (12 spaces).
    def _indent(s, n=12):
        return "\n".join((" " * n + line) if line.strip() else line for line in s.splitlines()) + ("\n" if s else "")
    write_tieoffs = _indent(full_axi_w_tieoffs)
    read_tieoffs  = _indent(full_axi_r_tieoffs)
    idle_tieoffs  = _indent(full_axi_idle, 12)
    return textwrap.dedent(f"""\
        // ====================================================================
        // tb_api primitives — AXI4-Lite single-beat master + helpers.
        // ====================================================================

        function automatic void _require_vif();
            if (vif == null) `uvm_fatal("TB_API",
                "vif not set — call tb_api::set_vif(...) in top initial block")
        endfunction

        task automatic _idle();
            vif.awvalid <= 1'b0;
            vif.wvalid  <= 1'b0;
            vif.bready  <= 1'b0;
            vif.arvalid <= 1'b0;
            vif.rready  <= 1'b0;
{idle_tieoffs}        endtask

        task automatic write(input logic [ADDR_W-1:0] addr,
                             input logic [DATA_W-1:0] data);
            _require_vif();
            @(posedge vif.aclk);
            vif.awvalid <= 1'b1;
            vif.awaddr  <= addr;
            vif.awprot  <= 3'b000;
            vif.wvalid  <= 1'b1;
            vif.wdata   <= data;
            vif.wstrb   <= '1;
            vif.bready  <= 1'b1;
{write_tieoffs}
            // Detect each handshake on the edge where both valid & ready are
            // high in the same cycle. Sample BEFORE the NBA region by using
            // `@(posedge clk iff ...)` so we don't race with our own drives.
            fork
                begin
                    @(posedge vif.aclk iff (vif.awvalid && vif.awready));
                    vif.awvalid <= 1'b0;
                end
                begin
                    @(posedge vif.aclk iff (vif.wvalid && vif.wready));
                    vif.wvalid <= 1'b0;
                end
                begin
                    @(posedge vif.aclk iff (vif.bvalid && vif.bready));
                end
            join
            vif.bready <= 1'b0;
            _idle();
        endtask

        task automatic read(input  logic [ADDR_W-1:0] addr,
                            output logic [DATA_W-1:0] data);
            _require_vif();
            @(posedge vif.aclk);
            vif.arvalid <= 1'b1;
            vif.araddr  <= addr;
            vif.arprot  <= 3'b000;
            vif.rready  <= 1'b1;
{read_tieoffs}            fork
                begin
                    @(posedge vif.aclk iff (vif.arvalid && vif.arready));
                    vif.arvalid <= 1'b0;
                end
                begin
                    @(posedge vif.aclk iff (vif.rvalid && vif.rready));
                    data = vif.rdata;
                end
            join
            vif.rready <= 1'b0;
            _idle();
        endtask

        task automatic write_array(input logic [ADDR_W-1:0] base,
                                    input int                stride,
                                    input logic [DATA_W-1:0] data[]);
            int i;
            for (i = 0; i < data.size(); i++) write(base + i*stride, data[i]);
        endtask

        task automatic read_array(input  logic [ADDR_W-1:0] base,
                                   input  int                stride,
                                   input  int                count,
                                   output logic [DATA_W-1:0] data[]);
            int i;
            data = new[count];
            for (i = 0; i < count; i++) read(base + i*stride, data[i]);
        endtask

        task automatic expect_reg(input logic [ADDR_W-1:0] addr,
                                   input logic [DATA_W-1:0] expected,
                                   input string             tag = "EXPECT");
            logic [DATA_W-1:0] got;
            read(addr, got);
            if (got !== expected)
                `uvm_error(tag, $sformatf("@0x%0h got=0x%08h expected=0x%08h",
                                          addr, got, expected))
            else
                `uvm_info(tag, $sformatf("@0x%0h = 0x%08h", addr, got), UVM_LOW)
        endtask

        task automatic wait_status_flag(
                input logic [ADDR_W-1:0] status_addr,
                input int                bit_idx,
                input bit                expected = 1'b1,
                input bit                wait_low_first = 1'b0,
                input int                timeout_polls = 1000,
                input string             tag = "WAIT");
            int  n;
            logic [DATA_W-1:0] rdata;
            if (wait_low_first) begin
                n = timeout_polls;
                do begin
                    read(status_addr, rdata);
                    if (rdata[bit_idx] !== expected) break;
                    if (--n == 0) `uvm_fatal(tag, "timeout waiting for status bit to drop")
                end while (1);
            end
            n = timeout_polls;
            do begin
                read(status_addr, rdata);
                if (rdata[bit_idx] === expected) return;
                if (--n == 0) `uvm_fatal(tag, $sformatf("timeout waiting for bit %0d == %0d", bit_idx, expected))
            end while (1);
        endtask
        """)


def _emit_ahb_master_tb_api_primitives() -> str:
    # AHB-Lite responder helpers (DUT is master). Memory-backed shared state.
    return textwrap.dedent("""\
        // ====================================================================
        // tb_api primitives — AHB-Lite responder helpers (DUT is master).
        // The responder agent owns the live bus handshakes; these helpers
        // expose its memory and observed-transaction signals.
        // ====================================================================

        function automatic void _require_vif();
            if (vif == null) `uvm_fatal("TB_API",
                "vif not set — call tb_api::set_vif(...) in top initial block")
        endfunction

        logic [DATA_W-1:0] _mem [logic [ADDR_W-1:0]];
        int unsigned writes_observed = 0;
        int unsigned reads_observed  = 0;
        logic [ADDR_W-1:0] last_write_addr;
        logic [DATA_W-1:0] last_write_data;
        logic [ADDR_W-1:0] last_read_addr;

        function automatic void clear_observed();
            writes_observed = 0;
            reads_observed  = 0;
        endfunction

        function automatic void seed_mem(input logic [ADDR_W-1:0] addr,
                                          input logic [DATA_W-1:0] data);
            _mem[addr] = data;
        endfunction

        function automatic logic [DATA_W-1:0] peek_mem(input logic [ADDR_W-1:0] addr);
            if (_mem.exists(addr)) return _mem[addr];
            return '0;
        endfunction

        // Wait for at least one observed write/read since start of sim (or
        // since the last clear_observed call). Returns immediately if one
        // has already happened.
        task automatic wait_for_write(input int unsigned timeout_cycles = 1000);
            int unsigned n = timeout_cycles;
            _require_vif();
            while (writes_observed == 0) begin
                @(posedge vif.hclk);
                if (--n == 0) `uvm_fatal("TB_API",
                    "timeout waiting for DUT-initiated write")
            end
        endtask

        task automatic wait_for_read(input int unsigned timeout_cycles = 1000);
            int unsigned n = timeout_cycles;
            _require_vif();
            while (reads_observed == 0) begin
                @(posedge vif.hclk);
                if (--n == 0) `uvm_fatal("TB_API",
                    "timeout waiting for DUT-initiated read")
            end
        endtask

        task automatic expect_observed_write(input logic [ADDR_W-1:0] addr,
                                              input logic [DATA_W-1:0] data,
                                              input string             tag = "EXPECT_WR");
            if (writes_observed == 0)
                `uvm_error(tag, "no writes observed yet")
            else if (last_write_addr !== addr || last_write_data !== data)
                `uvm_error(tag, $sformatf(
                    "last observed write @0x%0h=0x%08h, expected @0x%0h=0x%08h",
                    last_write_addr, last_write_data, addr, data))
            else
                `uvm_info(tag, $sformatf("@0x%0h = 0x%08h", addr, data), UVM_LOW)
        endtask
        """)


def emit_apb_agent_pkg() -> str:
    return textwrap.dedent("""\
        `ifndef APB_AGENT_SV
        `define APB_AGENT_SV
        package apb_agt_pkg;
            import uvm_pkg::*;
            import tb_api::*;
            `include "uvm_macros.svh"

            `include "apb_agt_config.sv"
            `include "apb_trans.sv"
            `include "apb_sequencer.sv"
            `include "apb_driver.sv"
            `include "apb_monitor.sv"
            `include "apb_sequence.sv"

            class apb_agent extends uvm_agent;
                `uvm_component_utils(apb_agent)
                apb_driver     drv;
                apb_monitor    mon;
                apb_sequencer  sqr;
                apb_agt_config cfg;

                function new(string name, uvm_component parent = null);
                    super.new(name, parent);
                endfunction

                function void build_phase(uvm_phase phase);
                    super.build_phase(phase);
                    if (!uvm_config_db#(apb_agt_config)::get(this, "", "cfg", cfg))
                        `uvm_fatal("CFG", "apb_agt_config missing")
                    if (cfg.vif != null)
                        uvm_config_db#(vif_t)::set(this, "*", "apb_vif", cfg.vif);
                    mon = apb_monitor::type_id::create("mon", this);
                    if (cfg.is_active == UVM_ACTIVE) begin
                        drv = apb_driver::type_id::create("drv", this);
                        sqr = apb_sequencer::type_id::create("sqr", this);
                    end
                endfunction

                function void connect_phase(uvm_phase phase);
                    super.connect_phase(phase);
                    if (cfg.is_active == UVM_ACTIVE)
                        drv.seq_item_port.connect(sqr.seq_item_export);
                endfunction
            endclass
        endpackage
        `endif
        """)


def emit_apb_agt_config() -> str:
    return textwrap.dedent("""\
        class apb_agt_config extends uvm_object;
            `uvm_object_utils(apb_agt_config)
            uvm_active_passive_enum is_active = UVM_ACTIVE;
            vif_t vif;

            function new(string name = "apb_agt_config");
                super.new(name);
            endfunction
        endclass
        """)


def emit_apb_trans() -> str:
    return textwrap.dedent("""\
        class apb_trans extends uvm_sequence_item;
            rand logic [ADDR_W-1:0] addr;
            rand logic [DATA_W-1:0] data;
            rand bit                write;
            logic [DATA_W-1:0]      rdata;
            bit                     slverr;

            `uvm_object_utils_begin(apb_trans)
                `uvm_field_int(addr,   UVM_ALL_ON)
                `uvm_field_int(data,   UVM_ALL_ON)
                `uvm_field_int(write,  UVM_ALL_ON)
                `uvm_field_int(rdata,  UVM_ALL_ON | UVM_NOCOMPARE)
                `uvm_field_int(slverr, UVM_ALL_ON | UVM_NOCOMPARE)
            `uvm_object_utils_end

            function new(string name = "apb_trans");
                super.new(name);
            endfunction
        endclass
        """)


def emit_apb_sequencer() -> str:
    return textwrap.dedent("""\
        typedef uvm_sequencer #(apb_trans) apb_sequencer;
        """)


def emit_apb_driver() -> str:
    return textwrap.dedent("""\
        class apb_driver extends uvm_driver #(apb_trans);
            `uvm_component_utils(apb_driver)
            vif_t vif;

            function new(string name, uvm_component parent = null);
                super.new(name, parent);
            endfunction

            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                if (!uvm_config_db#(vif_t)::get(this, "", "apb_vif", vif))
                    `uvm_fatal("CFG", "apb_vif missing")
                tb_api::set_vif(vif);
            endfunction

            task run_phase(uvm_phase phase);
                apb_trans tr;
                forever begin
                    seq_item_port.get_next_item(tr);
                    if (tr.write)
                        tb_api::write(tr.addr, tr.data);
                    else
                        tb_api::read(tr.addr, tr.rdata);
                    tr.slverr = vif.pslverr;
                    seq_item_port.item_done();
                end
            endtask
        endclass
        """)


def emit_apb_monitor() -> str:
    return textwrap.dedent("""\
        class apb_monitor extends uvm_monitor;
            `uvm_component_utils(apb_monitor)
            uvm_analysis_port #(apb_trans) ap;
            vif_t vif;

            function new(string name, uvm_component parent = null);
                super.new(name, parent);
                ap = new("ap", this);
            endfunction

            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                if (!uvm_config_db#(vif_t)::get(this, "", "apb_vif", vif))
                    `uvm_fatal("CFG", "apb_vif missing")
            endfunction

            task run_phase(uvm_phase phase);
                apb_trans tr;
                forever begin
                    @(posedge vif.pclk iff (vif.psel & vif.penable & vif.pready));
                    tr = apb_trans::type_id::create("tr");
                    tr.addr   = vif.paddr;
                    tr.write  = vif.pwrite;
                    tr.data   = vif.pwdata;
                    tr.rdata  = vif.prdata;
                    tr.slverr = vif.pslverr;
                    ap.write(tr);
                end
            endtask
        endclass
        """)


def emit_apb_sequence() -> str:
    return textwrap.dedent("""\
        class apb_sequence extends uvm_sequence #(apb_trans);
            `uvm_object_utils(apb_sequence)
            int unsigned n_transactions = 1;
            logic [ADDR_W-1:0] legal_addrs[$];

            function new(string name = "apb_sequence");
                super.new(name);
            endfunction

            task body();
                apb_trans tr;
                repeat (n_transactions) begin
                    tr = apb_trans::type_id::create("tr");
                    start_item(tr);
                    if (!tr.randomize() with { write == 1'b0; })
                        `uvm_fatal("RAND", "apb_trans randomize failed")
                    if (legal_addrs.size() != 0)
                        tr.addr = legal_addrs[$urandom_range(0, legal_addrs.size()-1)];
                    finish_item(tr);
                end
            endtask
        endclass
        """)


def emit_ahb_agent_pkg(direction: str = "slave") -> str:
    role = "// slave responder (DUT is master)" if direction == "master" else "// master BFM (DUT is slave)"
    _ = role  # currently only emitted as a comment via the format string
    return textwrap.dedent("""\
        `ifndef AHB_AGENT_SV
        `define AHB_AGENT_SV
        package ahb_agt_pkg;
            import uvm_pkg::*;
            import tb_api::*;
            `include "uvm_macros.svh"

            `include "ahb_agt_config.sv"
            `include "ahb_trans.sv"
            `include "ahb_sequencer.sv"
            `include "ahb_driver.sv"
            `include "ahb_monitor.sv"
            `include "ahb_sequence.sv"

            class ahb_agent extends uvm_agent;
                `uvm_component_utils(ahb_agent)
                ahb_driver     drv;
                ahb_monitor    mon;
                ahb_sequencer  sqr;
                ahb_agt_config cfg;

                function new(string name, uvm_component parent = null);
                    super.new(name, parent);
                endfunction

                function void build_phase(uvm_phase phase);
                    super.build_phase(phase);
                    if (!uvm_config_db#(ahb_agt_config)::get(this, "", "cfg", cfg))
                        `uvm_fatal("CFG", "ahb_agt_config missing")
                    if (cfg.vif != null)
                        uvm_config_db#(vif_t)::set(this, "*", "ahb_vif", cfg.vif);
                    mon = ahb_monitor::type_id::create("mon", this);
                    if (cfg.is_active == UVM_ACTIVE) begin
                        drv = ahb_driver::type_id::create("drv", this);
                        sqr = ahb_sequencer::type_id::create("sqr", this);
                    end
                endfunction

                function void connect_phase(uvm_phase phase);
                    super.connect_phase(phase);
                    if (cfg.is_active == UVM_ACTIVE)
                        drv.seq_item_port.connect(sqr.seq_item_export);
                endfunction
            endclass
        endpackage
        `endif
        """)


def emit_ahb_agt_config() -> str:
    return textwrap.dedent("""\
        class ahb_agt_config extends uvm_object;
            `uvm_object_utils(ahb_agt_config)
            uvm_active_passive_enum is_active = UVM_ACTIVE;
            vif_t vif;

            function new(string name = "ahb_agt_config");
                super.new(name);
            endfunction
        endclass
        """)


def emit_ahb_trans() -> str:
    return textwrap.dedent("""\
        class ahb_trans extends uvm_sequence_item;
            rand logic [ADDR_W-1:0] addr;
            rand logic [DATA_W-1:0] data;
            rand bit                write;
            logic [DATA_W-1:0]      rdata;
            bit                     resp;

            `uvm_object_utils_begin(ahb_trans)
                `uvm_field_int(addr,  UVM_ALL_ON)
                `uvm_field_int(data,  UVM_ALL_ON)
                `uvm_field_int(write, UVM_ALL_ON)
                `uvm_field_int(rdata, UVM_ALL_ON | UVM_NOCOMPARE)
                `uvm_field_int(resp,  UVM_ALL_ON | UVM_NOCOMPARE)
            `uvm_object_utils_end

            function new(string name = "ahb_trans");
                super.new(name);
            endfunction
        endclass
        """)


def emit_ahb_sequencer() -> str:
    return textwrap.dedent("""\
        typedef uvm_sequencer #(ahb_trans) ahb_sequencer;
        """)


def emit_ahb_driver(direction: str = "slave") -> str:
    if direction == "master":
        # AHB-Lite slave responder (DUT is master).
        # Single-master, single-beat, zero-wait-state behavior: drive
        # hready=1 always; on the cycle after a NONSEQ address phase,
        # capture data into _mem (for writes) or drive hrdata (for reads).
        return textwrap.dedent("""\
            class ahb_driver extends uvm_driver #(ahb_trans);
                `uvm_component_utils(ahb_driver)
                vif_t vif;

                function new(string name, uvm_component parent = null);
                    super.new(name, parent);
                endfunction

                function void build_phase(uvm_phase phase);
                    super.build_phase(phase);
                    if (!uvm_config_db#(vif_t)::get(this, "", "ahb_vif", vif))
                        `uvm_fatal("CFG", "ahb_vif missing")
                    tb_api::set_vif(vif);
                endfunction

                task run_phase(uvm_phase phase);
                    bit                addr_pend;
                    bit                pend_write;
                    logic [ADDR_W-1:0] pend_addr;
                    @(posedge vif.hclk);
                    vif.hready <= 1'b1;
                    vif.hresp  <= 1'b0;
                    vif.hrdata <= '0;
                    addr_pend  = 1'b0;
                    pend_write = 1'b0;
                    pend_addr  = '0;
                    forever begin
                        @(posedge vif.hclk);
                        if (vif.hresetn !== 1'b1) begin
                            addr_pend  = 1'b0;
                            vif.hrdata <= '0;
                            continue;
                        end
                        // Data phase for any captured address.
                        if (addr_pend) begin
                            if (pend_write) begin
                                tb_api::_mem[pend_addr]  = vif.hwdata;
                                tb_api::last_write_addr  = pend_addr;
                                tb_api::last_write_data  = vif.hwdata;
                                tb_api::writes_observed  = tb_api::writes_observed + 1;
                            end else begin
                                tb_api::last_read_addr = pend_addr;
                                tb_api::reads_observed = tb_api::reads_observed + 1;
                            end
                            addr_pend = 1'b0;
                        end
                        // Address phase capture (NONSEQ).
                        if (vif.hsel === 1'b1 && vif.htrans === 2'b10) begin
                            pend_addr  = vif.haddr;
                            pend_write = vif.hwrite;
                            addr_pend  = 1'b1;
                            // For reads, drive hrdata in the next cycle's data phase.
                            if (!vif.hwrite) begin
                                vif.hrdata <= tb_api::_mem.exists(vif.haddr) ?
                                               tb_api::_mem[vif.haddr] : '0;
                            end
                        end
                    end
                endtask
            endclass
            """)
    # Master BFM (DUT is slave) — unchanged.
    return textwrap.dedent("""\
        class ahb_driver extends uvm_driver #(ahb_trans);
            `uvm_component_utils(ahb_driver)
            vif_t vif;

            function new(string name, uvm_component parent = null);
                super.new(name, parent);
            endfunction

            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                if (!uvm_config_db#(vif_t)::get(this, "", "ahb_vif", vif))
                    `uvm_fatal("CFG", "ahb_vif missing")
                tb_api::set_vif(vif);
            endfunction

            task run_phase(uvm_phase phase);
                ahb_trans tr;
                forever begin
                    seq_item_port.get_next_item(tr);
                    if (tr.write)
                        tb_api::write(tr.addr, tr.data);
                    else
                        tb_api::read(tr.addr, tr.rdata);
                    tr.resp = vif.hresp;
                    seq_item_port.item_done();
                end
            endtask
        endclass
        """)


def emit_ahb_monitor() -> str:
    return textwrap.dedent("""\
        class ahb_monitor extends uvm_monitor;
            `uvm_component_utils(ahb_monitor)
            uvm_analysis_port #(ahb_trans) ap;
            vif_t vif;

            function new(string name, uvm_component parent = null);
                super.new(name, parent);
                ap = new("ap", this);
            endfunction

            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                if (!uvm_config_db#(vif_t)::get(this, "", "ahb_vif", vif))
                    `uvm_fatal("CFG", "ahb_vif missing")
            endfunction

            task run_phase(uvm_phase phase);
                ahb_trans tr;
                forever begin
                    @(posedge vif.hclk iff (vif.hsel & vif.htrans[1] & vif.hready));
                    tr = ahb_trans::type_id::create("tr");
                    tr.addr  = vif.haddr;
                    tr.write = vif.hwrite;
                    tr.data  = vif.hwdata;
                    tr.rdata = vif.hrdata;
                    tr.resp  = vif.hresp;
                    ap.write(tr);
                end
            endtask
        endclass
        """)


def emit_ahb_sequence() -> str:
    return textwrap.dedent("""\
        class ahb_sequence extends uvm_sequence #(ahb_trans);
            `uvm_object_utils(ahb_sequence)
            int unsigned n_transactions = 1;
            logic [ADDR_W-1:0] legal_addrs[$];

            function new(string name = "ahb_sequence");
                super.new(name);
            endfunction

            task body();
                ahb_trans tr;
                repeat (n_transactions) begin
                    tr = ahb_trans::type_id::create("tr");
                    start_item(tr);
                    if (!tr.randomize() with { write == 1'b0; })
                        `uvm_fatal("RAND", "ahb_trans randomize failed")
                    if (legal_addrs.size() != 0)
                        tr.addr = legal_addrs[$urandom_range(0, legal_addrs.size()-1)];
                    finish_item(tr);
                end
            endtask
        endclass
        """)


def emit_axi_lite_agent_pkg(direction: str) -> str:
    driver_role = "// slave responder (DUT is master)" if direction == "master" else "// master BFM (DUT is slave)"
    return textwrap.dedent(f"""\
        `ifndef AXI_LITE_AGENT_SV
        `define AXI_LITE_AGENT_SV
        package axi_lite_agt_pkg;
            import uvm_pkg::*;
            import tb_api::*;
            `include "uvm_macros.svh"

            `include "axi_lite_agt_config.sv"
            `include "axi_lite_trans.sv"
            `include "axi_lite_sequencer.sv"
            `include "axi_lite_driver.sv"   {driver_role}
            `include "axi_lite_monitor.sv"
            `include "axi_lite_sequence.sv"

            class axi_lite_agent extends uvm_agent;
                `uvm_component_utils(axi_lite_agent)
                axi_lite_driver     drv;
                axi_lite_monitor    mon;
                axi_lite_sequencer  sqr;
                axi_lite_agt_config cfg;

                function new(string name, uvm_component parent = null);
                    super.new(name, parent);
                endfunction

                function void build_phase(uvm_phase phase);
                    super.build_phase(phase);
                    if (!uvm_config_db#(axi_lite_agt_config)::get(this, "", "cfg", cfg))
                        `uvm_fatal("CFG", "axi_lite_agt_config missing")
                    if (cfg.vif != null)
                        uvm_config_db#(vif_t)::set(this, "*", "axi_lite_vif", cfg.vif);
                    mon = axi_lite_monitor::type_id::create("mon", this);
                    if (cfg.is_active == UVM_ACTIVE) begin
                        drv = axi_lite_driver::type_id::create("drv", this);
                        sqr = axi_lite_sequencer::type_id::create("sqr", this);
                    end
                endfunction

                function void connect_phase(uvm_phase phase);
                    super.connect_phase(phase);
                    if (cfg.is_active == UVM_ACTIVE)
                        drv.seq_item_port.connect(sqr.seq_item_export);
                endfunction
            endclass
        endpackage
        `endif
        """)


def emit_axi_lite_agt_config() -> str:
    return textwrap.dedent("""\
        class axi_lite_agt_config extends uvm_object;
            `uvm_object_utils(axi_lite_agt_config)
            uvm_active_passive_enum is_active = UVM_ACTIVE;
            vif_t vif;

            function new(string name = "axi_lite_agt_config");
                super.new(name);
            endfunction
        endclass
        """)


def emit_axi_lite_trans() -> str:
    return textwrap.dedent("""\
        class axi_lite_trans extends uvm_sequence_item;
            rand logic [ADDR_W-1:0] addr;
            rand logic [DATA_W-1:0] data;
            rand bit                write;
            logic [DATA_W-1:0]      rdata;
            logic [1:0]             resp;

            `uvm_object_utils_begin(axi_lite_trans)
                `uvm_field_int(addr,  UVM_ALL_ON)
                `uvm_field_int(data,  UVM_ALL_ON)
                `uvm_field_int(write, UVM_ALL_ON)
                `uvm_field_int(rdata, UVM_ALL_ON | UVM_NOCOMPARE)
                `uvm_field_int(resp,  UVM_ALL_ON | UVM_NOCOMPARE)
            `uvm_object_utils_end

            function new(string name = "axi_lite_trans");
                super.new(name);
            endfunction
        endclass
        """)


def emit_axi_lite_sequencer() -> str:
    return textwrap.dedent("""\
        typedef uvm_sequencer #(axi_lite_trans) axi_lite_sequencer;
        """)


def emit_axi_lite_driver(direction: str) -> str:
    if direction == "master":
        # Slave responder. Listens to vif and responds; updates tb_api shared state.
        return textwrap.dedent("""\
            class axi_lite_driver extends uvm_driver #(axi_lite_trans);
                `uvm_component_utils(axi_lite_driver)
                vif_t vif;

                function new(string name, uvm_component parent = null);
                    super.new(name, parent);
                endfunction

                function void build_phase(uvm_phase phase);
                    super.build_phase(phase);
                    if (!uvm_config_db#(vif_t)::get(this, "", "axi_lite_vif", vif))
                        `uvm_fatal("CFG", "axi_lite_vif missing")
                    tb_api::set_vif(vif);
                endfunction

                task run_phase(uvm_phase phase);
                    fork
                        drive_idle();
                        respond_write();
                        respond_read();
                    join
                endtask

                task drive_idle();
                    @(posedge vif.aclk);
                    vif.awready <= 1'b0;
                    vif.wready  <= 1'b0;
                    vif.bvalid  <= 1'b0;
                    vif.bresp   <= 2'b00;
                    vif.arready <= 1'b0;
                    vif.rvalid  <= 1'b0;
                    vif.rdata   <= '0;
                    vif.rresp   <= 2'b00;
                endtask

                task respond_write();
                    logic [ADDR_W-1:0] aw_addr;
                    logic [DATA_W-1:0] w_data;
                    bit aw_done, w_done;
                    forever begin
                        @(posedge vif.aclk);
                        if (vif.aresetn !== 1'b1) continue;
                        aw_done = 0; w_done = 0;
                        vif.awready <= 1'b1;
                        vif.wready  <= 1'b1;
                        while (!(aw_done && w_done)) begin
                            @(posedge vif.aclk);
                            if (!aw_done && vif.awvalid === 1'b1) begin
                                aw_addr = vif.awaddr;
                                aw_done = 1;
                                vif.awready <= 1'b0;
                            end
                            if (!w_done && vif.wvalid === 1'b1) begin
                                w_data = vif.wdata;
                                w_done = 1;
                                vif.wready <= 1'b0;
                            end
                        end
                        tb_api::_mem[aw_addr] = w_data;
                        tb_api::last_write_addr = aw_addr;
                        tb_api::last_write_data = w_data;
                        tb_api::writes_observed = tb_api::writes_observed + 1;
                        vif.bvalid <= 1'b1;
                        vif.bresp  <= 2'b00;
                        do @(posedge vif.aclk); while (vif.bready !== 1'b1);
                        vif.bvalid <= 1'b0;
                    end
                endtask

                task respond_read();
                    logic [ADDR_W-1:0] ar_addr;
                    logic [DATA_W-1:0] rd;
                    forever begin
                        @(posedge vif.aclk);
                        if (vif.aresetn !== 1'b1) continue;
                        vif.arready <= 1'b1;
                        do @(posedge vif.aclk); while (vif.arvalid !== 1'b1);
                        ar_addr = vif.araddr;
                        vif.arready <= 1'b0;
                        rd = tb_api::_mem.exists(ar_addr) ? tb_api::_mem[ar_addr] : '0;
                        tb_api::last_read_addr = ar_addr;
                        tb_api::reads_observed = tb_api::reads_observed + 1;
                        vif.rdata  <= rd;
                        vif.rresp  <= 2'b00;
                        vif.rvalid <= 1'b1;
                        do @(posedge vif.aclk); while (vif.rready !== 1'b1);
                        vif.rvalid <= 1'b0;
                    end
                endtask
            endclass
            """)
    # Master BFM (DUT is slave). Same shape as APB/AHB driver: calls tb_api primitives.
    return textwrap.dedent("""\
        class axi_lite_driver extends uvm_driver #(axi_lite_trans);
            `uvm_component_utils(axi_lite_driver)
            vif_t vif;

            function new(string name, uvm_component parent = null);
                super.new(name, parent);
            endfunction

            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                if (!uvm_config_db#(vif_t)::get(this, "", "axi_lite_vif", vif))
                    `uvm_fatal("CFG", "axi_lite_vif missing")
                tb_api::set_vif(vif);
            endfunction

            task run_phase(uvm_phase phase);
                axi_lite_trans tr;
                forever begin
                    seq_item_port.get_next_item(tr);
                    if (tr.write)
                        tb_api::write(tr.addr, tr.data);
                    else
                        tb_api::read(tr.addr, tr.rdata);
                    tr.resp = vif.bvalid ? vif.bresp : vif.rresp;
                    seq_item_port.item_done();
                end
            endtask
        endclass
        """)


def emit_axi_lite_monitor(direction: str, axi_full_signature: bool = False) -> str:
    # Both directions observe completed handshakes on AW/W (write) and AR/R (read).
    # The AXI4-full degraded-mode guard (axi_full_signature) lives in the
    # interface, not here — SV concurrent assertions cannot sit in a class body.
    body = textwrap.dedent("""\
        class axi_lite_monitor extends uvm_monitor;
            `uvm_component_utils(axi_lite_monitor)
            uvm_analysis_port #(axi_lite_trans) ap;
            vif_t vif;

            function new(string name, uvm_component parent = null);
                super.new(name, parent);
                ap = new("ap", this);
            endfunction

            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                if (!uvm_config_db#(vif_t)::get(this, "", "axi_lite_vif", vif))
                    `uvm_fatal("CFG", "axi_lite_vif missing")
            endfunction

            task run_phase(uvm_phase phase);
                fork
                    observe_write();
                    observe_read();
                join
            endtask

            task observe_write();
                axi_lite_trans tr;
                logic [ADDR_W-1:0] aw_addr;
                logic [DATA_W-1:0] w_data;
                forever begin
                    @(posedge vif.aclk iff (vif.awvalid & vif.awready));
                    aw_addr = vif.awaddr;
                    @(posedge vif.aclk iff (vif.wvalid & vif.wready));
                    w_data = vif.wdata;
                    @(posedge vif.aclk iff (vif.bvalid & vif.bready));
                    tr = axi_lite_trans::type_id::create("tr");
                    tr.write = 1;
                    tr.addr  = aw_addr;
                    tr.data  = w_data;
                    tr.resp  = vif.bresp;
                    ap.write(tr);
                end
            endtask

            task observe_read();
                axi_lite_trans tr;
                logic [ADDR_W-1:0] ar_addr;
                forever begin
                    @(posedge vif.aclk iff (vif.arvalid & vif.arready));
                    ar_addr = vif.araddr;
                    @(posedge vif.aclk iff (vif.rvalid & vif.rready));
                    tr = axi_lite_trans::type_id::create("tr");
                    tr.write = 0;
                    tr.addr  = ar_addr;
                    tr.rdata = vif.rdata;
                    tr.resp  = vif.rresp;
                    ap.write(tr);
                end
            endtask
        endclass
        """)
    return body


def emit_axi_lite_sequence() -> str:
    return textwrap.dedent("""\
        class axi_lite_sequence extends uvm_sequence #(axi_lite_trans);
            `uvm_object_utils(axi_lite_sequence)
            int unsigned n_transactions = 1;
            logic [ADDR_W-1:0] legal_addrs[$];

            function new(string name = "axi_lite_sequence");
                super.new(name);
            endfunction

            task body();
                axi_lite_trans tr;
                repeat (n_transactions) begin
                    tr = axi_lite_trans::type_id::create("tr");
                    start_item(tr);
                    if (!tr.randomize() with { write == 1'b0; })
                        `uvm_fatal("RAND", "axi_lite_trans randomize failed")
                    if (legal_addrs.size() != 0)
                        tr.addr = legal_addrs[$urandom_range(0, legal_addrs.size()-1)];
                    finish_item(tr);
                end
            endtask
        endclass
        """)


# ---------------------------------------------------------------------------
# Generic-bus emitters: placeholder skeletons the scaffold sub-agent fills in.
# See references/generic_bus.md and references/sub_agent_generic_scaffold.md.
# ---------------------------------------------------------------------------


def _generic_extra_ports(handshake: dict) -> list[tuple[str, int]]:
    """Collected non-clk/rst/addr/data ports (handshake req/ack/extra)."""
    h = handshake["handshake"]
    out: list[tuple[str, int]] = []
    for key in ("req", "ack"):
        if h.get(key):
            out.append((h[key], 1))
    for name in h.get("extra") or []:
        out.append((name, 1))
    return out


def emit_generic_if(handshake: dict) -> str:
    bus = handshake["bus_name"]
    clk = handshake["clock"]["name"]
    rst = handshake["reset"]["name"]
    data = handshake["data"]
    addr = handshake.get("addr")

    decls = []
    if addr is not None:
        decls.append(f"    logic [{int(addr['width'])-1}:0] {addr['port']};")
    decls.append(f"    logic [{int(data['width'])-1}:0] {data['write_port']};")
    if data.get("read_port") and data["read_port"] != data["write_port"]:
        decls.append(f"    logic [{int(data['width'])-1}:0] {data['read_port']};")
    for name, width in _generic_extra_ports(handshake):
        if width == 1:
            decls.append(f"    logic {name};")
        else:
            decls.append(f"    logic [{width-1}:0] {name};")
    decls_str = "\n".join(decls) if decls else "    // (no ports declared)"

    guard = f"{bus.upper()}_IF_SV"
    return textwrap.dedent(f"""\
        `ifndef {guard}
        `define {guard}
        // PLACEHOLDER interface generated for generic-mode bus `{bus}`.
        // Ports come from work/_gen_audit/bus_handshake.yaml; the scaffold
        // sub-agent (references/sub_agent_generic_scaffold.md) adds the
        // clocking blocks and any modports needed for the driver/monitor.
        interface {bus}_if (
            input logic {clk},
            input logic {rst}
        );
        {decls_str}
        endinterface
        `endif
        """)


def emit_generic_agent_pkg(handshake: dict) -> str:
    bus = handshake["bus_name"]
    return textwrap.dedent(f"""\
        `ifndef {bus.upper()}_AGT_PKG_SV
        `define {bus.upper()}_AGT_PKG_SV
        package {bus}_agt_pkg;
            import uvm_pkg::*;
            `include "uvm_macros.svh"
            typedef virtual {bus}_if vif_t;
            `include "{bus}_agt_config.sv"
            `include "{bus}_trans.sv"
            `include "{bus}_sequencer.sv"
            `include "{bus}_driver.sv"
            `include "{bus}_monitor.sv"
            `include "{bus}_agent.sv"
            `include "{bus}_sequence.sv"
        endpackage
        `endif
        """)


def emit_generic_agt_config(handshake: dict) -> str:
    bus = handshake["bus_name"]
    return textwrap.dedent(f"""\
        class {bus}_agt_config extends uvm_object;
            `uvm_object_utils({bus}_agt_config)
            vif_t vif;
            uvm_active_passive_enum is_active = UVM_ACTIVE;
            function new(string name = "{bus}_agt_config"); super.new(name); endfunction
        endclass
        """)


def emit_generic_trans(handshake: dict) -> str:
    bus = handshake["bus_name"]
    addr_w = int(handshake["addr"]["width"]) if handshake.get("addr") else 1
    data_w = int(handshake["data"]["width"])
    return textwrap.dedent(f"""\
        class {bus}_trans extends uvm_sequence_item;
            rand logic [{addr_w-1}:0] addr;
            rand logic [{data_w-1}:0] data;
            rand bit                   write;
            logic      [{data_w-1}:0] rdata;
            `uvm_object_utils_begin({bus}_trans)
                `uvm_field_int(addr,  UVM_ALL_ON)
                `uvm_field_int(data,  UVM_ALL_ON)
                `uvm_field_int(write, UVM_ALL_ON)
                `uvm_field_int(rdata, UVM_ALL_ON | UVM_NOCOMPARE)
            `uvm_object_utils_end
            function new(string name = "{bus}_trans"); super.new(name); endfunction
        endclass
        """)


def emit_generic_sequencer(handshake: dict) -> str:
    bus = handshake["bus_name"]
    return textwrap.dedent(f"""\
        typedef uvm_sequencer#({bus}_trans) {bus}_sequencer;
        """)


def emit_generic_driver(handshake: dict) -> str:
    bus = handshake["bus_name"]
    return textwrap.dedent(f"""\
        // PLACEHOLDER driver. The scaffold sub-agent replaces the body of
        // `drive_one` with protocol-specific handshake logic per
        // bus_handshake.yaml.handshake.kind.
        class {bus}_driver extends uvm_driver #({bus}_trans);
            `uvm_component_utils({bus}_driver)
            vif_t vif;
            function new(string name, uvm_component parent = null);
                super.new(name, parent);
            endfunction
            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                if (!uvm_config_db#(vif_t)::get(this, "", "{bus}_vif", vif))
                    `uvm_fatal("CFG", "{bus}_vif missing")
            endfunction
            task run_phase(uvm_phase phase);
                {bus}_trans tr;
                forever begin
                    seq_item_port.get_next_item(tr);
                    drive_one(tr);
                    seq_item_port.item_done();
                end
            endtask
            // SUB-AGENT: implement protocol-specific bus drive here.
            task drive_one({bus}_trans tr);
            endtask
        endclass
        """)


def emit_generic_monitor(handshake: dict) -> str:
    bus = handshake["bus_name"]
    return textwrap.dedent(f"""\
        // PLACEHOLDER monitor. The scaffold sub-agent replaces the body of
        // `run_phase` with handshake-sampling logic per
        // bus_handshake.yaml.handshake.kind.
        class {bus}_monitor extends uvm_monitor;
            `uvm_component_utils({bus}_monitor)
            uvm_analysis_port #({bus}_trans) ap;
            vif_t vif;
            function new(string name, uvm_component parent = null);
                super.new(name, parent);
                ap = new("ap", this);
            endfunction
            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                if (!uvm_config_db#(vif_t)::get(this, "", "{bus}_vif", vif))
                    `uvm_fatal("CFG", "{bus}_vif missing")
            endfunction
            // SUB-AGENT: implement protocol-specific bus sample loop here.
            task run_phase(uvm_phase phase);
            endtask
        endclass
        """)


def emit_generic_agent(handshake: dict) -> str:
    bus = handshake["bus_name"]
    return textwrap.dedent(f"""\
        class {bus}_agent extends uvm_agent;
            `uvm_component_utils({bus}_agent)
            {bus}_agt_config cfg;
            {bus}_driver     drv;
            {bus}_monitor    mon;
            {bus}_sequencer  sqr;
            function new(string name, uvm_component parent = null);
                super.new(name, parent);
            endfunction
            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                if (!uvm_config_db#({bus}_agt_config)::get(this, "", "cfg", cfg))
                    `uvm_fatal("CFG", "{bus}_agt_config missing")
                uvm_config_db#(vif_t)::set(this, "*", "{bus}_vif", cfg.vif);
                mon = {bus}_monitor::type_id::create("mon", this);
                if (cfg.is_active == UVM_ACTIVE) begin
                    drv = {bus}_driver::type_id::create("drv", this);
                    sqr = {bus}_sequencer::type_id::create("sqr", this);
                end
            endfunction
            function void connect_phase(uvm_phase phase);
                if (cfg.is_active == UVM_ACTIVE)
                    drv.seq_item_port.connect(sqr.seq_item_export);
            endfunction
        endclass
        """)


def emit_generic_sequence(handshake: dict) -> str:
    bus = handshake["bus_name"]
    return textwrap.dedent(f"""\
        // PLACEHOLDER sequence library. The scaffold sub-agent expands this
        // with reset_seq, single_write_seq, single_read_seq bodies.
        class {bus}_sequence extends uvm_sequence #({bus}_trans);
            `uvm_object_utils({bus}_sequence)
            int unsigned n_transactions = 1;
            logic [{max(int(handshake['addr']['width']) if handshake.get('addr') else 1, 1)-1}:0] legal_addrs[$];
            function new(string name = "{bus}_sequence"); super.new(name); endfunction
            task body();
                {bus}_trans tr;
                repeat (n_transactions) begin
                    tr = {bus}_trans::type_id::create("tr");
                    start_item(tr);
                    if (!tr.randomize()) `uvm_fatal("RAND", "{bus}_trans randomize failed")
                    if (legal_addrs.size() != 0)
                        tr.addr = legal_addrs[$urandom_range(0, legal_addrs.size()-1)];
                    finish_item(tr);
                end
            endtask
        endclass
        """)


def _emit_generic_tb_api_primitives(handshake: dict) -> str:
    """Placeholder write/read bodies. Sub-agent rewrites these to drive
    the actual bus. They compile and `read` returns zero so the testbench
    elaborates; they are NOT a working bus."""
    has_addr = handshake.get("addr") is not None
    has_regs = handshake["register_semantics"] == "yes"
    clk = handshake["clock"]["name"]
    write_sig = "input logic [ADDR_W-1:0] addr, input logic [DATA_W-1:0] data" if has_addr else "input logic [DATA_W-1:0] data"
    read_sig = "input logic [ADDR_W-1:0] addr, output logic [DATA_W-1:0] data" if has_addr else "output logic [DATA_W-1:0] data"
    expect = textwrap.dedent(f"""\

        // expect_reg semantics match the built-in APB/AHB/AXI-Lite tb_api:
        // print `@0x<addr> = 0x<value>` on success (UVM_LOW), fatal on
        // mismatch. The sub-agent should preserve this success print.
        task automatic expect_reg(input logic [ADDR_W-1:0] addr,
                                  input logic [DATA_W-1:0] expected,
                                  input string tag = "EXPECT");
            logic [DATA_W-1:0] got;
            read(addr, got);
            if (got !== expected)
                `uvm_fatal(tag, $sformatf("expect_reg @0x%0h: got 0x%08h expected 0x%08h",
                    addr, got, expected))
            else
                `uvm_info(tag, $sformatf("@0x%0h = 0x%08h", addr, got), UVM_LOW)
        endtask
        """) if has_regs else ""
    return textwrap.dedent(f"""\
        // ====================================================================
        // tb_api primitives — PLACEHOLDER for generic bus.
        // The scaffold sub-agent (references/sub_agent_generic_scaffold.md)
        // replaces the bodies below with real bus drive/sample.
        // ====================================================================

        // Responder-side state. Sub-agent populates these from monitor
        // observations in master-direction generic mode.
        logic [ADDR_W-1:0] last_write_addr = '0;
        logic [DATA_W-1:0] last_write_data = '0;
        int unsigned       writes_observed = 0;
        int unsigned       reads_observed  = 0;

        function automatic void _require_vif();
            if (vif == null) `uvm_fatal("TB_API",
                "vif not set — call tb_api::set_vif(...) in top initial block")
        endfunction

        // PLACEHOLDER write. Sub-agent rewrites to drive the bus.
        task automatic write({write_sig});
            _require_vif();
            @(posedge vif.{clk});
        endtask

        // PLACEHOLDER read. Sub-agent rewrites to drive the bus.
        task automatic read({read_sig});
            _require_vif();
            @(posedge vif.{clk});
            data = '0;
        endtask

        // PLACEHOLDER responder-wait. Sub-agent rewrites against monitor.
        task automatic wait_for_write(input int unsigned timeout_cycles = 1000);
            int unsigned n = timeout_cycles;
            _require_vif();
            while (writes_observed == 0) begin
                @(posedge vif.{clk});
                if (--n == 0) `uvm_fatal("TB_API",
                    "timeout waiting for DUT-initiated write (placeholder)")
            end
        endtask
        {expect}
        """)


def _generic_scaffold_prompt(intake: dict, handshake: dict) -> str:
    """Audit artifact handed to the scaffold sub-agent. Records the inputs
    actually used by scaffold.py so the sub-agent can pick up from a
    known-state skeleton. The sub-agent appends an `## Assumptions made
    by sub-agent` section after running."""
    ip = intake["ip_name"]
    prefix = handshake["bus_name"]
    kind = handshake["handshake"]["kind"]
    reg_sem = handshake["register_semantics"]
    direction = handshake["direction"]
    return textwrap.dedent(f"""\
        # Generic-mode scaffold sub-agent prompt — {ip} ({prefix})

        Written by scaffold.py at Phase 4. The skeleton under `tb/{prefix}_agt_top/`,
        `tb/{prefix}_if.sv`, `tb/tb_api/`, and `top/{ip}_tb_top.sv` is a placeholder:
        files compile, but the bus drive/sample logic is empty. Your job is to
        fill in the bodies per the contract in `references/generic_bus.md`.

        ## Inputs used by scaffold.py
        - `bus_handshake.yaml`:
            bus_name           = {prefix}
            direction          = {direction}
            handshake.kind     = {kind}
            register_semantics = {reg_sem}
        - `rtl_discovery.yaml` (port names/widths)
        - `intake.yaml` (clock period, reset cycles, UVM version, etc.)

        ## Files the sub-agent must complete
        - `tb/{prefix}_if.sv`: add clocking blocks for driver/monitor sample timing
        - `tb/{prefix}_agt_top/{prefix}_driver.sv`: implement `drive_one`
        - `tb/{prefix}_agt_top/{prefix}_monitor.sv`: implement `run_phase` sample loop
        - `tb/{prefix}_agt_top/{prefix}_sequence.sv`: expand with reset/single_write/single_read sequences
        - `tb/tb_api/tb_api_primitives.svh`: rewrite `write`/`read`{('/`expect_reg`' if reg_sem == 'yes' else '')} bodies
        {'- `tb/ral/' + ip + '_' + prefix + '_adapter.sv`: replace placeholder reg2bus/bus2reg with real handshake drive' if reg_sem == 'yes' else ''}

        ## Forbidden
        - Editing `intake.yaml`, `rtl_discovery.yaml`, `bus_handshake.yaml`, or
          `spec_normalized/registers.yaml`.
        - Editing user RTL, user VIP source, or specs.
        - Changing `tb_api::write/read/expect_reg` task signatures.
        - Inventing control signals not in `bus_handshake.yaml` or
          `rtl_discovery.yaml`.

        ## Ambiguity policy
        When `bus_handshake.yaml` is silent on a detail, pick the narrower
        interpretation and append it to the section below.

        ## Assumptions made by sub-agent
        <!-- The sub-agent appends one bullet per ambiguity resolved. -->
        """)


def emit_generic_adapter(ip: str, handshake: dict) -> str:
    """Placeholder RAL adapter for generic mode. Sub-agent rewrites
    reg2bus/bus2reg to route through the generated driver."""
    prefix = handshake["bus_name"]
    return textwrap.dedent(f"""\
        `ifndef {ip.upper()}_{prefix.upper()}_ADAPTER_SV
        `define {ip.upper()}_{prefix.upper()}_ADAPTER_SV
        package {ip}_{prefix}_adapter_pkg;
            import uvm_pkg::*;
            import {prefix}_agt_pkg::*;
            `include "uvm_macros.svh"

            // PLACEHOLDER adapter. Sub-agent rewrites reg2bus/bus2reg
            // against the generated driver and the bus_handshake.yaml
            // handshake kind.
            class {ip}_{prefix}_adapter extends uvm_reg_adapter;
                `uvm_object_utils({ip}_{prefix}_adapter)
                function new(string name = "{ip}_{prefix}_adapter");
                    super.new(name);
                    supports_byte_enable = 0;
                    provides_responses   = 0;
                endfunction
                virtual function uvm_sequence_item reg2bus(const ref uvm_reg_bus_op rw);
                    {prefix}_trans tr = {prefix}_trans::type_id::create("tr");
                    tr.write = (rw.kind == UVM_WRITE);
                    tr.addr  = rw.addr;
                    tr.data  = rw.data;
                    return tr;
                endfunction
                virtual function void bus2reg(uvm_sequence_item bus_item,
                                              ref uvm_reg_bus_op rw);
                    {prefix}_trans tr;
                    if (!$cast(tr, bus_item)) `uvm_fatal("ADAPTER", "bad bus item")
                    rw.kind = tr.write ? UVM_WRITE : UVM_READ;
                    rw.addr = tr.addr;
                    rw.data = tr.write ? tr.data : tr.rdata;
                    rw.status = UVM_IS_OK;
                endfunction
            endclass
        endpackage
        `endif
        """)


def emit_external_vip_f(vip: dict[str, Any]) -> str:
    lines = [f"+incdir+{p}" for p in vip["incdirs"]]
    lines.extend(str(p) for p in vip["compile_units"])
    return "\n".join(lines) + "\n"


def _as_int(v: Any) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return int(v, 0)
    return int(v)


def _sv_id(raw: str) -> str:
    out = re.sub(r"\W", "_", str(raw))
    if not out or re.match(r"\d", out):
        out = f"_{out}"
    return out


def _field_range(bits: Any) -> tuple[int, int]:
    text = str(bits).strip().strip('"')
    if ":" in text:
        a, b = [int(x, 0) for x in text.split(":", 1)]
        lo, hi = min(a, b), max(a, b)
        return lo, hi - lo + 1
    bit = int(text, 0)
    return bit, 1


def _same_reg_shape(a: dict, b: dict) -> bool:
    if _as_int(a["width"]) != _as_int(b["width"]):
        return False
    if a.get("access") != b.get("access"):
        return False
    af = a.get("fields", [])
    bf = b.get("fields", [])
    if len(af) != len(bf):
        return False
    for fa, fb in zip(af, bf):
        if (fa.get("name"), fa.get("bits"), fa.get("access"), fa.get("reset")) != (
            fb.get("name"), fb.get("bits"), fb.get("access"), fb.get("reset")
        ):
            return False
    if _as_int(a.get("reset", 0)) != _as_int(b.get("reset", 0)):
        return False
    return True


def _array_base(name: str) -> tuple[str, int] | None:
    m = re.match(r"^(.+?)(\d+)$", name)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def _group_ral_regs(regs: list[dict]) -> list[dict]:
    """Fold KEY0..N style runs into fixed-size RAL arrays."""
    used: set[int] = set()
    groups: list[dict] = []
    explicit_array_groups: dict[int, dict] = {}
    for idx, reg in enumerate(regs):
        if reg.get("array_of") not in (None, "", "null"):
            count = _as_int(reg["array_of"])
            stride = _as_int(reg.get("stride", _as_int(reg["width"]) // 8))
            explicit_array_groups[idx] = {
                "kind": "array",
                "name": reg["name"],
                "reg": reg,
                "members": [reg],
                "count": count,
                "stride": stride,
            }

    by_base: dict[str, list[tuple[int, int, dict]]] = {}
    for idx, reg in enumerate(regs):
        if idx in explicit_array_groups:
            continue
        parsed = _array_base(str(reg["name"]))
        if parsed:
            base, elem = parsed
            by_base.setdefault(base, []).append((elem, idx, reg))

    array_indices: set[int] = set()
    array_groups: dict[int, dict] = {}
    for base, entries in by_base.items():
        entries = sorted(entries)
        if len(entries) < 2 or entries[0][0] != 0:
            continue
        first = entries[0][2]
        stride = _as_int(entries[1][2]["offset"]) - _as_int(first["offset"])
        if stride <= 0:
            continue
        contiguous = True
        for pos, (elem, _, reg) in enumerate(entries):
            if elem != pos:
                contiguous = False
                break
            if _as_int(reg["offset"]) != _as_int(first["offset"]) + pos * stride:
                contiguous = False
                break
            if not _same_reg_shape(first, reg):
                contiguous = False
                break
        if not contiguous:
            continue
        idxs = [idx for _, idx, _ in entries]
        group = {
            "kind": "array",
            "name": base,
            "reg": first,
            "members": [reg for _, _, reg in entries],
            "count": len(entries),
            "stride": stride,
        }
        for idx in idxs:
            array_groups[idx] = group
            array_indices.add(idx)

    for idx, reg in enumerate(regs):
        if idx in used:
            continue
        if idx in explicit_array_groups:
            groups.append(explicit_array_groups[idx])
            used.add(idx)
            continue
        if idx in array_indices:
            group = array_groups[idx]
            if idx == min(i for i, g in array_groups.items() if g is group):
                groups.append(group)
                used.update(i for i, g in array_groups.items() if g is group)
            continue
        groups.append({"kind": "single", "name": reg["name"], "reg": reg, "members": [reg]})
        used.add(idx)
    return groups


def _alias_names(regs: list[dict]) -> set[str]:
    by_offset: dict[int, list[dict]] = {}
    aliases: set[str] = set()
    for reg in regs:
        if reg.get("aliased_by") not in (None, "", "null"):
            aliases.add(str(reg["name"]))
        by_offset.setdefault(_as_int(reg["offset"]), []).append(reg)
    for same_addr in by_offset.values():
        if len(same_addr) <= 1:
            continue
        accesses = sorted(str(r.get("access", "")).upper() for r in same_addr)
        # RO+WO at the same address is a disjoint access pair, not bank aliasing.
        disjoint_ro_wo = len(same_addr) == 2 and accesses == ["RO", "WO"]
        if not disjoint_ro_wo:
            aliases.update(str(r["name"]) for r in same_addr)
    return aliases


def _reg_class_name(ip: str, name: str) -> str:
    return f"{_sv_id(ip).upper()}_{_sv_id(name).upper()}_reg"


def emit_ral_block(ip: str, regs: list[dict]) -> str:
    """Generate a concrete UVM RAL block and a frontdoor smoke sequence."""
    aliases = _alias_names(regs)
    groups = _group_ral_regs(regs)

    reg_classes: list[str] = []
    declared: set[str] = set()
    for group in groups:
        reg = group["reg"]
        name = str(group["name"])
        cls = _reg_class_name(ip, name)
        if cls in declared:
            continue
        declared.add(cls)
        width = _as_int(reg["width"])
        reg_classes += [
            f"    class {cls} extends uvm_reg;",
            f"        `uvm_object_utils({cls})",
        ]
        used_fields: set[str] = set()
        field_ids: list[tuple[str, dict]] = []
        for f in reg.get("fields", []):
            fid = f"f_{_sv_id(f['name'])}"
            if fid in used_fields:
                fid = f"{fid}_{len(used_fields)}"
            used_fields.add(fid)
            field_ids.append((fid, f))
            reg_classes.append(f"        rand uvm_reg_field {fid};")
        reg_classes += [
            f"",
            f'        function new(string name="{cls}");',
            f"            super.new(name, {width}, UVM_NO_COVERAGE);",
            f"        endfunction",
            f"",
            f"        virtual function void build();",
        ]
        for fid, f in field_ids:
            lsb, fwidth = _field_range(f["bits"])
            access = str(f.get("access", reg.get("access", "RW"))).upper()
            reset = _as_int(f.get("reset", 0))
            is_rand = 1 if "W" in access else 0
            reg_classes += [
                f'            {fid} = uvm_reg_field::type_id::create("{_sv_id(f["name"])}");',
                f"            {fid}.configure(this, {fwidth}, {lsb}, \"{access}\",",
                f"                0, {width}'h{reset:X}, 1, {is_rand}, 1);",
            ]
        reg_classes += [
            f"        endfunction",
            f"    endclass",
            f"",
        ]

    block_lines = [
        f"    class {ip}_reg_block extends uvm_reg_block;",
        f"        `uvm_object_utils({ip}_reg_block)",
    ]
    for group in groups:
        name = _sv_id(group["name"])
        cls = _reg_class_name(ip, group["name"])
        if group["kind"] == "array":
            block_lines.append(f"        rand {cls} {name}[{group['count']}];")
        else:
            block_lines.append(f"        rand {cls} {name};")
    block_lines += [
        f"",
        f'        function new(string name="{ip}_reg_block");',
        f"            super.new(name, UVM_NO_COVERAGE);",
        f"        endfunction",
        f"",
        f"        virtual function void build();",
        f'            default_map = create_map("default_map", 0, 4, UVM_LITTLE_ENDIAN);',
    ]
    for group in groups:
        name = _sv_id(group["name"])
        reg = group["reg"]
        cls = _reg_class_name(ip, group["name"])
        access = str(reg.get("access", "RW")).upper()
        if group["kind"] == "array":
            base = _as_int(reg["offset"])
            stride = _as_int(group["stride"])
            block_lines += [
                f"            foreach ({name}[i]) begin",
                f'                {name}[i] = {cls}::type_id::create($sformatf("{name}[%0d]", i));',
                f"                {name}[i].configure(this, null, \"\");",
                f"                {name}[i].build();",
                f"                default_map.add_reg({name}[i], 'h{base:X} + i*'h{stride:X}, \"{access}\");",
                f"            end",
            ]
        else:
            offset = _as_int(reg["offset"])
            block_lines += [
                f'            {name} = {cls}::type_id::create("{name}");',
                f"            {name}.configure(this, null, \"\");",
                f"            {name}.build();",
                f"            default_map.add_reg({name}, 'h{offset:X}, \"{access}\");",
            ]
            if str(group["name"]) in aliases:
                block_lines.append(
                    f'            uvm_resource_db#(bit)::set({{"REG::", {name}.get_full_name()}}, "NO_REG_ACCESS_TEST", 1, this);'
                )
    block_lines += [
        f"            lock_model();",
        f"        endfunction",
        f"    endclass",
        f"",
    ]

    seq_lines = [
        f"    class {ip}_ral_access_seq extends uvm_sequence;",
        f"        `uvm_object_utils({ip}_ral_access_seq)",
        f"        {ip}_reg_block ral;",
        f"",
        f'        function new(string name="{ip}_ral_access_seq");',
        f"            super.new(name);",
        f"        endfunction",
        f"",
        f"        task body();",
        f"            uvm_status_e status;",
        f"            uvm_reg_data_t data;",
        f'            if (ral == null) `uvm_fatal("RAL_ACCESS", "ral handle is null")',
        f'            `uvm_info("RAL_ACCESS", "starting generated RAL frontdoor checks", UVM_LOW)',
    ]
    for group in groups:
        reg = group["reg"]
        name = _sv_id(group["name"])
        access = str(reg.get("access", "RW")).upper()
        aliased = any(str(m["name"]) in aliases for m in group["members"])
        if aliased:
            for member in group["members"]:
                seq_lines.append(
                    f'            `uvm_info("RAL_ACCESS", "Skipping aliased register {member["name"]}", UVM_LOW)'
                )
            continue
        if group["kind"] == "array":
            seq_lines += [
                f"            foreach (ral.{name}[i]) begin",
                f'                `uvm_info("RAL_ARRAY", $sformatf("{name}[%0d]", i), UVM_LOW)',
            ]
            prefix = f"ral.{name}[i]"
            suffix = "            end"
        else:
            prefix = f"ral.{name}"
            suffix = ""
        if "R" in access:
            seq_lines += [
                f"                {prefix}.read(status, data, UVM_FRONTDOOR);",
                f'                if (status != UVM_IS_OK) `uvm_error("RAL_ACCESS", "{name} read failed")',
            ]
        if "W" in access:
            seq_lines += [
                f"                data = {prefix}.get();",
                f"                {prefix}.write(status, data, UVM_FRONTDOOR);",
                f'                if (status != UVM_IS_OK) `uvm_error("RAL_ACCESS", "{name} write failed")',
            ]
        if suffix:
            seq_lines.append(suffix)
    seq_lines += [
        f'            `uvm_info("RAL_ACCESS", "completed generated RAL frontdoor checks", UVM_LOW)',
        f"        endtask",
        f"    endclass",
    ]

    lines = [
        f"`ifndef {ip.upper()}_REG_BLOCK_SV",
        f"`define {ip.upper()}_REG_BLOCK_SV",
        f"// gen-tb generated full RAL from registers.yaml.",
        f"package {ip}_ral_pkg;",
        f"    import uvm_pkg::*;",
        f'    `include "uvm_macros.svh"',
        f"",
        *reg_classes,
        *block_lines,
        *seq_lines,
        f"endpackage",
        f"`endif",
    ]
    return "\n".join(lines) + "\n"


def emit_apb_adapter(ip: str) -> str:
    return textwrap.dedent(f"""\
        `ifndef {ip.upper()}_APB_ADAPTER_SV
        `define {ip.upper()}_APB_ADAPTER_SV
        package {ip}_apb_adapter_pkg;
            import uvm_pkg::*;
            import tb_api::*;
            import apb_agt_pkg::*;
            `include "uvm_macros.svh"

            class {ip}_apb_adapter extends uvm_reg_adapter;
                `uvm_object_utils({ip}_apb_adapter)

                function new(string name = "{ip}_apb_adapter");
                    super.new(name);
                    supports_byte_enable = 0;
                    provides_responses   = 0;
                endfunction

                virtual function uvm_sequence_item reg2bus(const ref uvm_reg_bus_op rw);
                    apb_trans t = apb_trans::type_id::create("t");
                    t.addr  = rw.addr[ADDR_W-1:0];
                    t.data  = rw.data[DATA_W-1:0];
                    t.write = (rw.kind == UVM_WRITE);
                    return t;
                endfunction

                virtual function void bus2reg(uvm_sequence_item bus_item, ref uvm_reg_bus_op rw);
                    apb_trans t;
                    if (!$cast(t, bus_item)) begin
                        `uvm_error("RAL_ADAPTER", "bus_item is not apb_trans")
                        rw.status = UVM_NOT_OK;
                        return;
                    end
                    rw.kind   = t.write ? UVM_WRITE : UVM_READ;
                    rw.addr   = t.addr;
                    rw.data   = t.write ? t.data : t.rdata;
                    rw.status = t.slverr ? UVM_NOT_OK : UVM_IS_OK;
                endfunction
            endclass
        endpackage
        `endif
        """)


def emit_ahb_adapter(ip: str) -> str:
    return textwrap.dedent(f"""\
        `ifndef {ip.upper()}_AHB_ADAPTER_SV
        `define {ip.upper()}_AHB_ADAPTER_SV
        package {ip}_ahb_adapter_pkg;
            import uvm_pkg::*;
            import tb_api::*;
            import ahb_agt_pkg::*;
            `include "uvm_macros.svh"

            class {ip}_ahb_adapter extends uvm_reg_adapter;
                `uvm_object_utils({ip}_ahb_adapter)

                function new(string name = "{ip}_ahb_adapter");
                    super.new(name);
                    supports_byte_enable = 0;
                    provides_responses   = 0;
                endfunction

                virtual function uvm_sequence_item reg2bus(const ref uvm_reg_bus_op rw);
                    ahb_trans t = ahb_trans::type_id::create("t");
                    t.addr  = rw.addr[ADDR_W-1:0];
                    t.data  = rw.data[DATA_W-1:0];
                    t.write = (rw.kind == UVM_WRITE);
                    return t;
                endfunction

                virtual function void bus2reg(uvm_sequence_item bus_item, ref uvm_reg_bus_op rw);
                    ahb_trans t;
                    if (!$cast(t, bus_item)) begin
                        `uvm_error("RAL_ADAPTER", "bus_item is not ahb_trans")
                        rw.status = UVM_NOT_OK;
                        return;
                    end
                    rw.kind   = t.write ? UVM_WRITE : UVM_READ;
                    rw.addr   = t.addr;
                    rw.data   = t.write ? t.data : t.rdata;
                    rw.status = t.resp ? UVM_NOT_OK : UVM_IS_OK;
                endfunction
            endclass
        endpackage
        `endif
        """)


def emit_axi_lite_adapter(ip: str) -> str:
    return textwrap.dedent(f"""\
        `ifndef {ip.upper()}_AXI_LITE_ADAPTER_SV
        `define {ip.upper()}_AXI_LITE_ADAPTER_SV
        package {ip}_axi_lite_adapter_pkg;
            import uvm_pkg::*;
            import tb_api::*;
            import axi_lite_agt_pkg::*;
            `include "uvm_macros.svh"

            class {ip}_axi_lite_adapter extends uvm_reg_adapter;
                `uvm_object_utils({ip}_axi_lite_adapter)

                function new(string name = "{ip}_axi_lite_adapter");
                    super.new(name);
                    supports_byte_enable = 0;
                    provides_responses   = 0;
                endfunction

                virtual function uvm_sequence_item reg2bus(const ref uvm_reg_bus_op rw);
                    axi_lite_trans t = axi_lite_trans::type_id::create("t");
                    t.addr  = rw.addr[ADDR_W-1:0];
                    t.data  = rw.data[DATA_W-1:0];
                    t.write = (rw.kind == UVM_WRITE);
                    return t;
                endfunction

                virtual function void bus2reg(uvm_sequence_item bus_item, ref uvm_reg_bus_op rw);
                    axi_lite_trans t;
                    if (!$cast(t, bus_item)) begin
                        `uvm_error("RAL_ADAPTER", "bus_item is not axi_lite_trans")
                        rw.status = UVM_NOT_OK;
                        return;
                    end
                    rw.kind   = t.write ? UVM_WRITE : UVM_READ;
                    rw.addr   = t.addr;
                    rw.data   = t.write ? t.data : t.rdata;
                    rw.status = (t.resp != 2'b00) ? UVM_NOT_OK : UVM_IS_OK;
                endfunction
            endclass
        endpackage
        `endif
        """)


def emit_dpi_ref_pkg(ip: str, dpi: dict) -> str:
    sv_imports = []
    for f in dpi["dpi_exports"]:
        args_lines = []
        for a in f["args"]:
            packing = a.get("packing", "")
            args_lines.append(f"        {a['dir']:6s} {a['type']} {a['name']} {packing}")
        body = ",\n".join(args_lines)
        sv_imports.append(
            f'    import "DPI-C" function void {f["sv_name"]}(\n{body}\n    );'
        )
    body = "\n\n".join(sv_imports)
    return textwrap.dedent(f"""\
        `ifndef {ip.upper()}_REF_PKG_SV
        `define {ip.upper()}_REF_PKG_SV
        // Auto-generated from intake.yaml dpi_exports. Do not hand-edit.
        package {ip}_ref_pkg;
        {body}
        endpackage
        `endif
        """)


def emit_dpi_proto_h(ip: str, dpi: dict) -> str:
    type_map = {
        ("bit [31:0]", True):  "const uint32_t {name}[]",
        ("bit [31:0]", False): "uint32_t {name}[]",
        ("bit [7:0]",  True):  "const uint8_t {name}[]",
        ("bit [7:0]",  False): "uint8_t {name}[]",
    }
    protos = []
    for f in dpi["dpi_exports"]:
        c_args = []
        for a in f["args"]:
            is_input = a["dir"] == "input"
            key = (a["type"], is_input)
            if key in type_map:
                c_args.append(type_map[key].format(name=a["name"]))
            else:
                c_args.append(f"/* TODO: type {a['type']} */ void *{a['name']}")
        protos.append(f"void {f['c_name']}({', '.join(c_args)});")
    body = "\n".join(protos)
    return textwrap.dedent(f"""\
        /* Auto-generated from intake.yaml dpi_exports. Do not hand-edit. */
        #ifndef {ip.upper()}_DPI_PROTO_H
        #define {ip.upper()}_DPI_PROTO_H
        #include <stdint.h>
        #ifdef __cplusplus
        extern "C" {{
        #endif
        {body}
        #ifdef __cplusplus
        }}
        #endif
        #endif
        """)


def emit_test_pkg(intake: dict, regs: list[dict], dpi: dict | None, handshake: dict | None = None) -> str:
    ip = intake["ip_name"]
    bus = _bus(intake)
    addr_w = _addr_width(intake, handshake)
    direction = _direction(intake, handshake)
    has_ral = _bus_has_ral(intake, handshake)
    sanity_addr, sanity_value = _pick_sanity_target(regs) if has_ral else (0, 0)
    clk, rst = _clk_rst_names(bus, handshake)
    prefix = _bus_prefix(intake, handshake)
    agent_cls = f"{prefix}_agent"
    cfg_cls = f"{prefix}_agt_config"
    seq_cls = f"{prefix}_sequence"
    adapter_cls = f"{ip}_{prefix}_adapter"
    rst_released = "1'b0" if _reset_polarity(handshake) == "high" and bus == "generic" else "1'b1"

    if has_ral:
        addr_consts = []
        name_w = max((len(r["name"]) for r in regs), default=1)
        for r in regs:
            off = r["offset"] if isinstance(r["offset"], int) else int(r["offset"], 16)
            addr_consts.append(
                f"localparam logic [{addr_w-1}:0] ADDR_{r['name']:<{name_w}} = {addr_w}'h{off:03X};"
            )
        addr_consts_str = "\n".join(addr_consts)

        readable_regs = regs[:8]
        reg_reads = "\n".join(
            f'    tb_api::read(ADDR_{r["name"]}, rd); '
            f'`uvm_info(tag, $sformatf("{r["name"]} = 0x%08h", rd), UVM_LOW)'
            for r in readable_regs
        )
        legal_addr_list = ", ".join(f"ADDR_{r['name']}" for r in readable_regs)

        helper_tasks = (
            "task automatic run_reg_access_reads(input string tag);\n"
            "    logic [tb_api::DATA_W-1:0] rd;\n"
            "    `uvm_info(tag, \"reading all registers\", UVM_LOW)\n"
            f"{reg_reads}\n"
            "endtask\n"
        )
    else:
        addr_consts_str = ""
        legal_addr_list = ""
        helper_tasks = ""

    if direction == "master":
        sanity_test = textwrap.dedent(f"""\
            class {ip}_sanity_test extends uvm_test;
                `uvm_component_utils({ip}_sanity_test)
                {agent_cls}      agent;
                {cfg_cls} cfg;
                function new(string n="{ip}_sanity_test", uvm_component p=null); super.new(n,p); endfunction
                function void build_phase(uvm_phase phase);
                    super.build_phase(phase);
                    cfg = {cfg_cls}::type_id::create("cfg");
                    cfg.vif = tb_api::vif;
                    uvm_config_db#({cfg_cls})::set(this, "agent", "cfg", cfg);
                    agent = {agent_cls}::type_id::create("agent", this);
                endfunction
                task run_phase(uvm_phase phase);
                    phase.raise_objection(this);
                    wait (tb_api::vif.{rst} === {rst_released});
                    repeat (4) @(posedge tb_api::vif.{clk});
                    `uvm_info("SANITY", "responder is alive; idling 32 cycles", UVM_LOW)
                    repeat (32) @(posedge tb_api::vif.{clk});
                    phase.drop_objection(this);
                endtask
            endclass
            """)
    elif not has_ral:
        # Slave direction without RAL (generic + register_semantics: no):
        # tb_api::expect_reg is not generated in this mode and there is no
        # readable register to assert against, so SKILL.md Phase 6 specifies
        # only <ip>_responder_smoke_test for this combination. Emit nothing
        # for sanity here; sv_list also omits it.
        sanity_test = ""
    else:
        sanity_test = textwrap.dedent(f"""\
            class {ip}_sanity_test extends uvm_test;
                `uvm_component_utils({ip}_sanity_test)
                function new(string n="{ip}_sanity_test", uvm_component p=null); super.new(n,p); endfunction
                task run_phase(uvm_phase phase);
                    phase.raise_objection(this);
                    wait (tb_api::vif.{rst} === {rst_released});
                    repeat (4) @(posedge tb_api::vif.{clk});
                    tb_api::expect_reg({addr_w}'h{sanity_addr:03X}, 32'h{sanity_value:08X}, "SANITY");
                    phase.drop_objection(this);
                endtask
            endclass
            """)

    random_seq_test = textwrap.dedent(f"""\
        class {ip}_random_seq_test extends uvm_test;
            `uvm_component_utils({ip}_random_seq_test)
            {agent_cls}      agent;
            {cfg_cls} cfg;

            function new(string n="{ip}_random_seq_test", uvm_component p=null);
                super.new(n, p);
            endfunction

            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                cfg = {cfg_cls}::type_id::create("cfg");
                cfg.vif = tb_api::vif;
                uvm_config_db#({cfg_cls})::set(this, "agent", "cfg", cfg);
                agent = {agent_cls}::type_id::create("agent", this);
            endfunction

            task run_phase(uvm_phase phase);
                {seq_cls} seq;
                phase.raise_objection(this);
                wait (tb_api::vif.{rst} === {rst_released});
                repeat (4) @(posedge tb_api::vif.{clk});
                seq = {seq_cls}::type_id::create("seq");
                seq.n_transactions = 100;
                seq.legal_addrs = '{{{legal_addr_list}}};
                seq.start(agent.sqr);
                run_reg_access_reads("RANDOM_SEQ");
                phase.drop_objection(this);
            endtask
        endclass
        """)

    smoke_test = ""
    if dpi:
        smoke_test = "// DPI smoke test generation: see references/refm_dpi.md.\n"
        smoke_test += "// v1.1 stub — hand-write the smoke test for now using tb_api::wait_status_flag.\n"

    vip_source = _vip_source(intake) if bus != "generic" else "generate_fresh"
    agent_import = f"import {prefix}_agt_pkg::*;" if vip_source == "generate_fresh" else ""
    ral_import = f"import {ip}_ral_pkg::*;" if has_ral else ""
    adapter_import = (
        f"import {ip}_{prefix}_adapter_pkg::*;"
        if vip_source == "generate_fresh" and has_ral
        else ""
    )
    random_seq_body = random_seq_test if (vip_source == "generate_fresh" and has_ral) else ""
    if not has_ral:
        # DUT-as-master: emit responder_smoke_test instead of reg_access_test.
        reg_access_test = textwrap.dedent(f"""\
            class {ip}_responder_smoke_test extends uvm_test;
                `uvm_component_utils({ip}_responder_smoke_test)
                {agent_cls}      agent;
                {cfg_cls} cfg;
                function new(string n="{ip}_responder_smoke_test", uvm_component p=null); super.new(n,p); endfunction
                function void build_phase(uvm_phase phase);
                    super.build_phase(phase);
                    cfg = {cfg_cls}::type_id::create("cfg");
                    cfg.vif = tb_api::vif;
                    uvm_config_db#({cfg_cls})::set(this, "agent", "cfg", cfg);
                    agent = {agent_cls}::type_id::create("agent", this);
                endfunction
                task run_phase(uvm_phase phase);
                    phase.raise_objection(this);
                    wait (tb_api::vif.{rst} === {rst_released});
                    repeat (4) @(posedge tb_api::vif.{clk});
                    `uvm_info("RESPONDER", "waiting for DUT-initiated write", UVM_LOW)
                    tb_api::wait_for_write(2000);
                    `uvm_info("RESPONDER", $sformatf("observed write @0x%0h = 0x%08h",
                        tb_api::last_write_addr, tb_api::last_write_data), UVM_LOW)
                    phase.drop_objection(this);
                endtask
            endclass
            """)
    elif vip_source == "generate_fresh":
        reg_access_test = textwrap.dedent(f"""\
            class {ip}_reg_access_test extends uvm_test;
                `uvm_component_utils({ip}_reg_access_test)
                {agent_cls}         agent;
                {cfg_cls}    cfg;
                {ip}_reg_block    ral;
                {adapter_cls}  adapter;

                function new(string n="{ip}_reg_access_test", uvm_component p=null);
                    super.new(n,p);
                endfunction

                function void build_phase(uvm_phase phase);
                    super.build_phase(phase);
                    cfg = {cfg_cls}::type_id::create("cfg");
                    cfg.vif = tb_api::vif;
                    uvm_config_db#({cfg_cls})::set(this, "agent", "cfg", cfg);
                    agent = {agent_cls}::type_id::create("agent", this);
                    ral = {ip}_reg_block::type_id::create("ral");
                    ral.build();
                    adapter = {adapter_cls}::type_id::create("adapter");
                endfunction

                task run_phase(uvm_phase phase);
                    {ip}_ral_access_seq seq;
                    phase.raise_objection(this);
                    wait (tb_api::vif.{rst} === {rst_released});
                    repeat (4) @(posedge tb_api::vif.{clk});
                    ral.default_map.set_sequencer(agent.sqr, adapter);
                    ral.default_map.set_auto_predict(1);
                    seq = {ip}_ral_access_seq::type_id::create("seq");
                    seq.ral = ral;
                    seq.start(null);
                    phase.drop_objection(this);
                endtask
            endclass
            """)
    else:
        reg_access_test = textwrap.dedent(f"""\
            class {ip}_reg_access_test extends uvm_test;
                `uvm_component_utils({ip}_reg_access_test)
                function new(string n="{ip}_reg_access_test", uvm_component p=null); super.new(n,p); endfunction
                task run_phase(uvm_phase phase);
                    phase.raise_objection(this);
                    wait (tb_api::vif.{rst} === {rst_released});
                    repeat (4) @(posedge tb_api::vif.{clk});
                    run_reg_access_reads("REG_ACCESS");
                    phase.drop_objection(this);
                endtask
            endclass
            """)

    ref_import = f"import {ip}_ref_pkg::*;" if dpi else ""
    import_lines = [
        line for line in (
            "import uvm_pkg::*;",
            agent_import,
            ral_import,
            adapter_import,
            ref_import,
        ) if line
    ]
    import_block = "\n".join(import_lines) + "\n" + '`include "uvm_macros.svh"'

    blocks = [b.rstrip() for b in (
        import_block,
        addr_consts_str,
        helper_tasks,
        sanity_test,
        reg_access_test,
        random_seq_body,
        smoke_test,
    ) if b.strip()]
    body = textwrap.indent("\n\n".join(blocks) + "\n", "    ")

    return (
        f"`ifndef {ip.upper()}_PKG_SV\n"
        f"`define {ip.upper()}_PKG_SV\n"
        f"package {ip}_pkg;\n"
        f"{body}"
        f"endpackage\n"
        f"`endif\n"
    )


def emit_sv_list(ip: str, vip_source: str, has_ral: bool = True,
                 direction: str = "slave") -> str:
    if not has_ral:
        # SKILL.md Phase 6: register_semantics:no with slave direction runs
        # ONLY <ip>_responder_smoke_test (sanity has nothing to assert —
        # tb_api::expect_reg is not even generated in no-register mode).
        # The master-direction lane keeps the alive-style sanity it has
        # historically passed (see ahb-simple-master eval).
        if direction == "slave":
            return f"{ip}_responder_smoke_test\n"
        return f"{ip}_sanity_test\n{ip}_responder_smoke_test\n"
    lines = [f"{ip}_sanity_test", f"{ip}_reg_access_test"]
    if vip_source == "generate_fresh":
        lines.append(f"{ip}_random_seq_test")
    return "\n".join(lines) + "\n"


# Verbatim from references/generic_bus.md — generated CLAUDE.md must carry
# this in generic mode (the explicit hand-off to the human reviewer).
_GENERIC_REVIEW_CHECKLIST = """\
## Generic-mode review checklist
This testbench was generated in generic-bus mode. The skill cannot
verify protocol correctness it was never taught. Before relying on
this tb, review:

- [ ] Driver setup/hold against spec (especially req_ack and strobe
      modes — verify data is stable on the sampling cycle).
- [ ] Monitor sample timing matches the spec's data-valid window.
- [ ] Reset deassert cycle count matches DUT expectation.
- [ ] tb_api::write/read behavior on back-to-back transactions.
- [ ] If register_semantics: yes — spot-check 1-2 RW registers have
      correct addr / width / reset wired through RAL.
- [ ] Read the assumption list in
      work/_gen_audit/generic_bus_scaffold_prompt.md — each entry is
      a place the sub-agent picked the narrower interpretation.
"""


def emit_unresolved_md(ip: str) -> str:
    """Phase 7 hand-off artifact. scaffold.py seeds it so it is *always*
    present (per references/directory_layout.md); Phase 5/6/7 append any
    compile-fix, runtime-fix, or out-of-scope items that remain open."""
    return textwrap.dedent(f"""\
        # {ip} — Unresolved Items

        > Phase 7 hand-off artifact. Always present; may be empty.
        > Seeded by scaffold.py — Phase 5 (compile-fix), Phase 6
        > (runtime-fix), and Phase 7 append anything left open here.

        _No unresolved items recorded at scaffold time._
        """)


def emit_claude_md(ip: str, intake: dict, rtl: dict, handshake: dict | None,
                   bus: str, direction: str, vip_source: str,
                   vip_reuse_level: str, has_ral: bool,
                   axi_full_signature: bool) -> str:
    """Generated <ip>/CLAUDE.md — Phase 7 hand-off for future agent
    sessions. Shape follows references/generated_claude_md.md."""
    if bus == "generic":
        assert handshake is not None
        kind = handshake["handshake"]["kind"]
        bus_desc = (f"generic ({handshake['bus_name']}, {direction}, "
                    f"handshake.kind={kind})")
    elif bus == "apb":
        bus_desc = "APB slave"
    elif bus == "ahb":
        bus_desc = f"AHB-Lite {direction}"
    else:
        bus_desc = f"AXI4-Lite {direction}"
        if axi_full_signature:
            bus_desc += " (degraded mode — full-AXI ports, single-beat only)"

    refm = intake.get("ref_model_language", "skip")
    rtl_state = intake.get("rtl_state", "found_in_place")
    reuse_disp = vip_reuse_level if vip_source == "reuse_my_vip" else "n/a"

    tests = [f"{ip}_sanity_test"]
    if has_ral:
        tests.append(f"{ip}_reg_access_test")
        if vip_source == "generate_fresh":
            tests.append(f"{ip}_random_seq_test")
    else:
        tests.append(f"{ip}_responder_smoke_test")

    lines = [
        f"# {ip} Generated Testbench",
        "",
        "This directory was generated by the `gen-tb` skill.",
        "",
        "## Ownership",
        "",
        "- User-owned, do not edit without explicit request:",
        "  - `rtl/`",
        "  - `ref_model/`",
        "  - `vip/`",
        "  - `spec/`",
        "- Generated and safe to regenerate:",
        "  - `script/`",
        "  - `top/`",
        "  - `tb/`",
        "  - `test/`",
        "  - `work/_gen_audit/`",
        "",
    ]
    if rtl_state == "generated_stub":
        lines += [
            f"`rtl/{ip}_stub.sv` is a placeholder stub, **not** a golden "
            "implementation.",
            "",
        ]
    sims = _simulators(intake)
    sim_disp = ", ".join(s.upper() if s == "vcs" else s.capitalize() for s in sims)
    # `script/makefile` is always present (either VCS-native, or a shim
    # that includes makefile_questa when Questa is the only simulator).
    make_prefix = "make"
    lines += [
        "## Current Configuration",
        "",
        f"- IP: `{ip}`",
        f"- Bus: `{bus_desc}`",
        f"- Simulator(s): `{sim_disp}`" + (
            "  — Questa flow was generated statically; gen-tb has no Questa "
            "install to validate it. Verify locally." if "questa" in sims else ""
        ),
        f"- UVM: `{intake.get('uvm_version', '1.2')}`",
        f"- Bus VIP source: `{vip_source}`",
        f"- External VIP reuse level: `{reuse_disp}`",
        f"- Reference model: `{refm}`",
        "",
        "## Commands",
        "",
        "```bash",
        f"cd {ip}/script",
        "source setup.sh",
        f"{make_prefix} comp",
    ]
    lines += [f"{make_prefix} all SV_CASE={t}" for t in tests]
    if "vcs" in sims and "questa" in sims:
        lines += [
            "# Switch to Questa instead:",
            f"make -f makefile_questa all SV_CASE={tests[0]}",
        ]
    lines += [
        "```",
        "",
        "## Important Files",
        "",
        "- `work/_gen_audit/intake.yaml`",
        "- `work/_gen_audit/rtl_discovery.yaml`",
        "- `work/_gen_audit/spec_normalized/registers.yaml`",
        "- `work/_gen_audit/spec_normalized/behavior.md`",
        "- `work/_gen_audit/spec_normalized/parse_report.md`",
        "- `work/_gen_audit/scaffold_audit.json`",
        "- `work/_gen_audit/sanity_result.json`",
        "- `work/_gen_audit/unresolved.md`",
    ]
    if bus == "generic":
        lines += [
            "- `work/_gen_audit/bus_handshake.yaml` — handshake spec (authoritative)",
            "- `work/_gen_audit/generic_bus_scaffold_prompt.md` — prompt + assumptions",
            "- `work/_gen_audit/generic_bus_scaffold_diff.patch` — sub-agent edit diff",
        ]
    lines += [
        "",
        "## Rules For Future Agents",
        "",
        "- Do not edit user RTL, user VIP source, specs, or user "
        "reference-model source unless the user explicitly asks.",
        "- Do not weaken sanity or register-access checks to make a run pass.",
        "- Keep generated scoreboard code under `tb/scoreboard/`, not `test/`.",
        "- Keep testcase classes and `sv_list` under `test/`.",
        "- For external VIP reuse, generate project-local glue instead of "
        "patching the VIP source.",
        "",
    ]
    if bus == "generic":
        lines += [_GENERIC_REVIEW_CHECKLIST, ""]
    lines += [
        "## Known Limitations",
        "",
        "See `work/_gen_audit/unresolved.md` and "
        "`work/_gen_audit/spec_normalized/parse_report.md`.",
        "",
        "## Last Verified",
        "",
        "- `make comp`: `not run`",
    ]
    lines += [f"- `{t}`: `not run`" for t in tests]
    lines += [
        "",
        "_Generated at scaffold time (Phase 4); update after compile/sanity._",
    ]
    return "\n".join(lines) + "\n"


def emit_generic_scaffold_diff(files: list[tuple[str, str]]) -> str:
    """Unified diff of the generic-mode generated files against an empty
    placeholder skeleton — the audit artifact a future maintainer reads
    when deciding whether to promote this bus to first-class (SKILL.md
    Phase 4 / 'Promoting a generic bus' in CLAUDE.md)."""
    parts = [
        "# generic_bus_scaffold_diff.patch\n",
        "# Unified diff of the generic-bus files emitted by scaffold.py\n",
        "# against an empty placeholder skeleton. Read as the promotion-\n",
        "# review artifact, or apply from the IP root with `git apply`.\n",
        "\n",
    ]
    for relpath, content in sorted(files):
        diff = difflib.unified_diff(
            [], content.splitlines(keepends=True),
            fromfile=f"a/{relpath}", tofile=f"b/{relpath}",
        )
        parts.extend(diff)
        parts.append("\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="gen-tb scaffold generator")
    ap.add_argument("--ip-root", required=True, type=Path,
                    help="Path to the IP project root (contains work/_gen_audit/)")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite files in place (default: refuse if any target exists)")
    args = ap.parse_args()

    ip_root: Path = args.ip_root.resolve()
    if not ip_root.is_dir():
        sys.exit(f"FATAL: ip-root not a directory: {ip_root}")

    audit = ip_root / "work" / "_gen_audit"
    intake = _load_yaml(audit / "intake.yaml")
    rtl = _load_yaml(audit / "rtl_discovery.yaml")

    _validate_intake(intake)

    ip = intake["ip_name"]
    bus = _bus(intake)
    handshake = _load_handshake(audit) if bus == "generic" else None
    direction = _direction(intake, handshake)
    has_ral = _bus_has_ral(intake, handshake)
    # AXI4-full degraded-mode flag (Phase 1 detector + Phase 2 mandatory question).
    # Only meaningful for bus_protocol: axi_lite; routed through rtl_discovery.yaml.
    axi_full_signature = bool(rtl.get("axi_full_signature", False)) and bus == "axi_lite"
    axi_full_id_width = int(rtl.get("axi_full_id_width", 1)) if axi_full_signature else 1
    # Deterministic backstop for the SKILL.md Phase-2 mandatory question. When
    # the DUT exposes full-AXI ports, scaffold must NOT silently emit a degraded
    # environment — it requires explicit intake evidence that the user answered
    # "no bursts/IDs/outstanding". Missing or "present" => refuse (treat-as-Yes).
    if axi_full_signature:
        answer = intake.get("axi4_full_features")
        if answer not in ("none", "present"):
            sys.exit(
                "FATAL: rtl_discovery.yaml has axi_full_signature: true but "
                "intake.yaml is missing the mandatory axi4_full_features answer. "
                "Ask the user exactly 'Does this DUT use bursts (>1 beat), IDs, "
                "or outstanding?' and record axi4_full_features: none (No -> "
                "degraded AXI4-Lite) or axi4_full_features: present (Yes -> out "
                "of scope). Unanswered is treated as Yes; do not scaffold."
            )
        if answer == "present":
            sys.exit(
                "FATAL: AXI4-full (bursts/IDs/outstanding) is out of scope "
                "(intake.yaml records axi4_full_features: present). Write "
                "work/_gen_audit/unresolved.md per SKILL.md Phase 2 and stop; "
                "do not scaffold an out-of-scope environment."
            )

    regs_path = audit / "spec_normalized" / "registers.yaml"
    if has_ral:
        regs_doc = _load_yaml(regs_path)
        regs = regs_doc.get("registers", [])
    elif regs_path.exists():
        with regs_path.open() as f:
            regs = (yaml.safe_load(f) or {}).get("registers", []) or []
    else:
        regs = []
    has_dpi = intake.get("ref_model_language") == "c_dpi"
    dpi = intake.get("ref_model_inputs") if has_dpi else None
    addr_w = _addr_width(intake, handshake)
    data_w = _data_width(intake, handshake)
    vip_source = _vip_source(intake) if bus != "generic" else "generate_fresh"
    vip_reuse_level = _vip_reuse_level(intake) if bus != "generic" else "import_only"
    external_vip = (
        _scan_external_vip(intake[f"{bus}_vip_path"], ip_root, bus)
        if vip_source == "reuse_my_vip" and bus != "generic"
        else None
    )

    # ---- Build the (target, content) write plan ----
    plan: list[tuple[Path, str, bool]] = []  # (path, content, is_executable)

    sims = _simulators(intake)

    plan.append((ip_root / ".prj_top", "", False))
    plan.append((ip_root / "script" / "setup.sh", emit_setup_sh(sims), True))
    plan.append((ip_root / "script" / "check_env.sh", emit_check_env_sh(sims), True))
    plan.append((ip_root / "script" / "design.f", emit_design_f(rtl), False))
    plan.append((ip_root / "script" / "tb.f", emit_tb_f(ip, has_dpi, bus, vip_source, has_ral, handshake), False))
    if "vcs" in sims:
        plan.append((ip_root / "script" / "makefile", emit_makefile(intake, has_dpi, dpi), False))
    if "questa" in sims:
        plan.append((ip_root / "script" / "makefile_questa", emit_makefile_questa(intake, has_dpi, dpi), False))
    if "xrun" in sims:
        plan.append((ip_root / "script" / "makefile_xrun", emit_makefile_xrun(intake, has_dpi, dpi), False))
    # If VCS isn't requested but another sim is, make `makefile` an include
    # shim so `make all SV_CASE=...` still works without -f.
    if "vcs" not in sims:
        if "questa" in sims:
            shim_target = "makefile_questa"
        elif "xrun" in sims:
            shim_target = "makefile_xrun"
        else:
            shim_target = None
        if shim_target:
            plan.append((ip_root / "script" / "makefile",
                         f"# gen-tb: VCS not requested; delegate to {shim_target}.\n"
                         f"include $(dir $(lastword $(MAKEFILE_LIST))){shim_target}\n",
                         False))

    plan.append((ip_root / "top" / f"{ip}_tb_top.sv",
                 emit_tb_top(intake, rtl, handshake, axi_full_signature), False))

    if bus == "apb":
        plan.append((ip_root / "tb" / "apb_if.sv", emit_apb_if(), False))
    elif bus == "ahb":
        plan.append((ip_root / "tb" / "ahb_if.sv", emit_ahb_if(), False))
    elif bus == "axi_lite":
        plan.append((ip_root / "tb" / "axi_lite_if.sv",
                     emit_axi_lite_if(axi_full_signature=axi_full_signature,
                                      id_w=axi_full_id_width), False))
    else:  # generic
        prefix = handshake["bus_name"]
        plan.append((ip_root / "tb" / f"{prefix}_if.sv", emit_generic_if(handshake), False))
    plan.append((ip_root / "tb" / "tb_api" / "tb_api_pkg.sv", emit_tb_api_pkg(bus, addr_w, data_w, handshake), False))
    plan.append((ip_root / "tb" / "tb_api" / "tb_api_primitives.svh",
                 emit_tb_api_primitives(bus, direction, handshake, axi_full_signature), False))
    if vip_source == "generate_fresh":
        if bus == "apb":
            plan.append((ip_root / "tb" / "apb_agt_top" / "apb_agent.sv", emit_apb_agent_pkg(), False))
            plan.append((ip_root / "tb" / "apb_agt_top" / "apb_agt_config.sv", emit_apb_agt_config(), False))
            plan.append((ip_root / "tb" / "apb_agt_top" / "apb_trans.sv", emit_apb_trans(), False))
            plan.append((ip_root / "tb" / "apb_agt_top" / "apb_driver.sv", emit_apb_driver(), False))
            plan.append((ip_root / "tb" / "apb_agt_top" / "apb_monitor.sv", emit_apb_monitor(), False))
            plan.append((ip_root / "tb" / "apb_agt_top" / "apb_sequencer.sv", emit_apb_sequencer(), False))
            plan.append((ip_root / "tb" / "apb_agt_top" / "apb_sequence.sv", emit_apb_sequence(), False))
        elif bus == "ahb":
            plan.append((ip_root / "tb" / "ahb_agt_top" / "ahb_agent.sv", emit_ahb_agent_pkg(direction), False))
            plan.append((ip_root / "tb" / "ahb_agt_top" / "ahb_agt_config.sv", emit_ahb_agt_config(), False))
            plan.append((ip_root / "tb" / "ahb_agt_top" / "ahb_trans.sv", emit_ahb_trans(), False))
            plan.append((ip_root / "tb" / "ahb_agt_top" / "ahb_driver.sv", emit_ahb_driver(direction), False))
            plan.append((ip_root / "tb" / "ahb_agt_top" / "ahb_monitor.sv", emit_ahb_monitor(), False))
            plan.append((ip_root / "tb" / "ahb_agt_top" / "ahb_sequencer.sv", emit_ahb_sequencer(), False))
            plan.append((ip_root / "tb" / "ahb_agt_top" / "ahb_sequence.sv", emit_ahb_sequence(), False))
        elif bus == "axi_lite":
            plan.append((ip_root / "tb" / "axi_lite_agt_top" / "axi_lite_agent.sv", emit_axi_lite_agent_pkg(direction), False))
            plan.append((ip_root / "tb" / "axi_lite_agt_top" / "axi_lite_agt_config.sv", emit_axi_lite_agt_config(), False))
            plan.append((ip_root / "tb" / "axi_lite_agt_top" / "axi_lite_trans.sv", emit_axi_lite_trans(), False))
            plan.append((ip_root / "tb" / "axi_lite_agt_top" / "axi_lite_driver.sv", emit_axi_lite_driver(direction), False))
            plan.append((ip_root / "tb" / "axi_lite_agt_top" / "axi_lite_monitor.sv",
                         emit_axi_lite_monitor(direction, axi_full_signature), False))
            plan.append((ip_root / "tb" / "axi_lite_agt_top" / "axi_lite_sequencer.sv", emit_axi_lite_sequencer(), False))
            plan.append((ip_root / "tb" / "axi_lite_agt_top" / "axi_lite_sequence.sv", emit_axi_lite_sequence(), False))
        else:  # generic
            prefix = handshake["bus_name"]
            agt_dir = ip_root / "tb" / f"{prefix}_agt_top"
            plan.append((agt_dir / f"{prefix}_agt_pkg.sv", emit_generic_agent_pkg(handshake), False))
            plan.append((agt_dir / f"{prefix}_agt_config.sv", emit_generic_agt_config(handshake), False))
            plan.append((agt_dir / f"{prefix}_trans.sv", emit_generic_trans(handshake), False))
            plan.append((agt_dir / f"{prefix}_sequencer.sv", emit_generic_sequencer(handshake), False))
            plan.append((agt_dir / f"{prefix}_driver.sv", emit_generic_driver(handshake), False))
            plan.append((agt_dir / f"{prefix}_monitor.sv", emit_generic_monitor(handshake), False))
            plan.append((agt_dir / f"{prefix}_agent.sv", emit_generic_agent(handshake), False))
            plan.append((agt_dir / f"{prefix}_sequence.sv", emit_generic_sequence(handshake), False))
    else:
        plan.append((ip_root / "tb" / "external_vip.f", emit_external_vip_f(external_vip), False))
    if has_ral:
        plan.append((ip_root / "tb" / "ral" / f"{ip}_reg_block.sv", emit_ral_block(ip, regs), False))
    if vip_source == "generate_fresh" and has_ral:
        if bus == "apb":
            plan.append((ip_root / "tb" / "ral" / f"{ip}_apb_adapter.sv", emit_apb_adapter(ip), False))
        elif bus == "ahb":
            plan.append((ip_root / "tb" / "ral" / f"{ip}_ahb_adapter.sv", emit_ahb_adapter(ip), False))
        elif bus == "axi_lite":
            plan.append((ip_root / "tb" / "ral" / f"{ip}_axi_lite_adapter.sv", emit_axi_lite_adapter(ip), False))
        else:  # generic
            prefix = handshake["bus_name"]
            plan.append((ip_root / "tb" / "ral" / f"{ip}_{prefix}_adapter.sv", emit_generic_adapter(ip, handshake), False))

    if has_dpi:
        plan.append((ip_root / "tb" / "dpi" / f"{ip}_ref_pkg.sv", emit_dpi_ref_pkg(ip, dpi), False))
        plan.append((ip_root / "tb" / "dpi" / f"{ip}_dpi_proto.h", emit_dpi_proto_h(ip, dpi), False))

    plan.append((ip_root / "test" / f"{ip}_pkg.sv", emit_test_pkg(intake, regs, dpi, handshake), False))
    plan.append((ip_root / "test" / "sv_list",
                 emit_sv_list(ip, vip_source, has_ral, direction), False))

    # ---- Generic-mode audit artifact: prompt template for the scaffold sub-agent ----
    if bus == "generic":
        plan.append((audit / "generic_bus_scaffold_prompt.md",
                     _generic_scaffold_prompt(intake, handshake), False))
        # Promotion-review artifact: unified diff of the generic-bus files
        # emitted above against an empty skeleton (SKILL.md Phase 4).
        prefix = handshake["bus_name"]
        generic_files = [
            (str(t.relative_to(ip_root)), c)
            for (t, c, _) in plan
            if f"{prefix}_agt_top" in t.parts
            or t.name in (f"{prefix}_if.sv", f"{ip}_{prefix}_adapter.sv",
                          "tb_api_primitives.svh", f"{ip}_tb_top.sv")
        ]
        plan.append((audit / "generic_bus_scaffold_diff.patch",
                     emit_generic_scaffold_diff(generic_files), False))

    # ---- Phase 7 hand-off artifacts (always present in the deliverable) ----
    # CLAUDE.md: written on first scaffold only; if it already exists (user may
    # have edited it), emit CLAUDE.md.new instead of clobbering edits.
    claude_md = ip_root / "CLAUDE.md"
    claude_target = claude_md if not claude_md.exists() else ip_root / "CLAUDE.md.new"
    plan.append((claude_target,
                 emit_claude_md(ip, intake, rtl, handshake, bus, direction,
                                vip_source, vip_reuse_level, has_ral,
                                axi_full_signature), False))
    # unresolved.md: seed a placeholder only if Phase 5/6/7 has not written it.
    unresolved_md = audit / "unresolved.md"
    if not unresolved_md.exists():
        plan.append((unresolved_md, emit_unresolved_md(ip), False))

    # ---- Symlink-guard ALL targets first; refuse any if any fails ----
    resolved = []
    for target, _, _ in plan:
        try:
            resolved.append((_resolve_safely(target, ip_root), target))
        except RuntimeError as e:
            sys.exit(f"FATAL: {e}")

    # ---- Existence check unless --force ----
    if not args.force:
        existing = [t for _, t in resolved if t.exists() and t.stat().st_size > 0]
        if existing:
            print("FATAL: target files exist (use --force to overwrite):", file=sys.stderr)
            for t in existing:
                print(f"  {t}", file=sys.stderr)
            return 2

    # ---- Materialize ----
    written = []
    for (target, content, is_exec) in plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        if is_exec:
            target.chmod(0o755)
        written.append(str(target.relative_to(ip_root)))

    # ---- Audit ----
    audit_out = audit / "scaffold_audit.json"
    effective_reg_sem = (
        handshake["register_semantics"] if bus == "generic"
        else intake.get("register_semantics", "yes")
    )
    audit_doc: dict[str, Any] = {
        "ip_name": ip,
        "files_written": written,
        "has_dpi": has_dpi,
        "register_count": len(regs),
        "scaffold_version": "v1.5",
        "bus_protocol": bus,
        "register_semantics": effective_reg_sem,
    }
    if axi_full_signature:
        audit_doc["axi_full_signature"] = True
        audit_doc["axi_full_id_width"] = axi_full_id_width
    if bus == "generic":
        audit_doc["generic_bus"] = {
            "bus_name":      handshake["bus_name"],
            "direction":     handshake["direction"],
            "handshake_kind": handshake["handshake"]["kind"],
            "addr_width":    addr_w,
            "data_width":    data_w,
            "prompt_path":   "work/_gen_audit/generic_bus_scaffold_prompt.md",
        }
    else:
        audit_doc[f"{bus}_vip_source"] = vip_source
        audit_doc[f"{bus}_vip_reuse_level"] = (
            vip_reuse_level if vip_source == "reuse_my_vip" else None
        )
        audit_doc["external_vip"] = (
            {
                "root": str(external_vip["root"]),
                "packages": external_vip["packages"],
                "agents": external_vip["agents"],
                "transactions": external_vip["transactions"],
                "configs": external_vip["configs"],
                "interfaces": external_vip["interfaces"],
                "compile_units": [str(p) for p in external_vip["compile_units"]],
            }
            if external_vip else None
        )
    audit_out.write_text(json.dumps(audit_doc, indent=2))

    print(f"scaffold complete: {len(written)} files written")
    print(f"audit: {audit_out}")
    print(f"next: cd {ip_root}/script && source setup.sh && make all SV_CASE={ip}_sanity_test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
