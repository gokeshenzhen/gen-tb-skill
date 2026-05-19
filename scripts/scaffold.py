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
    tb/apb_agt_top/{apb_agent.sv, apb_agt_config.sv, apb_trans.sv,
                    apb_driver.sv, apb_monitor.sv, apb_sequencer.sv,
                    apb_sequence.sv}
    tb/tb_api/{tb_api_pkg.sv, tb_api_primitives.svh}
    tb/dpi/{<ip>_ref_pkg.sv, <ip>_dpi_proto.h}     # if c_dpi
    tb/ral/<ip>_reg_block.sv
    tb/ral/<ip>_apb_adapter.sv                      # if generating fresh APB agent
    test/<ip>_pkg.sv (sanity + reg_access + random_seq + smoke)
    test/sv_list
    work/_gen_audit/scaffold_audit.json

Symlink guard: any write target whose realpath escapes ip_root is
refused with a non-zero exit and a message. No writes are performed
if any target fails the guard (atomic).

This file is the v1.2 first-working-draft for APB agent and RAL
generation; corner cases (multi-clock, py_dpi, full external VIP
drive glue) are still deferred to references.
"""

from __future__ import annotations

import argparse
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
    vip_source = intake.get("apb_vip_source", "generate_fresh")
    if vip_source not in ("generate_fresh", "reuse_my_vip"):
        sys.exit(f"FATAL: unsupported apb_vip_source: {vip_source!r}")
    if vip_source == "reuse_my_vip" and "apb_vip_path" not in intake:
        sys.exit("FATAL: reuse_my_vip requires intake.yaml key: apb_vip_path")
    reuse_level = intake.get("apb_vip_reuse_level", "import_only")
    if reuse_level not in ("import_only", "drive_with_vip"):
        sys.exit(f"FATAL: unsupported apb_vip_reuse_level: {reuse_level!r}")
    if vip_source != "reuse_my_vip" and "apb_vip_reuse_level" in intake:
        sys.exit("FATAL: apb_vip_reuse_level is valid only with reuse_my_vip")


def _resolve_input_path(raw: str, ip_root: Path) -> Path:
    expanded = raw.replace("$PROJ_DIR", str(ip_root))
    return Path(expanded).expanduser().resolve()


def _scan_external_vip(raw_path: str, ip_root: Path) -> dict[str, Any]:
    vip_root = _resolve_input_path(raw_path, ip_root)
    if not vip_root.is_dir():
        sys.exit(f"FATAL: apb_vip_path is not a directory: {vip_root}")

    sv_files = sorted(vip_root.rglob("*.sv"))
    if not sv_files:
        sys.exit(f"FATAL: no SystemVerilog files found under apb_vip_path: {vip_root}")

    packages: list[tuple[str, Path]] = []
    agents: list[tuple[str, Path]] = []
    transactions: list[tuple[str, Path]] = []
    configs: list[tuple[str, Path]] = []
    interfaces: list[tuple[str, Path]] = []
    for path in sv_files:
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

    package_units = sorted({p for _, p in packages})
    interface_units = sorted({p for _, p in interfaces})
    if package_units:
        compile_units = interface_units + [p for p in package_units if p not in interface_units]
    else:
        compile_units = sv_files

    incdirs = sorted({p.parent for p in sv_files})
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


def emit_tb_f(ip: str, has_dpi: bool, vip_source: str) -> str:
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
    if vip_source == "generate_fresh":
        lines.insert(1, f"+incdir+{tb_root}/apb_agt_top")
        lines.insert(lines.index(f"{tb_root}/ral/{ip}_reg_block.sv"), f"{tb_root}/apb_agt_top/apb_agent.sv")
        lines.append(f"{tb_root}/ral/{ip}_apb_adapter.sv")
    else:
        lines.insert(lines.index(f"{tb_root}/ral/{ip}_reg_block.sv"), f"-f {tb_root}/external_vip.f")
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
                uvm_config_db#(tb_api::vif_t)::set(null, "*", "apb_vif", apb);
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
        if (fa.get("name"), fa.get("bits"), fa.get("access")) != (
            fb.get("name"), fb.get("bits"), fb.get("access")
        ):
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
    by_base: dict[str, list[tuple[int, int, dict]]] = {}
    for idx, reg in enumerate(regs):
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

    readable_regs = regs[:8]
    reg_reads = "\n".join(
        f'                tb_api::read(ADDR_{r["name"]}, rd); '
        f'`uvm_info(tag, $sformatf("{r["name"]} = 0x%08h", rd), UVM_LOW)'
        for r in readable_regs
    )
    legal_addr_list = ", ".join(f"ADDR_{r['name']}" for r in readable_regs)

    helper_tasks = textwrap.dedent(f"""\
        task automatic run_reg_access_reads(input string tag);
            logic [tb_api::DATA_W-1:0] rd;
            `uvm_info(tag, "reading all registers", UVM_LOW)
{reg_reads}
        endtask
        """)

    sanity_test = textwrap.dedent(f"""\
        class {ip}_sanity_test extends uvm_test;
            `uvm_component_utils({ip}_sanity_test)
            function new(string n="{ip}_sanity_test", uvm_component p=null); super.new(n,p); endfunction
            task run_phase(uvm_phase phase);
                phase.raise_objection(this);
                wait (tb_api::vif.presetn === 1'b1);
                repeat (4) @(posedge tb_api::vif.pclk);
                tb_api::expect_reg({addr_w}'h{sanity_addr:03X}, 32'h{sanity_value:08X}, "SANITY");
                phase.drop_objection(this);
            endtask
        endclass
        """)

    random_seq_test = textwrap.dedent(f"""\
        class {ip}_random_seq_test extends uvm_test;
            `uvm_component_utils({ip}_random_seq_test)
            apb_agent      agent;
            apb_agt_config cfg;

            function new(string n="{ip}_random_seq_test", uvm_component p=null);
                super.new(n, p);
            endfunction

            function void build_phase(uvm_phase phase);
                super.build_phase(phase);
                cfg = apb_agt_config::type_id::create("cfg");
                cfg.vif = tb_api::vif;
                uvm_config_db#(apb_agt_config)::set(this, "agent", "cfg", cfg);
                agent = apb_agent::type_id::create("agent", this);
            endfunction

            task run_phase(uvm_phase phase);
                apb_sequence seq;
                phase.raise_objection(this);
                wait (tb_api::vif.presetn === 1'b1);
                repeat (4) @(posedge tb_api::vif.pclk);
                seq = apb_sequence::type_id::create("seq");
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
        smoke_test = "    // DPI smoke test generation: see references/refm_dpi.md.\n"
        smoke_test += "    // v1.1 stub — hand-write the smoke test for now using tb_api::wait_status_flag.\n"

    vip_source = intake.get("apb_vip_source", "generate_fresh")
    agent_import = "    import apb_agt_pkg::*;" if vip_source == "generate_fresh" else ""
    ral_import = f"    import {ip}_ral_pkg::*;"
    adapter_import = f"    import {ip}_apb_adapter_pkg::*;" if vip_source == "generate_fresh" else ""
    random_seq_body = random_seq_test if vip_source == "generate_fresh" else ""
    if vip_source == "generate_fresh":
        reg_access_test = textwrap.dedent(f"""\
            class {ip}_reg_access_test extends uvm_test;
                `uvm_component_utils({ip}_reg_access_test)
                apb_agent         agent;
                apb_agt_config    cfg;
                {ip}_reg_block    ral;
                {ip}_apb_adapter  adapter;

                function new(string n="{ip}_reg_access_test", uvm_component p=null);
                    super.new(n,p);
                endfunction

                function void build_phase(uvm_phase phase);
                    super.build_phase(phase);
                    cfg = apb_agt_config::type_id::create("cfg");
                    cfg.vif = tb_api::vif;
                    uvm_config_db#(apb_agt_config)::set(this, "agent", "cfg", cfg);
                    agent = apb_agent::type_id::create("agent", this);
                    ral = {ip}_reg_block::type_id::create("ral");
                    ral.build();
                    adapter = {ip}_apb_adapter::type_id::create("adapter");
                endfunction

                task run_phase(uvm_phase phase);
                    {ip}_ral_access_seq seq;
                    phase.raise_objection(this);
                    wait (tb_api::vif.presetn === 1'b1);
                    repeat (4) @(posedge tb_api::vif.pclk);
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
                    wait (tb_api::vif.presetn === 1'b1);
                    repeat (4) @(posedge tb_api::vif.pclk);
                    run_reg_access_reads("REG_ACCESS");
                    phase.drop_objection(this);
                endtask
            endclass
            """)

    return textwrap.dedent(f"""\
        `ifndef {ip.upper()}_PKG_SV
        `define {ip.upper()}_PKG_SV
        package {ip}_pkg;
            import uvm_pkg::*;
        {agent_import}
        {ral_import}
        {adapter_import}
        {f'    import {ip}_ref_pkg::*;' if dpi else ''}
            `include "uvm_macros.svh"

        {addr_consts_str}

        {helper_tasks}
        {sanity_test}
        {reg_access_test}
        {random_seq_body}
        {smoke_test}
        endpackage
        `endif
        """)


def emit_sv_list(ip: str, vip_source: str) -> str:
    lines = [f"{ip}_sanity_test", f"{ip}_reg_access_test"]
    if vip_source == "generate_fresh":
        lines.append(f"{ip}_random_seq_test")
    return "\n".join(lines) + "\n"


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
    vip_source = intake.get("apb_vip_source", "generate_fresh")
    vip_reuse_level = intake.get("apb_vip_reuse_level", "import_only")
    external_vip = (
        _scan_external_vip(intake["apb_vip_path"], ip_root)
        if vip_source == "reuse_my_vip"
        else None
    )

    # ---- Build the (target, content) write plan ----
    plan: list[tuple[Path, str, bool]] = []  # (path, content, is_executable)

    plan.append((ip_root / ".prj_top", "", False))
    plan.append((ip_root / "script" / "setup.sh", emit_setup_sh(), True))
    plan.append((ip_root / "script" / "check_env.sh", emit_check_env_sh(), True))
    plan.append((ip_root / "script" / "design.f", emit_design_f(rtl), False))
    plan.append((ip_root / "script" / "tb.f", emit_tb_f(ip, has_dpi, vip_source), False))
    plan.append((ip_root / "script" / "makefile", emit_makefile(intake, has_dpi, dpi), False))

    plan.append((ip_root / "top" / f"{ip}_tb_top.sv", emit_tb_top(intake, rtl), False))

    plan.append((ip_root / "tb" / "apb_if.sv", emit_apb_if(), False))
    plan.append((ip_root / "tb" / "tb_api" / "tb_api_pkg.sv", emit_tb_api_pkg(paddr_w), False))
    plan.append((ip_root / "tb" / "tb_api" / "tb_api_primitives.svh", emit_tb_api_primitives(), False))
    if vip_source == "generate_fresh":
        plan.append((ip_root / "tb" / "apb_agt_top" / "apb_agent.sv", emit_apb_agent_pkg(), False))
        plan.append((ip_root / "tb" / "apb_agt_top" / "apb_agt_config.sv", emit_apb_agt_config(), False))
        plan.append((ip_root / "tb" / "apb_agt_top" / "apb_trans.sv", emit_apb_trans(), False))
        plan.append((ip_root / "tb" / "apb_agt_top" / "apb_driver.sv", emit_apb_driver(), False))
        plan.append((ip_root / "tb" / "apb_agt_top" / "apb_monitor.sv", emit_apb_monitor(), False))
        plan.append((ip_root / "tb" / "apb_agt_top" / "apb_sequencer.sv", emit_apb_sequencer(), False))
        plan.append((ip_root / "tb" / "apb_agt_top" / "apb_sequence.sv", emit_apb_sequence(), False))
    else:
        plan.append((ip_root / "tb" / "external_vip.f", emit_external_vip_f(external_vip), False))
    plan.append((ip_root / "tb" / "ral" / f"{ip}_reg_block.sv", emit_ral_block(ip, regs), False))
    if vip_source == "generate_fresh":
        plan.append((ip_root / "tb" / "ral" / f"{ip}_apb_adapter.sv", emit_apb_adapter(ip), False))

    if has_dpi:
        plan.append((ip_root / "tb" / "dpi" / f"{ip}_ref_pkg.sv", emit_dpi_ref_pkg(ip, dpi), False))
        plan.append((ip_root / "tb" / "dpi" / f"{ip}_dpi_proto.h", emit_dpi_proto_h(ip, dpi), False))

    plan.append((ip_root / "test" / f"{ip}_pkg.sv", emit_test_pkg(intake, regs, dpi), False))
    plan.append((ip_root / "test" / "sv_list", emit_sv_list(ip, vip_source), False))

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
        "scaffold_version": "v1.2",
        "apb_vip_source": vip_source,
        "apb_vip_reuse_level": vip_reuse_level if vip_source == "reuse_my_vip" else None,
        "external_vip": {
            "root": str(external_vip["root"]),
            "packages": external_vip["packages"],
            "agents": external_vip["agents"],
            "transactions": external_vip["transactions"],
            "configs": external_vip["configs"],
            "interfaces": external_vip["interfaces"],
            "compile_units": [str(p) for p in external_vip["compile_units"]],
        } if external_vip else None,
    }, indent=2))

    print(f"scaffold complete: {len(written)} files written")
    print(f"audit: {audit_out}")
    print(f"next: cd {ip_root}/script && source setup.sh && make all SV_CASE={ip}_sanity_test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
