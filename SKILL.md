---
name: gen-tb
description: Generate a complete UVM testbench scaffold for a single IP from its spec documents (docx/pdf/md) and register table (xlsx/csv/md/IP-XACT). Use whenever the user asks to "generate a UVM tb", "build a verification environment", "scaffold a testbench", "create a UVC", "set up DV for this IP", or anything equivalent in Chinese ("帮我生成验证环境", "搭一个 tb", "做 UVM 环境", "做这个 IP 的验证"). Also trigger when the user points at a directory containing an IP spec and asks Claude to "verify it" / "test it" without naming UVM explicitly. Produces a runnable APB / AHB-Lite / AXI4-Lite-based UVM tb with sanity + register-access tests passing, plus a DE-friendly `tb_api::` task BFM.
---

# gen-tb — UVM Testbench Scaffold Generator

## Purpose

Generate a directory-aligned UVM testbench for one APB / AHB-Lite slave or
AXI4-Lite (slave or master) IP from:

- behavioral spec: `*.docx`, `*.pdf`, `*.md`
- register table: `*.xlsx`, `*.csv`, markdown table, IP-XACT XML
- RTL in place, an external RTL path, or a generated stub

The result must compile, run `<ip>_sanity_test`, run
`<ip>_reg_access_test`, and provide two usage surfaces:

- DV persona: UVM tests, sequences, agent, monitor, RAL hooks
- DE persona: `tb_api::write/read/expect_reg` task-style BFM

## Scope

| Dimension | v1 scope | Planned |
|---|---|---|
| Bus | APB slave, AHB-Lite slave, AXI4-Lite (slave or master) | AXI4 full |
| Simulator | VCS | xrun, vsim |
| Ref model | none, SV, C-DPI, Python-DPI | — |
| Spec input | docx, pdf, md | — |
| Reg input | xlsx, csv, md, IP-XACT | — |

If the user asks for anything outside this table, say so and offer the
closest in-scope alternative. AXI4 full (with bursts/IDs/outstanding) is
out of scope; if the DUT has AXI ports but uses only single-beat
transfers, generate the AXI4-Lite environment and note the assumption.

Do not use this skill for debugging an existing UVM environment, formal
verification, gate-level sim, DFT, or a multi-master SoC subsystem.

## Pipeline

Each phase writes audit artifacts under `<ip>/work/_gen_audit/`.
Do not collapse phases; resumability and auditability are part of the
deliverable.

1. Discover: identify IP, spec, regs, RTL, optional external VIP
2. Intake: ask only unresolved questions
3. Normalize: produce `registers.yaml`, `behavior.md`, `parse_report.md`
4. Scaffold: write the generated tree
5. Compile-fix: run compile and use a constrained sub-agent if needed
6. Sanity: run mandatory tests and runtime-fix loop if needed
7. Hand-off: write `CLAUDE.md`, `unresolved.md`, and concise summary

## Phase 1: Discover

### IP Selection

Pick the IP root in this order:

1. Explicit IP name/path from the user
2. Current directory if its basename looks like an IP name
3. The only child directory containing spec files
4. Otherwise ask the user to choose

Show the selected IP name and path once so the user can correct it.

### Spec And Registers

Search `spec/`, `doc/`, `docs/`, then IP root. Classify:

- register table: `*reg*table*.xlsx/csv`, `*regs*.xlsx/csv`, IP-XACT XML
- behavior spec: large `*.pdf`, `*.docx`, `*spec*.md`
- markdown tables that look like regs: confirm if ambiguous

Do not ingest README, license, unrelated datasheets, draft/old/bak files.

### RTL

Prefer an existing filelist. Otherwise scan `rtl/`, `src/`, `design/`,
`hdl/`, and `<ip_name>/` for RTL, excluding tb/test/sim/bench paths.
If no RTL exists, generate a stub only when the user confirms.

Infer top module using AST tooling if available; regex inference is
medium confidence. If top is ambiguous, ask. Record exact APB / AHB /
AXI4-Lite signal names and widths in `rtl_discovery.yaml`; never
normalize case. For AXI4-Lite, also record which side of the bus the DUT
sits on (`bus_direction: slave|master`) — slave is the default and
mirrors APB/AHB; master means TB provides a slave responder.

For details, load `references/rtl_discovery.md` and
`references/rtl_stub.md`.

### Existing Bus VIP

If the user says they already have an APB, AHB, or AXI4-Lite VIP, ask
for only:

- `<bus>_vip_path`
- desired reuse level: `import_only` or `drive_with_vip`

Use:

```yaml
bus_protocol: apb                  # or ahb, axi_lite
apb_vip_source: reuse_my_vip       # or ahb_vip_source / axi_lite_vip_source
apb_vip_path: <path>               # or ahb_vip_path / axi_lite_vip_path
apb_vip_reuse_level: import_only   # or ahb_vip_reuse_level / axi_lite_vip_reuse_level; default
```

`import_only` means scaffold imports the VIP into the compile tree and
keeps built-in tests on `tb_api`. `drive_with_vip` means Phase 5 must
also generate/adapt glue so the user's VIP can drive a minimal bus
read/write smoke sequence. Do not guess third-party VIP APIs in Phase 4.
For `import_only`, scaffold may skip vendor `*_test_pkg.sv` files that
only serve the VIP's standalone harness.

Load `references/apb_external_vip.md`, `references/ahb_external_vip.md`,
or `references/axi_lite_external_vip.md` before implementing or fixing
external VIP reuse.

## Phase 2: Intake

Ask unresolved items in small batches. Questions to ask only when not
already known:

- RTL state: found, external path, generated stub, none
- bus protocol: APB, AHB, or AXI4-Lite
- bus direction (AXI4-Lite only): DUT is slave (default) or master
- bus VIP source: generate fresh, reuse existing VIP
- external VIP reuse level: import only, drive with VIP
- reference model language: none, SV, C-DPI, Python-DPI
- clock/reset: `pclk/presetn`, `hclk/hresetn`, or `aclk/aresetn` frequency, polarity, reset cycles
- UVM version: default 1.2 unless user specifies otherwise
- address width: `paddr_width`, `haddr_width`, or `axi_addr_width`, default 12
- endianness for multi-word arrays
- coverage: enabled or skipped
- required extra smoke tests

All generated tests must be named `<ip>_<purpose>_test`.

Every question turn must include an abort/restart option. Save partial
answers to `work/_gen_audit/intake.yaml` and resume from it if present.

## Phase 3: Normalize

Create:

```text
work/_gen_audit/spec_normalized/registers.yaml
work/_gen_audit/spec_normalized/behavior.md
work/_gen_audit/spec_normalized/parse_report.md
```

Never proceed to Phase 4 without valid `registers.yaml`. Missing
behavior parsers may degrade to warnings, but a missing/unparseable
register table is blocking.

Field-level reset values win over register-level reset values. Emit
warnings for unparseable offsets, widths, bit ranges, PDF tables,
ambiguous aliases, and contradictions. The user must be told to read
`parse_report.md`.

Load `references/spec_parsing.md` and
`references/registers_yaml_schema.md` for schemas and parser rules.

## Phase 4: Scaffold

Use `scripts/scaffold.py` with the audit inputs. The generated layout is
fixed; load `references/directory_layout.md` for the authoritative tree.

Key scaffold rules:

- write `script/design.f`, not `rtl/design.f`
- write lowercase `script/makefile`
- write `script/tb.f` for tb-side sources
- never copy or modify user RTL
- if `<bus>_vip_source: generate_fresh`, emit `tb/<bus>_agt_top/`
- if `reuse_my_vip`, emit `tb/external_vip.f` and skip fresh agent files
- keep `tb_api` generated in all modes
- generate `top/<ip>_tb_top.sv`, `tb/<bus>_if.sv`, RAL, tests, audit

For APB generation, load `references/apb.md`. For AHB generation, load
`references/ahb.md`. For AXI4-Lite generation, load
`references/axi_lite.md`. For external VIP reuse, load
`references/apb_external_vip.md`, `references/ahb_external_vip.md`, or
`references/axi_lite_external_vip.md`.
For top wiring, load
`references/top_sv.md`. For Makefile, load
`references/makefile_contract.md`. For `tb_api`, load
`references/tb_api.md`. For DPI, load `references/refm_dpi.md`.

## Phase 5: Compile-Fix

Run:

```bash
make comp
```

If compile fails, save the log under
`work/_gen_audit/compile_fix_attempts/attempt_N.log` and use a
constrained sub-agent. The sub-agent may edit only:

- `tb/`
- `top/`
- `test/`
- `script/`

It may not edit user RTL, user VIP source, specs, or register tables.

For `reuse_my_vip`:

- `import_only`: fix filelist/include/order issues needed to compile
- `drive_with_vip`: additionally create generated glue, interface
  bridge, config_db wiring, and one minimal read/write smoke sequence

Stop after 5 compile attempts. Write `unresolved.md` with the last
error, likely cause, and next action. Do not fake success.

Load `references/sub_agent_compile_fix.md` for the prompt template and
constraints.

## Phase 6: Sanity

Run mandatory tests:

```bash
make all SV_CASE=<ip>_sanity_test
make all SV_CASE=<ip>_reg_access_test
```

Sanity must include a positive bus check: read a register with known
non-zero reset when possible; otherwise read register 0 and prove the
DUT responds (`pready` / `hready` / `rvalid`) before timeout. For
AXI4-Lite with `bus_direction: master`, the mandatory test is instead
`<ip>_responder_smoke_test`: the slave responder must observe at least
one valid AW/W or AR handshake from the DUT within a timeout. RAL and
`<ip>_reg_access_test` are not generated for the master direction.

If a mandatory runtime test fails, re-enter Phase 5 with a runtime-fix
sub-agent for up to 3 attempts. Save results to
`work/_gen_audit/sanity_result.json`.

For algorithmic IPs with a reference model, also run one end-to-end
smoke test that proves scoreboard/refmodel integration.

For `drive_with_vip`, also run the generated external-VIP read/write
smoke test.

## Phase 7: Hand-Off

Write:

- `<ip>/CLAUDE.md`
- `<ip>/work/_gen_audit/unresolved.md`
- concise chat summary with exact next command

The summary should state generated IP, tests passed, compile-fix attempt
count, unresolved count, and:

```bash
cd <ip>/script && source setup.sh && make all SV_CASE=<ip>_sanity_test
```

Load `references/generated_claude_md.md` for `CLAUDE.md`.

## Hard Constraints

- Never copy user RTL.
- Never edit user RTL.
- Never edit user VIP source during generation or compile-fix.
- Never write through a symlink that resolves outside the IP root.
- Never silently overwrite without `--force` or backup policy from the
  implementation.
- Never invent registers; RAL is 1:1 from `registers.yaml`.
- Treat TraceWeave MCP, `eda-environment`, and `skill-creator` as
  optional, not hard dependencies.
- If using VCS fails due to license/environment, source the user's shell
  setup if instructed and rerun outside sandbox when needed.

## References

Load only the relevant file for the current phase.

| File | Use |
|---|---|
| `references/directory_layout.md` | generated tree and ownership |
| `references/makefile_contract.md` | makefile API |
| `references/rtl_discovery.md` | RTL discovery schema |
| `references/top_sv.md` | top/interface wiring |
| `references/apb.md` | generated APB agent |
| `references/apb_external_vip.md` | existing APB VIP reuse |
| `references/ahb.md` | generated AHB-Lite agent |
| `references/ahb_external_vip.md` | existing AHB VIP reuse |
| `references/axi_lite.md` | generated AXI4-Lite agent (slave + master directions) |
| `references/axi_lite_external_vip.md` | existing AXI4-Lite VIP reuse |
| `references/spec_parsing.md` | parser rules |
| `references/registers_yaml_schema.md` | normalized registers |
| `references/ral_gen.md` | RAL generation |
| `references/refm_dpi.md` | C/Python DPI |
| `references/tb_api.md` | DE task BFM |
| `references/rtl_stub.md` | generated RTL stub |
| `references/sub_agent_compile_fix.md` | compile-fix sub-agent |
| `references/generated_claude_md.md` | generated CLAUDE.md |

## Evaluation

This skill ships with a three-layer eval harness under `evals/`. See
`evals/README.md` for the operator-facing walkthrough; the contract
below is what the skill promises.

1. **Mechanical assertions** (`scripts/run_evals.py run`) —
   compile/sim must exit clean, log substrings must hit, generated
   files must exist. Deterministic, no LLM. Always run after a change
   to the skill or scaffolder. Per-eval artifacts land in
   `evals/iteration-N/<eval-name>/` (`outputs/`, `transcript.md`,
   `assertions_result.json`).

2. **Per-run quality Grader** (`run_evals.py run --grade`) —
   `claude -p` subprocess that reads `evals/agents/grader.md` and
   judges 8 quality dimensions assertions can't catch (scoreboard
   value, RAL fidelity, `tb_api::` BFM usability, hardcoding risk,
   `unresolved.md` honesty, …). Writes `grading.json` per eval with
   `strong/ok/weak/broken` verdicts and severity counts. Always run
   when iterating on the skill.

3. **Optional Comparator + Analyzer** (`run_evals.py compare A B
   --analyze`) — blind A/B between two iterations or candidate
   variants. Most iterations don't need this — mechanical assertions
   plus the Grader catch regressions. Use Comparator when you want a
   stronger statement that "vN is genuinely better than vN-1" (e.g.
   before promoting a major rewrite, or to pick between two candidate
   skill variants). Analyzer runs after Comparator picks a non-tie
   winner and writes `<eval>.analysis.md` with root-cause hypotheses
   and concrete suggested edits to this SKILL.md or `references/`.

The three agent contracts live in `evals/agents/{grader,comparator,
analyzer}.md`. They are written so the same contracts can be invoked
by the harness (`claude -p`) or by a conversational Claude via the
Task tool — pick whichever fits the iteration loop.

## Done Checklist

- `<ip>/.prj_top` exists
- symlink guard clean
- `make comp` exits 0
- no dual-driver or structural/procedural driver warnings
- `<ip>_sanity_test` passes with a positive bus assertion
- `<ip>_reg_access_test` passes
- required smoke tests pass
- `parse_report.md`, `scaffold_audit.json`, `sanity_result.json`, and
  `unresolved.md` exist
- generated `CLAUDE.md` exists
- user receives concise summary and exact next command
