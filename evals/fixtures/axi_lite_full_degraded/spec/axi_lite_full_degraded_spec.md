# axi_lite_full_degraded

An AXI4-Lite slave that exposes full-AXI burst/ID ports
(`AWLEN`/`ARLEN`/`AWBURST`/`ARBURST`/`AWID`/`ARID`/`WLAST`/`RLAST`)
but only ever services single-beat transfers.

Used to exercise the gen-tb **AXI4-full degraded mode**:

- Phase 1 detector finds the full-AXI signals → sets
  `axi_full_signature: true` in `rtl_discovery.yaml`.
- Phase 2 mandatory question would ask the user "does the DUT use
  bursts?" — for this fixture the answer is *no*, so the AXI4-Lite
  environment is generated in degraded mode.
- The generated interface carries the burst/ID ports plus
  `AWLEN == 0` / `ARLEN == 0` concurrent assertions.
- The TB master ties the burst signals to single-beat values.

One read-only register: `ID` at offset 0x0, reset `0x000000A5`.
