# AHB-Lite agent generation (gen-tb reference)

> Loaded when `intake.yaml: bus_protocol: ahb` and the user wants a
> generated bus agent instead of an external AHB VIP.

## Scope

The generated AHB-Lite support covers two directions:

- `bus_direction: slave` (default) — DUT is the AHB-Lite slave; TB
  drives a single master BFM. Full agent + RAL adapter +
  `<ip>_sanity_test`, `<ip>_reg_access_test`, `<ip>_random_seq_test`
  on `tb_api::write/read`.
- `bus_direction: master` — DUT is the AHB-Lite master; TB provides a
  memory-backed slave responder (zero-wait-state, drives `hready=1`,
  captures NONSEQ writes into `tb_api::_mem`, serves reads from it).
  RAL is not generated; mandatory test is
  `<ip>_responder_smoke_test`.

Restrictions in both directions:

- single master / single slave
- single NONSEQ word read/write transfers (no SEQ bursts, no SPLIT/
  RETRY, no locked transfers, no multi-master arbitration)
- 32-bit data bus
- `haddr_width` controls address width, default 12

Use this path only for register-level peripheral verification (slave)
or single-beat traffic verification (master). If the DUT requires full
AHB system behavior, report that as out of scope.

## Intake

```yaml
bus_protocol: ahb
bus_direction: slave          # or master
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

`tb_api::write/read/expect_reg` remain the mandatory DE surface for
the slave direction and use AHB-Lite transfers internally. For the
master direction, `tb_api::` exposes responder helpers (`seed_mem`,
`peek_mem`, `wait_for_write`, `wait_for_read`,
`expect_observed_write`, `clear_observed`) — `write`/`read` are not
emitted because the DUT is the master.

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

