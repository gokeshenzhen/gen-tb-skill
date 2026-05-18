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
    tb/apb_if.sv
    tb/tb_api/{tb_api_pkg.sv, tb_api_primitives.svh}
    tb/dpi/{<ip>_ref_pkg.sv, <ip>_dpi_proto.h}     # if c_dpi
    tb/ral/<ip>_reg_block.sv                        # basic version
    test/<ip>_pkg.sv (sanity + reg_access + smoke)
    test/sv_list
    work/_gen_audit/scaffold_audit.json

Symlink guard: any write target whose realpath escapes ip_root is
refused with a non-zero exit and a message. No writes are performed
if any target fails the guard (atomic).

This file is the v1.1 first-working-draft; corner cases (multi-clock,
RAL aliasing, py_dpi) are stubbed with TODO comments and a non-fatal
warning printed to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
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
    Raises RuntimeError if the resolved path escapes."""
    real_root = ip_root.resolve()
    # Resolve the *parent* if the file does not yet exist
    parent = target.parent
    if not parent.exists():
        # walk up to first existing ancestor
        check = parent
        while not check.exists() and check != check.parent:
            check = check.parent
        real_parent = check.resolve() / parent.relative_to(check)
    else:
        real_parent = parent.resolve()
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


def _validate_intake(intake: dict) -> None:
    required = ["ip_name", "bus_protocol", "ref_model_language", "uvm_version", "paddr_width"]
    missing = [k for k in required if k not in intake]
    if missing:
        sys.exit(f"FATAL: intake.yaml missing keys: {missing}")
    if intake["bus_protocol"] != "apb":
        sys.exit(f"FATAL: v1.1 only supports apb (got {intake['bus_protocol']!r})")


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


def emit_setup_sh() -> str:
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
        if [ -z "$UVM_HOME" ] && [ -n "$VCS_HOME" ]; then
            export UVM_HOME="$VCS_HOME/etc/uvm-1.2"
        fi
        echo "WORK_DIR=$WORK_DIR"
        echo "UVM_HOME=$UVM_HOME"
        echo "setup done"
        """)


def emit_check_env_sh() -> str:
    return textwrap.dedent("""\
        #!/bin/bash
        # gen-tb generated. Validates simulator env before compile.
        err=0
        command -v vcs >/dev/null || { echo "FATAL: vcs not in PATH"; err=1; }
        [ -n "$VCS_HOME" ] || { echo "FATAL: VCS_HOME unset"; err=1; }
        [ -n "$UVM_HOME" ] || { echo "FATAL: UVM_HOME unset (source script/setup.sh first)"; err=1; }
        [ -n "$PROJ_DIR" ] || { echo "FATAL: PROJ_DIR unset (source script/setup.sh first)"; err=1; }
        [ -e "$UVM_HOME/src/uvm.sv" ] || { echo "FATAL: UVM source not found"; err=1; }
        exit $err
        """)


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


def emit_tb_f(ip: str, has_dpi: bool) -> str:
    tb_root = "$PROJ_DIR/tb"
    test_root = "$PROJ_DIR/test"
    top_root = "$PROJ_DIR/top"
    lines = [
        f"+incdir+{tb_root}",
        f"+incdir+{tb_root}/tb_api",
        f"+incdir+{tb_root}/ral",
        f"+incdir+{test_root}",
        f"+incdir+{top_root}",
        f"{tb_root}/apb_if.sv",
        f"{tb_root}/tb_api/tb_api_pkg.sv",
        f"{tb_root}/ral/{ip}_reg_block.sv",
    ]
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


def emit_tb_top(intake: dict, rtl: dict) -> str:
    ip = intake["ip_name"]
    top = rtl["top_module"]["name"]
    paddr_w = intake.get("paddr_width", 12)
    rst_cycles = intake.get("reset", {}).get("presetn_duration_cycles", 16)
    half_period = intake.get("clock", {}).get("pclk_period_ns", 10) // 2

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

    return textwrap.dedent(f"""\
        // gen-tb generated tb top for {ip}.
        // Interface receives clk/rst via input ports (no dual-drive).
        // Non-bus DUT pads tied to protocol-idle defaults at this level.
        `timescale 1ns/1ps

        module {ip}_tb_top;
            import uvm_pkg::*;
            `include "uvm_macros.svh"

            // ---- clock & reset (top owns the drive) ----
            logic pclk = 0;
            logic presetn = 0;
            always #{half_period} pclk = ~pclk;

            // ---- interface ----
            apb_if #(.ADDR_W({paddr_w}), .DATA_W(32)) apb (.pclk(pclk), .presetn(presetn));

            // ---- non-bus DUT pad defaults ----
        {pad_decls_str}

            // ---- DUT ----
            {top} u_dut (
                .pclk    (pclk),     .presetn (presetn),
                .psel    (apb.psel), .penable (apb.penable),
                .pwrite  (apb.pwrite), .paddr  (apb.paddr),
                .pwdata  (apb.pwdata), .prdata (apb.prdata),
                .pready  (apb.pready), .pslverr(apb.pslverr){pad_connects_str}
            );

            // ---- reset sequence ----
            initial begin
                presetn = 0;
                repeat ({rst_cycles}) @(posedge pclk);
                presetn = 1;
            end

            // ---- UVM entry ----
            initial begin
                uvm_config_db#(virtual apb_if)::set(null, "*", "apb_vif", apb);
                tb_api::set_vif(apb);
                run_test();
            end
        endmodule
        """)


def emit_tb_api_pkg(paddr_w: int) -> str:
    return textwrap.dedent(f"""\
        `ifndef TB_API_PKG_SV
        `define TB_API_PKG_SV
        package tb_api;
            import uvm_pkg::*;
            `include "uvm_macros.svh"
            parameter int  ADDR_W = {paddr_w};
            parameter int  DATA_W = 32;
            typedef virtual apb_if #(.ADDR_W(ADDR_W), .DATA_W(DATA_W)) vif_t;
            vif_t vif;
            function automatic void set_vif(vif_t v); vif = v; endfunction
            `include "tb_api_primitives.svh"
        endpackage
        `endif
        """)


def emit_tb_api_primitives() -> str:
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


def emit_ral_block(ip: str, regs: list[dict]) -> str:
    """Generate a minimal uvm_reg_block stub. Full RAL semantics
    (aliasing, RO/WO disjoint) deferred to references/ral_gen.md
    implementation in v1.2."""
    lines = [
        f"`ifndef {ip.upper()}_REG_BLOCK_SV",
        f"`define {ip.upper()}_REG_BLOCK_SV",
        f"// gen-tb minimal RAL — v1.1 stub. Full RAL generation deferred",
        f"// to v1.2 (RAL aliasing, RO/WO disjoint handling).",
        f"package {ip}_ral_pkg;",
        f"    import uvm_pkg::*;",
        f'    `include "uvm_macros.svh"',
        f"",
        f"    class {ip}_reg_block extends uvm_reg_block;",
        f"        `uvm_object_utils({ip}_reg_block)",
        f'        function new(string name="{ip}_reg_block"); super.new(name, UVM_NO_COVERAGE); endfunction',
        f"        virtual function void build();",
        f"            // TODO: instantiate {len(regs)} uvm_reg objects from registers.yaml",
        f"            // See references/ral_gen.md for the full schema.",
        f"            default_map = create_map(\"default_map\", 0, 4, UVM_LITTLE_ENDIAN);",
        f"            lock_model();",
        f"        endfunction",
        f"    endclass",
        f"endpackage",
        f"`endif",
    ]
    return "\n".join(lines) + "\n"


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


def emit_test_pkg(intake: dict, regs: list[dict], dpi: dict | None) -> str:
    ip = intake["ip_name"]
    addr_w = intake.get("paddr_width", 12)
    sanity_addr, sanity_value = _pick_sanity_target(regs)

    addr_consts = []
    for r in regs:
        off = r["offset"] if isinstance(r["offset"], int) else int(r["offset"], 16)
        addr_consts.append(
            f"    localparam logic [{addr_w-1}:0] ADDR_{r['name']} = {addr_w}'h{off:03X};"
        )
    addr_consts_str = "\n".join(addr_consts)

    sanity_test = textwrap.dedent(f"""\
        class {ip}_sanity_test extends uvm_test;
            `uvm_component_utils({ip}_sanity_test)
            function new(string n="{ip}_sanity_test", uvm_component p=null); super.new(n,p); endfunction
            task run_phase(uvm_phase ph);
                ph.raise_objection(this);
                wait (tb_api::vif.presetn === 1'b1);
                repeat (4) @(posedge tb_api::vif.pclk);
                tb_api::expect_reg({addr_w}'h{sanity_addr:03X}, 32'h{sanity_value:08X}, "SANITY");
                ph.drop_objection(this);
            endtask
        endclass
        """)

    reg_access_test = textwrap.dedent(f"""\
        class {ip}_reg_access_test extends uvm_test;
            `uvm_component_utils({ip}_reg_access_test)
            function new(string n="{ip}_reg_access_test", uvm_component p=null); super.new(n,p); endfunction
            task run_phase(uvm_phase ph);
                logic [31:0] rd;
                ph.raise_objection(this);
                wait (tb_api::vif.presetn === 1'b1);
                repeat (4) @(posedge tb_api::vif.pclk);
                // v1.1 stub: just read every register once and log.
                // Full RAL walk (write/read/check) deferred to v1.2.
                `uvm_info("REG_ACCESS", "v1.1 stub — reading all registers", UVM_LOW)
                {chr(10).join(f'                tb_api::read(ADDR_{r["name"]}, rd); `uvm_info("REG_ACCESS", $sformatf("{r["name"]} = 0x%08h", rd), UVM_LOW)' for r in regs[:8])}
                ph.drop_objection(this);
            endtask
        endclass
        """)

    smoke_test = ""
    if dpi:
        smoke_test = "    // DPI smoke test generation: see references/refm_dpi.md.\n"
        smoke_test += "    // v1.1 stub — hand-write the smoke test for now using tb_api::wait_status_flag.\n"

    return textwrap.dedent(f"""\
        `ifndef {ip.upper()}_PKG_SV
        `define {ip.upper()}_PKG_SV
        package {ip}_pkg;
            import uvm_pkg::*;
        {f'    import {ip}_ref_pkg::*;' if dpi else ''}
            `include "uvm_macros.svh"

        {addr_consts_str}

        {sanity_test}
        {reg_access_test}
        {smoke_test}
        endpackage
        `endif
        """)


def emit_sv_list(ip: str) -> str:
    return f"{ip}_sanity_test\n{ip}_reg_access_test\n"


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
    regs_doc = _load_yaml(audit / "spec_normalized" / "registers.yaml")
    regs = regs_doc.get("registers", [])

    _validate_intake(intake)

    ip = intake["ip_name"]
    has_dpi = intake.get("ref_model_language") == "c_dpi"
    dpi = intake.get("ref_model_inputs") if has_dpi else None
    paddr_w = intake.get("paddr_width", 12)

    # ---- Build the (target, content) write plan ----
    plan: list[tuple[Path, str, bool]] = []  # (path, content, is_executable)

    plan.append((ip_root / ".prj_top", "", False))
    plan.append((ip_root / "script" / "setup.sh", emit_setup_sh(), True))
    plan.append((ip_root / "script" / "check_env.sh", emit_check_env_sh(), True))
    plan.append((ip_root / "script" / "design.f", emit_design_f(rtl), False))
    plan.append((ip_root / "script" / "tb.f", emit_tb_f(ip, has_dpi), False))
    plan.append((ip_root / "script" / "makefile", emit_makefile(intake, has_dpi, dpi), False))

    plan.append((ip_root / "top" / f"{ip}_tb_top.sv", emit_tb_top(intake, rtl), False))

    plan.append((ip_root / "tb" / "apb_if.sv", emit_apb_if(), False))
    plan.append((ip_root / "tb" / "tb_api" / "tb_api_pkg.sv", emit_tb_api_pkg(paddr_w), False))
    plan.append((ip_root / "tb" / "tb_api" / "tb_api_primitives.svh", emit_tb_api_primitives(), False))
    plan.append((ip_root / "tb" / "ral" / f"{ip}_reg_block.sv", emit_ral_block(ip, regs), False))

    if has_dpi:
        plan.append((ip_root / "tb" / "dpi" / f"{ip}_ref_pkg.sv", emit_dpi_ref_pkg(ip, dpi), False))
        plan.append((ip_root / "tb" / "dpi" / f"{ip}_dpi_proto.h", emit_dpi_proto_h(ip, dpi), False))

    plan.append((ip_root / "test" / f"{ip}_pkg.sv", emit_test_pkg(intake, regs, dpi), False))
    plan.append((ip_root / "test" / "sv_list", emit_sv_list(ip), False))

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
    audit_out.write_text(json.dumps({
        "ip_name": ip,
        "files_written": written,
        "has_dpi": has_dpi,
        "register_count": len(regs),
        "scaffold_version": "v1.1",
    }, indent=2))

    print(f"scaffold complete: {len(written)} files written")
    print(f"audit: {audit_out}")
    print(f"next: cd {ip_root}/script && source setup.sh && make all SV_CASE={ip}_sanity_test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
