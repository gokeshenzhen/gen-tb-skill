# generic_valid_ready_stream

A trivial valid/ready streaming master with no register interface.
Used as a generic-bus fixture for the gen-tb skill — exercises:

- `bus_protocol: generic`
- `direction: master` (DUT initiates)
- `handshake.kind: valid_ready`
- `register_semantics: no` (no addr, no RAL, responder_smoke instead of reg_access)

After reset the DUT asserts `tvalid_o` with `tdata_o = 0xDEADBEEF`
and waits for `tready_i`. Once the handshake completes the DUT idles.

The scaffold sub-agent
(`references/sub_agent_generic_scaffold.md`) is responsible for
filling in the slave-side responder driver/monitor/tb_api bodies.
