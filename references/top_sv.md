# Generated top.sv rules (gen-tb reference)

> Loaded during Phase 4 scaffold and Phase 5 compile-fix. Defines how
> `top/<ip>_tb_top.sv` connects the generated APB testbench to user RTL.

## Ownership

`top/<ip>_tb_top.sv` is generated and may be overwritten by scaffold.
User RTL is never edited. Any project-specific bridge or tie-off needed
for compile/runtime fix must be generated under `top/`, `tb/`, `test/`,
or `script/`, not patched into `rtl/` or a user VIP directory.

## Canonical Structure

Generated top has this shape:

```systemverilog
`timescale 1ns/1ps

module <ip>_tb_top;
    import uvm_pkg::*;
    `include "uvm_macros.svh"

    logic pclk = 0;
    logic presetn = 0;
    always #<half_period_ns> pclk = ~pclk;

    apb_if #(.ADDR_W(<paddr_width>), .DATA_W(32)) apb (
        .pclk(pclk),
        .presetn(presetn)
    );

    // non-bus DUT pad declarations

    <top_module> u_dut (...);

    initial begin
        presetn = 0;
        repeat (<reset_cycles>) @(posedge pclk);
        presetn = 1;
    end

    initial begin
        uvm_config_db#(tb_api::vif_t)::set(null, "*", "apb_vif", apb);
        tb_api::set_vif(apb);
        run_test();
    end
endmodule
```

The top module owns clock/reset generation and DUT instantiation. Tests
own UVM components, sequences, and per-test agent config objects.

## Clock And Reset (G12)

Clock and reset are driven exactly once, by the generated top. The APB
interface receives them as input ports:

```systemverilog
interface apb_if #(int ADDR_W = 12, int DATA_W = 32) (
    input logic pclk,
    input logic presetn
);
```

Do not generate:

```systemverilog
assign apb.presetn = presetn;
assign apb.pclk = pclk;
```

Those create structural/procedural dual-driver warnings in VCS and can
become errors. Any signal driven by top and observed by an interface
should use the same input-port pattern.

Reset defaults:

- `presetn` is active-low unless intake says otherwise
- reset starts asserted at time 0
- deassert after `intake.reset.presetn_duration_cycles` pclk cycles
- tests wait for `tb_api::vif.presetn === 1'b1` before driving

## DUT APB Wiring

Use exact port names from `rtl_discovery.yaml`; do not normalize case.
The canonical APB connection is:

```systemverilog
.<pclk>    (pclk),
.<presetn> (presetn),
.<psel>    (apb.psel),
.<penable> (apb.penable),
.<pwrite>  (apb.pwrite),
.<paddr>   (apb.paddr),
.<pwdata>  (apb.pwdata),
.<prdata>  (apb.prdata),
.<pready>  (apb.pready),
.<pslverr> (apb.pslverr)
```

If the DUT lacks `pready` or `pslverr`, do not guess. Ask the user or
record an explicit tie/default in `rtl_discovery.yaml` before scaffold.

## Non-bus Pads (G4)

Every top-level DUT port not consumed by APB clock/reset/data must come
from `rtl_discovery.yaml: other_pads`.

Rules:

- DUT input pads get generated `logic` declarations with conservative
  protocol-idle defaults
- DUT output pads get `wire` declarations and no driver
- pad declarations live in top, not in the APB interface
- mark functional pads as future agent hookups in comments when needed

Example:

```systemverilog
logic srx_pad_i = 1'b1;   // TODO: connect serial agent when needed
logic cts_pad_i = 1'b1;   // modem idle
wire  stx_pad_o;
wire  irq;
```

Default policy:

| Pad role | Direction | Default |
|---|---|---|
| serial RX | input | `1'b1` |
| modem/flow-control input | input | `1'b1` |
| enable/config input | input | `1'b0` |
| scan/test/DFT input | input | ask user |
| extra clock/reset input | input | ask user |
| interrupt/status/data output | output | wire only |

If an input's default can affect mandatory sanity, mention it in
`parse_report.md` and generated `CLAUDE.md`.

## UVM Handoff

Top must publish the APB virtual interface for both DE and DV surfaces:

```systemverilog
initial begin
    uvm_config_db#(tb_api::vif_t)::set(null, "*", "apb_vif", apb);
    tb_api::set_vif(apb);
    run_test();
end
```

`tb_api::set_vif(apb)` is mandatory because sanity and import-only VIP
reuse tests use `tb_api` directly. The `uvm_config_db` entry is
mandatory because generated APB agent drivers/monitors retrieve
`apb_vif`.

Generated tests configure the generated agent by setting
`apb_agt_config` on their local agent path:

```systemverilog
cfg = apb_agt_config::type_id::create("cfg");
cfg.vif = tb_api::vif;
uvm_config_db#(apb_agt_config)::set(this, "agent", "cfg", cfg);
```

Do not instantiate the generated APB agent in top. Keep UVM components
inside tests/env-level classes.

## External VIP Reuse

For `apb_vip_source: reuse_my_vip` and `apb_vip_reuse_level:
import_only`, top stays the same canonical top. Built-in sanity and
reg-access tests use `tb_api`; the external VIP is only imported into
the compile tree.

For `drive_with_vip`, Phase 5 may generate project-local bridge logic:

- an interface instance matching the user's VIP
- continuous assignments between generated `apb_if` and user VIP
  interface signals
- `uvm_config_db` setup for the user's VIP virtual interface
- one read/write smoke test under `test/`

This bridge must live under generated directories and must not edit the
user VIP source. If the VIP's interface derives a signal internally
(for example read-data sliced from a wider bus), bridge the DUT to the
VIP's expected source signal instead of force-driving a derived net.

## Timescale And Includes

Emit:

```systemverilog
`timescale 1ns/1ps
import uvm_pkg::*;
`include "uvm_macros.svh"
```

Do not include generated packages directly from top. `script/tb.f`
owns compile order. Top should only instantiate modules/interfaces and
call `run_test()`.

## Validation Checks

Before accepting generated top:

- no dual-driver warnings for clock/reset
- every DUT top port is connected exactly once
- input pads have explicit defaults or user-approved ties
- output pads have no procedural or continuous driver from top
- `tb_api::set_vif(apb)` exists
- `uvm_config_db` publishes `"apb_vif"`
- reset deasserts before mandatory tests drive APB
- `paddr` width matches `intake.yaml.paddr_width`

## Cross-references

- `references/rtl_discovery.md` — source schema for ports and pads
- `references/apb.md` — generated APB interface/agent behavior
- `references/apb_external_vip.md` — external VIP bridge boundary
- `references/tb_api.md` — DE BFM virtual interface handoff
- `scripts/scaffold.py` `emit_tb_top` — current implementation
