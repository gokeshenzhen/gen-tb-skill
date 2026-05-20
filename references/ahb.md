# AHB-Lite agent generation (gen-tb reference)

> Loaded when `intake.yaml: bus_protocol: ahb` and the user wants a
> generated bus agent instead of an external AHB VIP.

## Scope

The generated AHB support is AHB-Lite slave-oriented:

- single master in the generated testbench
- single NONSEQ word read/write transfers
- 32-bit data bus
- `haddr_width` controls address width, default 12
- no SPLIT/RETRY, bursts, locked transfers, or multi-master arbitration

Use this path only for register-level peripheral verification. If the
DUT requires full AHB system behavior, report that as out of scope.

## Intake

```yaml
bus_protocol: ahb
haddr_width: 12
ahb_vip_source: generate_fresh
```

Fresh generation emits:

```text
tb/ahb_if.sv
tb/ahb_agt_top/
├── ahb_agent.sv
├── ahb_agt_config.sv
├── ahb_trans.sv
├── ahb_driver.sv
├── ahb_monitor.sv
├── ahb_sequencer.sv
└── ahb_sequence.sv
tb/ral/<ip>_ahb_adapter.sv
```

`tb_api::write/read/expect_reg` remain the mandatory DE surface and use
AHB-Lite transfers internally.

## Signals

The generated top expects the DUT to expose these canonical AHB-Lite
slave ports unless compile-fix adds project-local glue:

```systemverilog
hclk, hresetn,
hsel, haddr, htrans, hwrite, hsize, hburst, hprot,
hwdata, hrdata, hready, hresp
```

Record the exact discovered names in `rtl_discovery.yaml:
ahb_interface`. Do not edit user RTL to rename ports.

## External VIP

For user-owned AHB VIP reuse, use `references/ahb_external_vip.md`.

