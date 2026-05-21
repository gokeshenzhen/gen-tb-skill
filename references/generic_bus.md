# Generic bus mode — scaffold and compile-fix contract

> Loaded during Phase 4/5 **only** when `bus_protocol == generic`.
> Built-in buses (APB / AHB-Lite / AXI4-Lite) never load this file —
> they have first-class scaffolders. AXI signal sets must never reach
> this file (see SKILL.md Phase 1 / Phase 2 AXI4-full rules).
>
> This file is the contract handed to the scaffold sub-agent
> (`references/sub_agent_generic_scaffold.md`) and is additionally
> loaded by the compile-fix sub-agent when it operates on generic-mode
> output.

## Why generic mode exists

The three built-in buses share one UVM agent shape: interface,
driver, monitor, sequencer, sequence library, transaction object,
`tb_api` task BFM, top-level wiring. Most simple register/IO buses
(I2C, SPI, Wishbone, OBI, custom register buses, simple req/ack
slaves) fit the same shape — only the handshake details and signal
names differ. Generic mode reuses the layout and lets a constrained
sub-agent infer the bus-shaped pieces from three exemplars and the
structured `bus_handshake.yaml` collected in Phase 2.

Generic mode is **best-effort**. The skill cannot verify protocol
correctness it was never taught; the generated `CLAUDE.md` must carry
the manual review checklist (§Manual review checklist below) so the
user knows what to look at.

## Inputs to the scaffold sub-agent

The Phase 4 driver code MUST pass all of these to the sub-agent:

- `work/_gen_audit/bus_handshake.yaml`  *(authoritative handshake spec)*
- `work/_gen_audit/rtl_discovery.yaml`  *(exact port names and widths)*
- `work/_gen_audit/spec_normalized/registers.yaml`  *(only when
  `register_semantics: yes`)*
- Exemplars: `references/apb.md`, `references/ahb.md`,
  `references/axi_lite.md`
- Layout contracts: `references/directory_layout.md`,
  `references/top_sv.md`, `references/tb_api.md`
- The placeholder files `scripts/scaffold.py` already wrote into the
  generated tree (empty `<bus>_agt_top/` shells, stub `<bus>_if.sv`)

The sub-agent must not read user RTL beyond what
`rtl_discovery.yaml` already exposes, and must not read the original
spec docs — Phase 3 already normalized everything it needs.

## Required outputs

```
tb/<bus>_if.sv
tb/<bus>_agt_top/
    <bus>_agent.sv
    <bus>_agt_config.sv
    <bus>_trans.sv
    <bus>_driver.sv
    <bus>_monitor.sv
    <bus>_sequencer.sv
    <bus>_seq_lib.sv      # minimum: reset_seq, single_write_seq, single_read_seq
    <bus>_pkg.sv
top/<ip>_tb_top.sv         # interface instantiated, bound to DUT
tb_api task body           # write/read/expect_reg; read/write only when register_semantics: no
```

Naming rules:
- All filenames lowercase.
- Prefix = `bus_name` from `bus_handshake.yaml` (e.g. `wb_if.sv`,
  `i2c_agent.sv`).
- No deviation from `references/directory_layout.md`.
- `tb_api::write/read/expect_reg` signatures stay stable — this is
  the DV/DE-facing surface contract. Generic mode never changes
  these task names or argument lists; only their bodies.

`tb_api::expect_reg` semantic contract (must match built-in buses):
- On success: emit `\`uvm_info(tag, $sformatf("@0x%0h = 0x%08h", addr, got), UVM_LOW)`.
  This success print is load-bearing — the eval harness and human
  reviewers look for it as proof the bus actually moves data.
- On mismatch: emit `\`uvm_fatal(tag, ...)` with addr/got/expected.
- Do NOT remove the success print when rewriting the read path.

Register-less buses (`register_semantics: no`):
- Do not generate RAL, RAL package, or `<ip>_reg_access_test`.
- `tb_api` exposes only `write(addr_or_token, data)` and
  `read(addr_or_token, ref data)`. If `addr: null`, the token
  argument is omitted entirely.

## Handshake kinds — behavioral rules

The sub-agent MUST select implementation per
`bus_handshake.yaml.handshake.kind`:

### `req_ack`
- Driver: assert `req` high in the clocking block, hold stimulus
  stable, wait for `ack` to assert, sample read data **on the cycle
  ack is sampled high**, deassert `req` next cycle.
- Monitor: identical sampling rule, never drives `req`/`ack`.
- No req-without-ack timeout in driver — the testbench timeout (set
  in `<ip>_tb_top.sv`) is the only watchdog.

### `valid_ready`
- Driver: standard `valid → ready` protocol. Once `valid` is
  asserted with payload, do not change payload until the cycle
  `ready` is sampled high.
- Driver MAY honor backpressure on the receive direction.
- Monitor MUST NOT assert `ready`. The monitor passively samples on
  `valid && ready`.

### `strobe`
- Single-cycle strobe. Data must be stable on the strobe cycle.
- Driver: pulse strobe one clock, present data on the same clock.
- Monitor: sample on strobe-high; do not look at adjacent cycles.

### `custom`
- Follow `bus_handshake.yaml.notes` verbatim.
- Do not invent protocol detail not present in the notes.
- If notes are insufficient, write a TODO comment naming the
  ambiguity and pick the narrower interpretation (e.g. single-beat,
  no back-pressure, blocking driver).

Common rules for all kinds:
- Reset behavior taken verbatim from `bus_handshake.yaml.reset`
  (port, polarity, cycles). Driver and monitor both deassert outputs
  during reset.
- Burst: only single-beat unless `burst: simple_incr`. `simple_incr`
  means a sequence of single-beat transactions with the address
  pre-incremented by the driver; do not add AXI-style burst control
  signals.
- Address-less buses (`addr: null`): RAL is forbidden even if
  `register_semantics` was set to `yes` by mistake — in that case
  emit a Phase 4 warning and proceed as `register_semantics: no`.

## Forbidden behavior

- Inventing control signals not present in `bus_handshake.yaml` or
  `rtl_discovery.yaml`.
- Adding protocol-legality assertions the spec did not state
  (e.g. "ack must drop within N cycles"). Such assertions cause
  silent test failures whose root cause looks like a DUT bug.
- Widening data/addr ports beyond what `rtl_discovery.yaml` records.
- Changing `tb_api` task signatures.
- Reading user RTL files or user VIP source.
- Writing outside `tb/ top/ test/ script/`.

## Ambiguity policy

When `bus_handshake.yaml` is silent on a detail, the sub-agent must:

1. Choose the **narrower** interpretation (single-beat over burst,
   blocking over pipelined, no back-pressure over honored
   back-pressure).
2. Log the choice — both the question and the chosen answer — in
   `work/_gen_audit/generic_bus_scaffold_prompt.md` under a
   `## Assumptions made by sub-agent` heading.
3. Surface the same assumption list verbatim into the manual review
   checklist of the generated `<ip>/CLAUDE.md`.

## Audit artifacts

Phase 4 must produce, in addition to the standard scaffold audit:

- `work/_gen_audit/generic_bus_scaffold_prompt.md` — the actual
  prompt and inputs handed to the sub-agent.
- `work/_gen_audit/generic_bus_scaffold_diff.patch` — unified diff
  of the sub-agent's writes against the placeholder skeleton.

These exist so a maintainer can later read a successful generic-mode
run and decide whether to promote that bus to a first-class
reference (see repo-root `CLAUDE.md` for the promotion rule).

## Compile-fix sub-agent — additions in generic mode

When the compile-fix loop runs in generic mode (`bus_protocol ==
generic`), the constrained sub-agent in
`references/sub_agent_compile_fix.md` additionally:

- Loads this file plus the three exemplars (`apb.md`, `ahb.md`,
  `axi_lite.md`) so it can pattern-match against built-in buses.
- May **regenerate** a whole bus-agent file from scratch (instead of
  patching) once per file, when the attempt log shows structural
  errors rather than typos. Each regeneration is recorded in
  `compile_fix_attempts/attempt_N.note.md` and counts as one
  attempt against the (8-attempt) generic budget.
- May revise the interface clocking block when a sampling-skew error
  is the diagnosed root cause.
- Must not change `tb_api` task signatures.
- All other compile-fix constraints from
  `references/sub_agent_compile_fix.md` still apply.

## Manual review checklist (copied into generated CLAUDE.md)

Generic-mode deliverables ship with the checklist below in the
generated `<ip>/CLAUDE.md`. Phase 7 is responsible for the copy.

```
## Generic-mode review checklist
This testbench was generated in generic-bus mode. The skill cannot
verify protocol correctness it was never taught. Before relying on
this tb, review:

- [ ] Driver setup/hold against spec (especially req_ack and strobe
      modes — verify data is stable on the sampling cycle).
- [ ] Monitor sample timing matches the spec's data-valid window.
- [ ] Reset deassert cycle count matches DUT expectation.
- [ ] tb_api::write/read behavior on back-to-back transactions.
- [ ] If register_semantics: yes — spot-check 1–2 RW registers have
      correct addr / width / reset wired through RAL.
- [ ] Read the assumption list in
      work/_gen_audit/generic_bus_scaffold_prompt.md — each entry is
      a place the sub-agent picked the narrower interpretation.
```

## Eval expectations

`evals/fixtures/generic_bus_*` covers at minimum:
- a simple `req_ack` bus with registers,
- a `valid_ready` bus without registers,
- a "near-APB" custom register bus with renamed signals.

Mechanical assertions verify that `bus_handshake.yaml` and
`generic_bus_scaffold_prompt.md` both exist after a generic-mode
run, on top of the usual compile/sim substring checks. The grader
rubric (`evals/agents/grader.md`) adds a "generic-mode honesty"
dimension: the generated tb must log its protocol assumptions and
surface them in `CLAUDE.md`, not silently pretend correctness.
