# APB agent generation (gen-tb reference)

> Loaded during Phase 4 when the user has not opted to reuse an
> external APB VIP. Defines the seven-file UVM agent gen-tb generates.
>
> **Status: v1.2 implemented for generated APB agents.** v1.1 shipped
> only `tb_api` (the DE-persona surface); v1.2 adds the full seven-file
> UVM agent for fresh generation. External VIP reuse remains a separate
> follow-up path.

## Why an agent matters even with tb_api

`tb_api` is sufficient for DE-style directed tests and for sanity /
reg_access. But the DV persona expects to:

- Write `uvm_sequence` subclasses constrained by transaction-level
  randomization
- Subscribe a scoreboard to `monitor`'s `analysis_port`
- Stack virtual sequences for multi-agent coordination
- Use the `uvm_reg::write/read` adapter (RAL → APB transactions)

None of this works through `tb_api` directly. The full agent is the
proper home for it.

## The seven-file agent (v1.2 target)

```
tb/apb_agt_top/
├── apb_agent.sv          # uvm_agent — composes the others
├── apb_agt_config.sv     # uvm_object — knobs (is_active, vif handle, ...)
├── apb_trans.sv          # uvm_sequence_item — addr / data / pwrite / response
├── apb_driver.sv         # uvm_driver — consumes trans, drives apb_if
├── apb_monitor.sv        # uvm_monitor — observes apb_if, publishes trans
├── apb_sequencer.sv      # typedef uvm_sequencer (no extension needed usually)
└── apb_sequence.sv       # uvm_sequence base + reusable building blocks
```

## apb_trans.sv (canonical)

```systemverilog
class apb_trans extends uvm_sequence_item;
    rand logic [11:0] addr;
    rand logic [31:0] data;
    rand bit          write;        // 1 = write, 0 = read
    logic [31:0]      rdata;        // populated by driver/monitor on reads
    bit               slverr;       // captured pslverr

    `uvm_object_utils_begin(apb_trans)
        `uvm_field_int(addr,  UVM_ALL_ON)
        `uvm_field_int(data,  UVM_ALL_ON)
        `uvm_field_int(write, UVM_ALL_ON)
        `uvm_field_int(rdata, UVM_ALL_ON | UVM_NOCOMPARE)
        `uvm_field_int(slverr, UVM_ALL_ON | UVM_NOCOMPARE)
    `uvm_object_utils_end
    function new(string name = "apb_trans"); super.new(name); endfunction
endclass
```

`UVM_NOCOMPARE` on `rdata`/`slverr` is critical: those are response
fields, not stimulus. Scoreboards compare expected vs `rdata`
explicitly, not via `compare()`.

## apb_driver.sv (canonical)

Driver's `drive_one()` task is **the same body** as
`tb_api::write`/`read`. To avoid divergence, generated `apb_driver.sv`
will include `tb_api_primitives.svh` and call `tb_api::write` /
`tb_api::read` after setting `tb_api::vif = m_vif`. This guarantees
the DE persona and DV persona drive the bus identically.

```systemverilog
class apb_driver extends uvm_driver #(apb_trans);
    `uvm_component_utils(apb_driver)
    virtual apb_if vif;
    function new(string n, uvm_component p=null); super.new(n,p); endfunction
    function void build_phase(uvm_phase phase);
        if (!uvm_config_db#(virtual apb_if)::get(this, "", "apb_vif", vif))
            `uvm_fatal("CFG", "apb_vif missing")
        tb_api::set_vif(vif);
    endfunction
    task run_phase(uvm_phase phase);
        apb_trans t;
        forever begin
            seq_item_port.get_next_item(t);
            if (t.write) tb_api::write(t.addr, t.data);
            else         tb_api::read (t.addr, t.rdata);
            t.slverr = vif.pslverr;
            seq_item_port.item_done();
        end
    endtask
endclass
```

## apb_monitor.sv (canonical)

Monitor watches the bus and broadcasts decoded transactions. It does
NOT use `tb_api` (which is a master-side construct).

```systemverilog
class apb_monitor extends uvm_monitor;
    `uvm_component_utils(apb_monitor)
    uvm_analysis_port#(apb_trans) ap;
    virtual apb_if vif;
    function new(string n, uvm_component p=null);
        super.new(n,p); ap = new("ap", this);
    endfunction
    function void build_phase(uvm_phase phase);
        if (!uvm_config_db#(virtual apb_if)::get(this, "", "apb_vif", vif))
            `uvm_fatal("CFG", "apb_vif missing")
    endfunction
    task run_phase(uvm_phase phase);
        apb_trans t;
        forever begin
            // wait for the access phase to complete (pready high)
            @(posedge vif.pclk iff (vif.psel & vif.penable & vif.pready));
            t = apb_trans::type_id::create("t");
            t.addr   = vif.paddr;
            t.write  = vif.pwrite;
            t.data   = vif.pwdata;
            t.rdata  = vif.prdata;
            t.slverr = vif.pslverr;
            ap.write(t);
        end
    endtask
endclass
```

If the DUT does not expose `pready` / `pslverr`, `top.sv` ties
`apb.pready = 1'b1` / `apb.pslverr = 1'b0` at the interface side
(see `references/top_sv.md`). The driver/monitor code above is
unchanged — the `pready`-high wait falls through immediately,
giving correct zero-wait-state APB timing.

## apb_agent.sv (canonical)

```systemverilog
class apb_agent extends uvm_agent;
    `uvm_component_utils(apb_agent)
    apb_driver    drv;
    apb_monitor   mon;
    apb_sequencer sqr;
    apb_agt_config cfg;
    function new(string n, uvm_component p=null); super.new(n,p); endfunction
    function void build_phase(uvm_phase phase);
        if (!uvm_config_db#(apb_agt_config)::get(this, "", "cfg", cfg))
            `uvm_fatal("CFG", "apb_agt_config missing")
        mon = apb_monitor::type_id::create("mon", this);
        if (cfg.is_active == UVM_ACTIVE) begin
            drv = apb_driver  ::type_id::create("drv", this);
            sqr = apb_sequencer::type_id::create("sqr", this);
        end
    endfunction
    function void connect_phase(uvm_phase phase);
        if (cfg.is_active == UVM_ACTIVE) drv.seq_item_port.connect(sqr.seq_item_export);
    endfunction
endclass
```

## What v1.2 provides today

`scripts/scaffold.py` now emits:

1. `tb_api` + `apb_if`
2. the seven-file `tb/apb_agt_top/` tree above
3. `apb_agt_pkg`, imported by `test/<ip>_pkg.sv`
4. `<ip>_random_seq_test`, which runs 100 sequencer-driven APB
   transactions and then reuses the same register-read helper as
   `reg_access_test`

## When to generate the agent vs reuse an existing VIP

| User situation | gen-tb action |
|---|---|
| `apb_vip_source: generate_fresh` | emit the 7 files above (v1.2) |
| `apb_vip_source: reuse_my_vip` + path | scan the user's VIP, emit `tb/external_vip.f`, import it into the compile, and do NOT emit the 7 fresh-agent files |
| (silent on intake) | ask via AskUserQuestion before defaulting |

Both paths are implemented in v1.2, with different maturity levels:

- `generate_fresh` creates the canonical seven-file agent and the
  generated `random_seq_test`
- `reuse_my_vip` defaults to `apb_vip_reuse_level: import_only`: it
  preserves the user's files, emits `tb/external_vip.f`, and keeps the
  built-in tests on `tb_api` instead of guessing arbitrary third-party
  runtime APIs. In import-only mode, scaffold may omit obvious vendor
  test packages such as `*_test_pkg.sv` when those packages are only
  used by the VIP's standalone top and re-include internal env/test
  files.
- `apb_vip_reuse_level: drive_with_vip` means Phase 5 must generate
  project-local glue and one minimal external-VIP read/write smoke
  sequence without editing the user's VIP source

See `references/apb_external_vip.md` for the reuse contract and current
boundary.

## Connection to RAL

The RAL block needs a `uvm_reg_adapter` that turns `uvm_reg_bus_op`
into `apb_trans`. gen-tb emits this as `tb/ral/<ip>_apb_adapter.sv`
when both an agent and a RAL are generated. v1.1 RAL stub does not
have the adapter (since reg_access_test uses tb_api directly).

## Cross-references

- `references/tb_api.md` — the master-side primitives the driver
  reuses
- `references/apb_external_vip.md` — direct import rules for an
  existing user APB VIP
- `references/ral_gen.md` — RAL emission rules; the adapter lives
  there too
- `references/directory_layout.md` — `tb/apb_agt_top/` placement
- `scripts/scaffold.py` `emit_apb_agent_*` (v1.2) — the implementation
