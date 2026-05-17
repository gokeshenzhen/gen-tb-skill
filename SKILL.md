---
name: gen-tb
description: Generate a complete UVM testbench scaffold for a single IP from its spec documents (docx/pdf/md) and register table (xlsx/csv/md/IP-XACT). Use whenever the user asks to "generate a UVM tb", "build a verification environment", "scaffold a testbench", "create a UVC", "set up DV for this IP", or anything equivalent in Chinese ("帮我生成验证环境", "搭一个 tb", "做 UVM 环境", "做这个 IP 的验证"). Also trigger when the user points at a directory containing an IP spec and asks Claude to "verify it" / "test it" without naming UVM explicitly — testbench scaffolding is the natural answer. Produces a runnable APB-based UVM tb with sanity + register-access tests passing, plus a DE-friendly task BFM (`tb_api::`) so design engineers can drive the DUT without writing UVM sequences.
---

# gen-tb — UVM testbench scaffold generator

## What this skill does

Takes an IP directory containing some combination of:
- a behavioral specification (`*.docx`, `*.pdf`, `*.md`)
- a register table (`*.xlsx`, `*.csv`, `*.md` table, `*.xml` IP-XACT)
- the synthesizable RTL (or nothing yet — a stub can be generated)

…and produces a directory-aligned UVM testbench that **compiles, elaborates,
and passes a sanity test plus a register-access test on the first try**.
The generated tb is meant to be handed straight to two distinct users:

- **DV engineers** extend it the conventional way: new `uvm_test`, new
  sequences under `tb/seq_lib/`, new virtual sequences for multi-agent
  coordination.
- **DE engineers** drive the tb through `tb_api::` — a task-style BFM
  package that wraps the UVM machinery so they can write directed test
  vectors that look like RTL bench code, without needing to know UVM.

## When NOT to use this skill

- The user wants to debug an *existing* UVM testbench — use the
  TraceWeave MCP and `eda-environment` skill directly.
- The user wants SystemVerilog / UVM tutorials or concept explanations —
  answer inline; do not scaffold a project for a question.
- The user wants formal verification, gate-level sim, or DFT scaffolding —
  out of scope for v1.
- The IP is a full SoC subsystem with multiple bus masters and complex
  interconnect — gen-tb v1 targets single IP, single APB-slave interface.

## Scope of v1 (hard limits)

| Dimension | Supported | Planned |
|---|---|---|
| Bus protocol | APB only | AHB, AXI-Lite |
| Simulator | VCS only | xrun, vsim |
| Reference model language | SystemVerilog, C-DPI, Python-DPI, none | — |
| Spec input | docx, pdf, md | — |
| Reg-table input | xlsx, csv, md table, IP-XACT XML | — |

If the user asks for something outside v1, say so clearly and offer the
closest in-scope alternative. **Do not silently generate something the
user did not ask for** (e.g., don't generate an AXI agent just because
the DUT looks like it has AXI ports — refuse politely and explain v1
limits).

---

## Pipeline overview

The skill runs through 7 phases. Each phase writes its outputs into
`<ip>/work/_gen_audit/` so the work is resumable and auditable. **Do not
collapse phases into one big step** — the audit trail is what lets the
user trust an autonomous tool.

```
1. Discover     →  identify IP, find spec / regs / RTL
2. Intake       →  fill remaining unknowns via AskUserQuestion
3. Normalize    →  docx/pdf/xlsx → structured yaml + plain md
4. Scaffold     →  materialize the directory tree + UVM code
5. Compile-fix  →  iterate up to 5 rounds via sub-agent until clean
6. Sanity       →  run test_sanity + reg_access_test, confirm pass
7. Hand-off     →  write CLAUDE.md, unresolved.md, summary to user
```

---

## Phase 1: Discover

The user typically invokes the skill from somewhere inside their project
tree, with the IP they want verified either being the current directory
itself or a child of it.

### IP identification

Apply these rules in order; stop at first match:

1. If the user said the IP name in their request (e.g., "generate i3c
   tb"), use that.
2. Else if `basename $PWD` looks like an IP name (no spaces, not a
   generic name like `project`/`work`/`tmp`), use it.
3. Else if cwd has exactly one child directory that contains spec files,
   use that directory's name.
4. Else use `AskUserQuestion` with the candidate directories listed.

Always show the chosen IP name + path to the user once and let them
correct it before proceeding. Don't ask via `AskUserQuestion` if (1) or
(2) gave an unambiguous answer — that's friction for no gain.

### Spec discovery

Inside the IP directory, look for spec inputs in this order of
preference:

```
./spec/    →  ./doc/    →  ./docs/    →  (IP root, flat)
```

For each candidate file, classify by extension and name hint:

| Pattern | Classification |
|---|---|
| `*reg*table*.{xlsx,csv}` / `*regs*.{xlsx,csv}` | register table |
| `*.xml` containing `<ipxact:`, `<spirit:` | register table (IP-XACT) |
| `*.docx`, `*.pdf` (large), `*spec*.md` | behavior spec |
| `*.md` containing a markdown table that looks like regs | both candidates — confirm |

Do **not** ingest README files, license files, datasheets unrelated to
the IP, or files explicitly named `*draft*` / `*old*` / `*bak*`.

### RTL discovery

In order of preference (stop at first that works):

1. **Existing filelist**: `./rtl/*.f`, `./rtl/*.flist`, `./*.f` —
   single-source-of-truth, trust it, do not regenerate.
2. **Scan candidate dirs**: `./rtl/`, `./src/`, `./design/`, `./hdl/`,
   `./<ip_name>/` for `*.v`, `*.sv`, `*.svh`, `*.vh`. Exclude any path
   segment containing `tb`, `test`, `sim`, `bench`.
3. **External path** declared by user during intake.
4. **No RTL at all** → generate a stub from the spec's interface
   description (see `references/rtl_stub.md`).

Top-module inference: find modules that are defined but not instantiated
anywhere. If multiple candidates, prefer the one whose name matches the
IP name, else `AskUserQuestion`.

Write `work/_gen_audit/rtl_discovery.yaml` describing what was found
including a confidence label per file (`high` = user-supplied filelist,
`medium` = scan-inferred, `low` = guess).

### VIP discovery (when user has existing AMBA UVCs)

If the user mentions they have an existing APB VIP, ask only for a
path. Then auto-infer:

- Scan the path for `*_pkg.sv`, `*_agent.sv`, `*_trans*.sv`,
  `*_if.sv` / `*_intf.sv`.
- Extract package names from `package X;` declarations.
- Infer the agent class as the one extending `uvm_agent` whose name
  contains `apb`.
- Infer transaction / config / interface types from class extends and
  parameterization.

Only fall back to `AskUserQuestion` for items the heuristic can't
resolve. Generating a usable wiring from a path + minimal user input is
the goal — never make the user fill a 10-field form.

---

## Phase 2: Intake

After discovery, you have partial knowledge. Fill the rest with **one
AskUserQuestion turn at a time**, batched into ≤4 questions per turn so
the user isn't death-by-survey'd.

Questions to ask (only if not already answered by discovery or prior
conversation):

1. **RTL state** — found / external path / generate stub / no RTL needed
2. **Bus protocol** — APB (v1 only) — confirm
3. **APB VIP source** — generate fresh / reuse my VIP at `<path>`
4. **Reference model language** — SystemVerilog / C-DPI / Python-DPI / skip
5. **Clock & reset polarity** — `pclk` freq (MHz) + `presetn` active-low
6. **Coverage** — enable functional cov + scoreboard / skip
7. **Sanity test additions** — beyond default `test_sanity` +
   `reg_access_test`, anything you want guaranteed?

Save answers to `work/_gen_audit/intake.yaml` as you go. **If the user
interrupts and resumes later, read intake.yaml first** and skip already
answered items.

---

## Phase 3: Normalize spec

Spec files come in many formats; downstream generation code reads only
two artifacts:

```
work/_gen_audit/spec_normalized/
├── registers.yaml      # canonical register schema
├── behavior.md         # plain text + markdown headings extracted from spec
└── parse_report.md     # confidence per section, ambiguities, warnings
```

### Parsing routes

| Input | Parser | Notes |
|---|---|---|
| `*.xlsx` reg table | `python-openpyxl` | schema in `references/registers_yaml_schema.md` |
| `*.csv` reg table | stdlib `csv` | same schema |
| `*.md` reg table | regex on table rows | brittle — flag low confidence |
| `*.xml` IP-XACT | `lxml` or stdlib `xml.etree` | highest confidence |
| `*.docx` behavior | `python-docx` | preserve heading levels |
| `*.pdf` behavior | `pdfplumber` text + tables | text-only spec; tables become "tables-in-pdf" warnings |
| `*.md` behavior | passthrough | already normalized |

If a parser is not installed, write a clear error to `parse_report.md`
and surface it to the user **before** scaffolding — do not generate
half-correct code.

### parse_report.md is a trust boundary

Any of these go in parse_report.md, with a heading the user can scan:

- Registers whose offset / width / reset value could not be parsed
- Fields with unparseable bit ranges
- Behavior sections under 50 characters (likely truncated)
- Tables inside PDFs that fell back to text extraction
- Anything in the spec that contradicts the register table

The user must be told to read this file. Do not silently treat parse
warnings as success.

---

## Phase 4: Scaffold

Materialize the directory tree. The shape is fixed — do not improvise.

```
<ip>/
├── .prj_top                         # marker for setup.sh walk-up
├── rtl/
│   ├── design.f                     # filelist (generated, $PROJ_DIR paths)
│   └── (user RTL referenced, NOT copied — except generated stubs)
├── script/
│   ├── makefile                     # SV_CASE / seed / cov contract — see below
│   ├── setup.sh                     # walks up to .prj_top, exports PROJ_DIR/WORK_DIR
│   ├── check_env.sh                 # validates vcs + UVM_HOME + license
│   └── vcm.cfg                      # coverage hierarchy scope
├── top/
│   ├── <ip>_tb_top.sv               # clocks, resets, dut + iface instantiation, run_test
│   └── <ip>_assertions.sv           # bound assertions for protocol checks
├── tb/
│   ├── <ip>_env.sv
│   ├── <ip>_env_config.sv
│   ├── <ip>_sb.sv                   # scoreboard
│   ├── v_sequencer.sv
│   ├── v_sequence.sv
│   ├── apb_agt_top/                 # the APB agent (skip if reusing user VIP)
│   │   ├── apb_agent.sv
│   │   ├── apb_agt_config.sv
│   │   ├── apb_driver.sv
│   │   ├── apb_monitor.sv
│   │   ├── apb_sequencer.sv
│   │   ├── apb_sequence.sv
│   │   └── apb_trans.sv
│   ├── seq_lib/
│   │   └── <ip>_basic_sequences.sv
│   ├── ral/
│   │   └── <ip>_reg_block.sv        # from registers.yaml
│   ├── ref_model/                   # only if user chose non-skip refm
│   │   ├── <ip>_ref_model.sv        # SV refm OR DPI shim
│   │   └── <ip>_ref.c               # if C-DPI
│   ├── tb_api/
│   │   └── tb_api_pkg.sv            # task-style BFM for DE persona
│   └── external_vip.f               # only if reusing user VIP
├── test/
│   ├── <ip>_pkg.sv                  # single package, `includes class hierarchy
│   ├── <ip>_test_lib.sv             # uvm_test classes
│   ├── sv_list                      # one test name per line
│   ├── sanity_test.sv               # mandatory
│   └── reg_access_test.sv           # mandatory
└── work/                            # gitignored, created by setup.sh
    └── _gen_audit/                  # audit trail (this file too)
        ├── intake.yaml
        ├── spec_normalized/
        ├── rtl_discovery.yaml
        ├── compile_fix_attempts/    # filled in Phase 5
        ├── sanity_result.json       # filled in Phase 6
        └── unresolved.md            # filled in Phase 7
```

### The Makefile contract

The makefile is the public API of the generated tb. **Do not deviate**
from these targets and variables — downstream tools (regression scripts,
TraceWeave, the user's eyes) all rely on them:

```
SV_CASE ?= <ip>_sanity_test    # name passed to +UVM_TESTNAME
seed    ?= $(shell date +1%N)
cov     ?= 0                   # set to 1 to enable coverage

make comp                                # compile only
make run SV_CASE=<case>                  # run one case
make all SV_CASE=<case>                  # comp + run
make all SV_CASE=<case> seed=42 cov=1
make merge                               # urg merge across all work_*
make wave                                # launch verdi
make clean
```

Per-case artifacts go to `work/work_<SV_CASE>_/`. See
`references/makefile_contract.md` for the full template.

### tb_api — the DE persona surface

`tb/tb_api/tb_api_pkg.sv` exposes simple tasks the DE can call from any
`initial` block in the top, **without** writing a sequence:

```systemverilog
tb_api::write(addr, data);
tb_api::read(addr, data);
tb_api::wait_irq(timeout_ns);
tb_api::expect_reg(addr, value);
tb_api::reset(cycles);
```

Internally these forward to UVM sequences/sequencers. The DE never sees
`uvm_sequence` or `uvm_test`. See `references/tb_api.md` for the
generation rules.

### Reference model

Branch on the refm language chosen at intake — see `references/refm_dpi.md`
for the DPI contract (function signatures, `-CFLAGS` wiring, the
`tb/ref_model/<ip>_ref_pkg.sv` import boilerplate). For SV-only refm,
the model is just another class in the env.

### RAL generation

Generate `tb/ral/<ip>_reg_block.sv` directly from
`spec_normalized/registers.yaml`. Every register becomes a `uvm_reg`
subclass; every field becomes a `uvm_reg_field`. The `reg_access_test`
walks the block and does a write-then-read on every RW field. See
`references/ral_gen.md`.

---

## Phase 5: Compile-fix loop (sub-agent)

After scaffolding, the tb almost certainly won't compile clean on the
first try — small interface naming mismatches, missing `include order,
typos in port lists. **Spawn a dedicated sub-agent to fix these**, do
not try to debug inline:

```
For attempt N in 1..max_attempts (default 5):
  - Run `make comp` and capture full log
  - If exit code == 0: break, success
  - Save log to work/_gen_audit/compile_fix_attempts/attempt_N.log
  - Spawn sub-agent (Agent tool, subagent_type=general-purpose):
      Prompt: see references/sub_agent_compile_fix.md
      Inputs: the log, rtl_discovery.yaml, intake.yaml, the generated tb
      Constraint: may only edit files under tb/, top/, test/, script/.
                  May NOT edit files under rtl/.
                  May NOT introduce new dependencies.
  - Save diff to work/_gen_audit/compile_fix_attempts/attempt_N.diff
```

If after max_attempts the compile is still failing, **stop** and write
to `unresolved.md`:

```
## Compile failure (5 attempts exhausted)

Last error: <one-line summary>
Full log:   work/_gen_audit/compile_fix_attempts/attempt_5.log
Likely cause: <best guess>
Suggested next step: <concrete action>
```

Then surface to the user with the unresolved.md path. **Do not fake
success.** A tb that doesn't compile is worse than no tb because it
wastes the user's debug time.

---

## Phase 6: Sanity tests

Once compile is clean, run two mandatory tests:

```bash
make all SV_CASE=<ip>_sanity_test
make all SV_CASE=reg_access_test
```

Parse each `run.log` for `UVM_FATAL`, `UVM_ERROR`, simulator
errors. Save outcome to `work/_gen_audit/sanity_result.json`:

```json
{
  "test_sanity":    {"passed": true,  "seed": "...", "duration_ms": 8412},
  "reg_access_test":{"passed": false, "seed": "...", "fail_signature": "..."}
}
```

If a mandatory test fails, **re-enter the Phase 5 loop** with a sub-agent
prompted to fix runtime issues (it may now edit the failing test itself,
within tb/test/ scope). Max 3 additional attempts. If still failing,
write to unresolved.md and surface.

For algorithmic IPs with a reference model, **also** generate and run a
single end-to-end smoke test (e.g., `aes_ecb_smoke` for AES) that drives
one transaction through the DUT and confirms the scoreboard sees a
ref-vs-dut match. Same pass/fail handling.

---

## Phase 7: Hand-off

Write three artifacts for the human:

1. **`<ip>/CLAUDE.md`** — agent-facing guidance for future Claude
   sessions in this directory. Cover: setup.sh requirement,
   SV_CASE/seed/cov contract, where audit trail lives, the
   `tb_api` vs UVM sequence persona split, TraceWeave MCP debug entry
   point ("if available"). Pattern from
   `references/generated_claude_md.md`.

2. **`<ip>/work/_gen_audit/unresolved.md`** — list every parse warning,
   every low-confidence inference, every assumption the skill made
   that the user should verify. Empty file is fine (and a good sign);
   never delete it.

3. **A summary to the user in chat** — max 10 lines:
   - what IP was scaffolded
   - which sanity tests passed
   - number of compile-fix attempts used
   - count of unresolved items
   - the exact `cd <ip>/script && source setup.sh && make all SV_CASE=...`
     command they should run next

Do not lecture. The user can read the generated CLAUDE.md when they
need to.

---

## Constraints that apply across all phases

- **Never copy user RTL**. Always reference it by path in `design.f`
  (use `$PROJ_DIR/...` for in-tree, absolute or relative for external).
  Exception: stub RTL generated by gen-tb itself.
- **Never edit user RTL**. The skill is testbench-only. Even a tempting
  one-line fix is out of scope.
- **Never silently overwrite**. If a target file exists, back it up to
  `<path>.bak.<timestamp>` first and warn the user in the summary.
- **Never invent registers**. The RAL must derive 1:1 from
  `registers.yaml`. If a register seems missing, that's a spec-parsing
  bug — flag it in `parse_report.md`, do not patch over it.
- **Zero hard external skill dependencies**. `eda-environment`,
  TraceWeave MCP, and `skill-creator` are all *optional* enhancements.
  The skill must work on a vanilla Claude Code install with VCS in PATH.

---

## References (load on demand)

This SKILL.md stays under 500 lines on purpose; deeper detail is in
`references/`. Load the right file when you reach that phase, not
upfront.

| File | When to load |
|---|---|
| `references/directory_layout.md` | Phase 4 — exact file list per IP class |
| `references/makefile_contract.md` | Phase 4 — full Makefile template + variables |
| `references/apb.md` | Phase 4 — APB agent generation rules |
| `references/spec_parsing.md` | Phase 3 — per-format parser invocation + pitfalls |
| `references/registers_yaml_schema.md` | Phase 3 + Phase 4 — yaml schema + RAL mapping |
| `references/ral_gen.md` | Phase 4 — RAL class layout |
| `references/refm_dpi.md` | Phase 4 — DPI function signatures + C/Py wiring |
| `references/tb_api.md` | Phase 4 — tb_api task list + internals |
| `references/rtl_stub.md` | Phase 1 — when generating RTL stub from spec only |
| `references/sub_agent_compile_fix.md` | Phase 5 — sub-agent prompt template + constraints |
| `references/generated_claude_md.md` | Phase 7 — template for the generated CLAUDE.md |

## External tools

- **`eda-environment` skill**: invoke before any `vcs` / `simv` /
  `verdi` call **if available**. If not, rely on the user's shell env
  and the generated `script/check_env.sh`. Do not fail just because the
  skill is missing.
- **TraceWeave MCP**: not used during generation. Reference it from the
  generated CLAUDE.md as "use if available" for downstream debug.
- **`skill-creator`**: not invoked during generation. Used only when
  iterating gen-tb itself (eval harness, description optimization).

---

## Quick checklist before you say "done"

- [ ] `<ip>/.prj_top` exists
- [ ] `make comp` exit code 0
- [ ] `make all SV_CASE=<ip>_sanity_test` shows no UVM_ERROR/FATAL
- [ ] `make all SV_CASE=reg_access_test` shows no UVM_ERROR/FATAL
- [ ] (algorithmic IP) refm smoke test shows scoreboard match
- [ ] `work/_gen_audit/intake.yaml` complete
- [ ] `work/_gen_audit/spec_normalized/parse_report.md` reviewed for
       warnings
- [ ] `work/_gen_audit/unresolved.md` exists (may be empty)
- [ ] `<ip>/CLAUDE.md` written
- [ ] Summary delivered to user with the exact next command
