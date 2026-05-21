# AXI4-Lite agent generation (gen-tb reference)

> Loaded when `intake.yaml: bus_protocol: axi_lite` and the user wants a
> generated bus agent instead of an external AXI4-Lite VIP.

## Scope

The generated AXI4-Lite support covers two directions:

- `bus_direction: slave` (default) — DUT is the AXI4-Lite slave; TB
  provides a master BFM. Mirrors the APB/AHB-slave flow exactly:
  `tb_api::write/read`, full UVM agent, RAL adapter,
  `<ip>_sanity_test`, `<ip>_reg_access_test`, `<ip>_random_seq_test`.
- `bus_direction: master` — DUT is the AXI4-Lite master; TB provides a
  memory-backed slave responder. RAL is not generated (the DUT is not a
  register block). The mandatory test is `<ip>_responder_smoke_test`,
  which asserts the DUT issues at least one valid AW/W or AR handshake
  before a timeout. `tb_api::` exposes responder helpers
  (`seed_mem`, `peek_mem`, `wait_for_write`, `wait_for_read`).

Restrictions:

- single outstanding transaction (`AWVALID`/`ARVALID` not asserted
  again until the response handshake completes)
- 32-bit data bus, `wstrb` = `4'b1111` on writes
- no `awprot`/`arprot` checks (driven to `3'b000`, ignored on the
  responder side)
- no exclusive or atomic access
- no IDs (this is AXI4-Lite, not AXI4)

If the DUT requires bursts, IDs, or outstanding transactions, report
that as out of scope.

## Intake

```yaml
bus_protocol: axi_lite
bus_direction: slave          # or master
axi_addr_width: 12            # default 12
axi_data_width: 32            # only 32 supported in v1
axi_lite_vip_source: generate_fresh
```

For `bus_direction: master` the responder uses an SRAM-style memory
backing whose depth is `1 << axi_addr_width`.

Fresh generation emits:

```text
tb/axi_lite_if.sv
tb/axi_lite_agt_top/
├── axi_lite_agent.sv
├── axi_lite_agt_config.sv
├── axi_lite_trans.sv
├── axi_lite_driver.sv         # master BFM (slave-DUT) or slave responder (master-DUT)
├── axi_lite_monitor.sv
├── axi_lite_sequencer.sv
└── axi_lite_sequence.sv
tb/ral/<ip>_axi_lite_adapter.sv   # only when bus_direction: slave
```

`tb_api::write/read/expect_reg` are the mandatory DE surface for the
slave direction. For the master direction, `tb_api::` exposes the
responder side (see "Master-direction tb_api" below).

## Signals (slave direction — DUT is slave)

```systemverilog
aclk, aresetn,
awvalid, awready, awaddr, awprot,
wvalid,  wready,  wdata,  wstrb,
bvalid,  bready,  bresp,
arvalid, arready, araddr, arprot,
rvalid,  rready,  rdata,  rresp
```

Record exact discovered names in `rtl_discovery.yaml:
axi_lite_interface`. Do not edit user RTL to rename ports.

## Signals (master direction — DUT is master)

Same set, but the DUT drives `awvalid/awaddr/awprot/wvalid/wdata/wstrb/
bready/arvalid/araddr/arprot/rready`, and the TB slave responder drives
`awready/wready/bvalid/bresp/arready/rvalid/rdata/rresp`.

## Master-direction tb_api

When `bus_direction: master`, `tb_api::` exposes the responder helpers
(the DUT initiates transactions; the TB cannot start them):

- `seed_mem(addr, data)`        — preload the responder memory
- `peek_mem(addr) -> data`      — read the responder memory
- `wait_for_write(timeout_cycles)`  — block until DUT issues one AW/W
- `wait_for_read(timeout_cycles)`   — block until DUT issues one AR
- `expect_observed_write(addr, data)` — after a write, assert what
  the responder captured

`tb_api::write/read` are not emitted in master mode (the DUT is the
master).

## RAL adapter

Only generated for `bus_direction: slave`. The adapter uses the same
`reg2bus`/`bus2reg` shape as APB/AHB but with `axi_lite_trans` fields
(`addr`, `data`, `write`, `rdata`, `resp`).

## Driver/responder

- Slave direction: `axi_lite_driver` is a master BFM that issues one
  write or read per `seq_item_port.get_next_item()`, identical body to
  `tb_api::write/read`.
- Master direction: `axi_lite_driver` is a slave responder. It
  `accept_phase` accepts any incoming `awvalid`/`arvalid`, records the
  transaction into a `uvm_analysis_port` (so the monitor publishes
  observed traffic), and uses an internal associative array as the
  backing memory.

## AXI4-full degraded mode (`axi_full_signature: true`)

When the Phase 1 detector finds AXI-full signals
(`AWLEN`/`ARLEN`/`AWBURST`/`ARBURST`/`AWID`/`ARID` with width > 0)
on the DUT, the Phase 2 mandatory question routes the user to one of:

- **Yes, the DUT uses bursts/IDs/outstanding** → hard refuse,
  out of scope.
- **No, single-beat only** → set `axi_full_signature: true` in
  `rtl_discovery.yaml` and generate the AXI4-Lite environment in
  **degraded mode**. This is what this section describes.

In degraded mode the scaffolder:

1. **Interface (`emit_axi_lite_if`)** — declares the extra ports
   `awlen[7:0]`, `awsize[2:0]`, `awburst[1:0]`, `awid[ID_W-1:0]`,
   `arlen[7:0]`, `arsize[2:0]`, `arburst[1:0]`, `arid[ID_W-1:0]`,
   `bid[ID_W-1:0]`, `rid[ID_W-1:0]`, `wlast`, `rlast` alongside
   the standard Lite signals. `ID_W` defaults to `axi_full_id_width`
   from `rtl_discovery.yaml` (default 1).

2. **Top-level wiring (`emit_tb_top`)** — adds the burst/ID ports
   to the DUT instance so VCS doesn't complain about unconnected
   `output` ports the DUT actually drives.

3. **Master tb_api primitives** (slave direction = TB master) —
   in `_idle`, `write`, and `read` the BFM ties `AWLEN=0`,
   `ARLEN=0`, `AWBURST=01`/`ARBURST=01` (INCR), `AWSIZE=ARSIZE=010`
   (4-byte), `WLAST=1`, and `AWID=ARID=0`. This guarantees the DUT
   sees a legal single-beat AXI transaction regardless of what
   the protocol-checker downstream expects.

4. **Monitor (`emit_axi_lite_monitor`)** — adds two `assert
   property` statements gated on `aresetn`:

   ```sv
   property p_awlen_zero;
       @(posedge vif.aclk) disable iff (!vif.aresetn)
           (vif.awvalid && vif.awready) |-> (vif.awlen == 8'h00);
   endproperty
   property p_arlen_zero;
       @(posedge vif.aclk) disable iff (!vif.aresetn)
           (vif.arvalid && vif.arready) |-> (vif.arlen == 8'h00);
   endproperty
   ```

   On violation: `uvm_fatal("AXI_FULL_DEGRADED", ...)`. Sim fails
   loud rather than relying on user review.

5. **Audit** — `scaffold_audit.json` carries
   `axi_full_signature: true` and `axi_full_id_width: <n>` for any
   downstream check.

The degraded mode does **not** add support for bursts, IDs, or
outstanding requests anywhere in the generated tb. The intent is
narrow: connect the DUT's full-AXI ports cleanly, drive them as
single-beat from the TB side, and trap any actual burst issued by
the DUT before it causes silent miscompare downstream.

## External VIP

For user-owned AXI4-Lite VIP reuse, see
`references/axi_lite_external_vip.md`.

## Cross-references

- `references/tb_api.md` — primitives the slave-direction driver reuses
- `references/directory_layout.md` — `tb/axi_lite_agt_top/` placement
- `references/ral_gen.md` — adapter rules
- `scripts/scaffold.py` `emit_axi_lite_*` — implementation
