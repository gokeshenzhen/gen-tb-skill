# generic_req_ack_simple

A trivial req_ack-handshake DUT used as the seed generic-bus fixture
for the gen-tb skill. Not based on any real protocol — picks the
narrowest possible single-channel request/response shape:

- `stb_i` asserts a request alongside `cyc_i` (Wishbone-classic style)
- `ack_o` pulses high one cycle later with `dat_o` valid on reads
- `we_i` selects write (`dat_i`) vs read (returns `0x000000A5`)
- single-beat only

Used to confirm the gen-tb scaffolder emits a generic-mode skeleton
that compiles. The scaffold sub-agent
(`references/sub_agent_generic_scaffold.md`) is responsible for
filling in the driver/monitor/`tb_api` bodies to actually drive this
handshake.
