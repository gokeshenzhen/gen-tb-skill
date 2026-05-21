# generic_near_apb_renamed

A register slave with APB-shaped semantics but vendor-renamed ports.
Used as a generic-bus fixture for the gen-tb skill — the scaffolder
must not pattern-match this to first-class APB. Instead the generic
handshake (`req_ack` between {sel,en} and rdy) plus
`register_semantics: yes` must produce a working RAL-backed tb.

Two registers:

| Offset | Name    | Access | Reset       | Notes |
|--------|---------|--------|-------------|-------|
| 0x00   | ID      | RO     | 0x000000C3  | constant |
| 0x04   | SCRATCH | RW     | 0x00000000  | writable |

Handshake: assert `bus_sel` + `bus_en` together for one cycle; the
slave drives `bus_rdy=1` one cycle later with `bus_rdata` valid on
reads. (APB ENABLE/READY semantics under different names.)
