# Generic-mode scaffold sub-agent prompt (gen-tb reference)

> Loaded during Phase 4 **only** when `bus_protocol == generic`.
> The sub-agent generates the bus-shaped pieces of a generic-mode
> testbench (interface, agent files, `tb_api` body, top wiring) from
> three built-in exemplars and the structured handshake yaml. It is a
> **generative** sub-agent, not a patcher — different role from
> `sub_agent_compile_fix.md`, which reacts to failure logs.

## When to use

Invoke once per generic-mode generation, after `scripts/scaffold.py`
has written the bus-agnostic skeleton (directory tree, filelists,
makefile, audit dirs, placeholder agent files, stub `<bus>_if.sv`).

Do not invoke this sub-agent in built-in (APB/AHB-Lite/AXI4-Lite)
mode. Do not invoke it during compile-fix — that is
`sub_agent_compile_fix.md`'s job, even though that sub-agent loads
this contract for context in generic mode.

## Editable scope

Same hard constraints as compile-fix:

```text
tb/
top/
test/
script/
work/_gen_audit/
```

Forbidden to edit:

```text
rtl/                user RTL
ref_model/          user reference model
vip/                user VIP source
spec/               user spec docs
work/_gen_audit/intake.yaml
work/_gen_audit/rtl_discovery.yaml
work/_gen_audit/bus_handshake.yaml
work/_gen_audit/spec_normalized/registers.yaml
```

Unlike compile-fix (which mostly edits), this sub-agent mostly
**creates** new files under `tb/<bus>_agt_top/`, `tb/`, and `top/`.

## Inputs passed by the Phase 4 driver

```text
- references/generic_bus.md                  (authoritative contract)
- references/apb.md
- references/ahb.md
- references/axi_lite.md
- references/directory_layout.md
- references/top_sv.md
- references/tb_api.md
- work/_gen_audit/bus_handshake.yaml         (authoritative handshake)
- work/_gen_audit/rtl_discovery.yaml         (exact port names/widths)
- work/_gen_audit/spec_normalized/registers.yaml   (only if register_semantics: yes)
- the placeholder files scripts/scaffold.py wrote
```

The sub-agent must NOT read user RTL, user VIP source, or original
spec docs. Phase 3 already normalized everything it needs.

## Prompt template

```text
You are scaffolding the bus-shaped pieces of a generic-mode UVM
testbench produced by gen-tb.

Goal:
- Fill in <bus>_if.sv, the eight-file agent under tb/<bus>_agt_top/,
  the tb_api task bodies, and the bus instantiation in
  top/<ip>_tb_top.sv. Match the directory layout, naming, and
  tb_api signatures defined in references/generic_bus.md and the
  three exemplar references.

Inputs:
- IP root: <absolute ip root>
- Authoritative contract: references/generic_bus.md
- Exemplars (do NOT copy verbatim, pattern-match shape):
    references/apb.md, references/ahb.md, references/axi_lite.md
- Layout: references/directory_layout.md, references/top_sv.md,
  references/tb_api.md
- bus_handshake.yaml: work/_gen_audit/bus_handshake.yaml
- rtl_discovery.yaml: work/_gen_audit/rtl_discovery.yaml
- registers.yaml: work/_gen_audit/spec_normalized/registers.yaml
  (skip if register_semantics: no)
- Placeholder files already written by scripts/scaffold.py

Editable scope:
- tb/
- top/
- test/
- script/
- work/_gen_audit/

Do not edit:
- rtl/, ref_model/, vip/, spec/
- intake.yaml, rtl_discovery.yaml, bus_handshake.yaml, registers.yaml

Rules:
- Pick the implementation of handshake per
  bus_handshake.yaml.handshake.kind (req_ack | valid_ready | strobe
  | custom). Honor the rules in references/generic_bus.md verbatim.
- Take all port names, widths, and reset polarity from
  rtl_discovery.yaml and bus_handshake.yaml. Never normalize case.
  Never widen a port beyond its recorded width.
- Do NOT invent control signals, do NOT add protocol-legality
  assertions the spec did not state.
- Keep tb_api task signatures stable. Only fill in their bodies.
  For register_semantics: no, omit expect_reg and drop the addr
  argument if addr is null.
- For register_semantics: yes, the RAL adapter must route through
  the generated driver via the sequencer, not through tb_api.
- When bus_handshake.yaml is silent on a detail, pick the narrower
  interpretation (single-beat, blocking, no back-pressure). Log
  every such choice under "## Assumptions made by sub-agent" in
  work/_gen_audit/generic_bus_scaffold_prompt.md.
- All filenames lowercase, prefixed by bus_handshake.yaml.bus_name.

Output:
- Write the files directly into the IP tree.
- Write work/_gen_audit/generic_bus_scaffold_prompt.md containing:
    * the inputs you actually used,
    * the assumption list (one bullet per ambiguity resolved),
    * a one-line summary of each generated file's role.
- Report changed files in your reply.
- Do not run make comp — Phase 5 owns compile.
```

## Output the sub-agent must produce

Files (under the IP tree, paths relative to IP root):

```text
tb/<bus>_if.sv
tb/<bus>_agt_top/<bus>_pkg.sv
tb/<bus>_agt_top/<bus>_agt_config.sv
tb/<bus>_agt_top/<bus>_trans.sv
tb/<bus>_agt_top/<bus>_driver.sv
tb/<bus>_agt_top/<bus>_monitor.sv
tb/<bus>_agt_top/<bus>_sequencer.sv
tb/<bus>_agt_top/<bus>_seq_lib.sv
tb/<bus>_agt_top/<bus>_agent.sv
top/<ip>_tb_top.sv          (bus_if instance + DUT bind only — env wiring may already exist)
tb/<ip>_tb_api.sv or equivalent   (bodies of write/read/expect_reg; signatures untouched)
```

Audit artifact (mandatory):

```text
work/_gen_audit/generic_bus_scaffold_prompt.md
```

Optional (if any RAL stub was skipped per the addr: null exception):

```text
work/_gen_audit/generic_bus_ral_skipped.md
```

## Failure mode

If the sub-agent cannot satisfy the contract (e.g. `bus_handshake.yaml`
contradicts `rtl_discovery.yaml`, or `handshake.kind: custom` notes
are too sparse to produce a driver):

- Do NOT emit half-built files that will silently fail compile.
- Write `work/_gen_audit/generic_bus_scaffold_blocker.md` describing
  the contradiction and the missing information.
- Exit. Phase 4 will surface this to the user as an actionable
  intake gap, not a compile-fix problem.

## Relationship to compile-fix

If compile-fix later regenerates one of these files end-to-end (per
the "structural error" escape hatch in `references/generic_bus.md`),
it must produce a file consistent with this contract. The
compile-fix attempt log records that the regeneration happened; the
two sub-agents do not share state otherwise.
