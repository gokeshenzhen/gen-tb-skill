# SKILL.md gaps found during uart16550 dry-run

Logged in order of discovery. Each gap = something SKILL.md does not
say clearly enough, requiring me to invent or guess.

## G1 — RTL discovery: canonical directory name not stated

SKILL.md Phase 1 says discovery looks in `./rtl/`, `./src/`, `./design/`,
`./hdl/`. But it does not state which one the **generated** `design.f`
should reference, nor whether gen-tb assumes user's RTL lives at the
canonical `<ip>/rtl/`. If user's RTL is at `./src/`, do we:
(a) reference `$PROJ_DIR/src/...` in design.f?
(b) symlink `<ip>/rtl -> <ip>/src` and reference `$PROJ_DIR/rtl/...`?
(c) ask user to move it?

**Fix**: SKILL.md must declare (a) — design.f references the **actual**
user RTL path, prefixed with `$PROJ_DIR/`. The directory name `rtl/`
in the generated tree contract is not a hard requirement when user RTL
exists; `rtl/` is reserved for the generated stub case.

## G2 — Top-module heuristic is too thin

SKILL.md says "find modules defined but not instantiated, prefer name
matching IP". Naive regex grep for instantiations misses non-trivial
Verilog idioms (no `u_` prefix, multi-line port maps). For the
uart16550 fixture this happened to give the right answer (uart_apb_wrap)
but only by accident.

**Fix**: reference doc `rtl_discovery.md` should require a real parse
(slang or verible) OR enumerate the brittle-cases the regex misses so
the user can spot-check. Add a hard rule: when ≥2 modules appear to be
top candidates, *always* AskUserQuestion — do not just pick alphabetical.

## G3 — APB interface signal names are not standardized

The uart_apb_wrap exposes `pclk/presetn/psel/penable/...` (lowercase),
while uart_uvm_demo's APB uart used `PCLK/PRESETn/PSEL/...` (mixed
case). The generated APB agent's virtual interface declaration must
match the actual DUT port casing exactly. SKILL.md does not say to
record the casing in rtl_discovery.yaml — without that, the agent
generation will guess wrong half the time.

**Fix**: `references/apb.md` (when written) must require the
discovered APB signal name+width+case be carried into agent generation
verbatim. The yaml schema already supports this (just record `name`
field exactly).

## G5 — Phase 2 missing several real questions

SKILL.md Phase 2 lists 7 question categories. Real intake needs more:
- UVM version (1.1 vs 1.2 — VCS bundles both, behavior differs)
- Address space size / max paddr width (12 bits assumed but never asked)
- Multiple clock domains (CDC)? — gen-tb currently assumes single clk
- Endianness (KEY0=MSB or LSB?) — relevant for multi-word reg groups
- Per-IP test naming prefix (uart_sanity_test vs sanity_test) — affects
  every generated test name. SKILL.md is inconsistent: Phase 6 uses
  `uart_sanity_test`, scaffold checklist uses `sanity_test`.

**Fix**: SKILL.md must reconcile naming convention. Recommend
`<ip>_sanity_test` consistently. Add the missing questions to Phase 2
or document defaults explicitly.

## G6 — Phase 2 has no "abort/skip" path

If the user gets to Phase 2 and realizes they want to abort (e.g.,
wrong IP picked), SKILL.md does not say how. Currently AskUserQuestion
has no "cancel" option. Skill should always allow `[d] abort / let me
restart` and exit cleanly leaving the audit dir for inspection.

## G11 — design.f location assumes writable RTL dir

SKILL.md says generated `rtl/design.f` lives under `<ip>/rtl/`. But
when user's RTL is symlinked / read-only / git-controlled, gen-tb
cannot write into it. In this dry-run, writing `rtl/design.f`
silently traversed the symlink and polluted the fixture directory.

**Fix**: design.f must live in a gen-tb-owned dir. Recommend
`<ip>/script/design.f` (next to makefile) or `<ip>/design.f`
(project root). All file path constraints in SKILL.md Phase 4
need re-audit for "what if this dir is read-only".



SKILL.md says "RAL must derive 1:1 from registers.yaml" and "every
register becomes a uvm_reg". Classic 16550 has DLAB-aliased registers
at the same offset selected by another register's bit. RAL cannot
model this with the naive 1:1 mapping — reg_access_test will fail
spuriously.

**Fix**: `references/ral_gen.md` (when written) must include an
address-alias resolution strategy: skip aliased regs from default
reg_access_test, OR generate a custom frontdoor that toggles DLAB,
OR mark the regs `NOREG_HW_RESET` and exclude. Provide a clear
yaml-level marker (`aliased_by: LCR.DLAB`) so the parser can flag
and the user can choose.

## G8 — Reset value cross-check policy undefined

xlsx supports both register-level `reset` and field-level `reset`.
They can disagree. SKILL.md doesn't say which wins. Need a rule:
field-level wins, register-level is computed/checked. The parser
must warn on mismatch (currently not implemented).

## G9 — Spec parser hard-dependencies not declared

`python-docx`, `pdfplumber`, `openpyxl`, `lxml` are all conditional
imports. SKILL.md says "if parser not installed, write to
parse_report.md and surface to user". But it doesn't say:
- Should gen-tb attempt `pip install --user <pkg>` itself?
- Should it just degrade gracefully (PDF → skip, xlsx → fail)?
- Should it offer to convert (e.g., `docx → md` via pandoc)?

**Fix**: Declare a "minimum viable spec" — at least registers.yaml or
xlsx or csv. PDF/docx are *enrichment* only. If reg table can't be
parsed, abort cleanly with a precise install command suggestion.

## G10 — RW vs RO at same offset (IIR / FCR)

Different from DLAB: IIR is read-only and FCR is write-only at 0x08
unconditionally. RAL needs *two* uvm_reg's mapped to the same address
but with disjoint access modes. SKILL.md does not address this.

## G4 — Non-bus DUT pads need a tie/loopback strategy

Beyond APB, the UART has 9 serial/modem pads (stx/srx/cts/rts/dtr/dsr/
ri/dcd/irq). For a sanity test that just hits reset+idle, the inputs
need defaults (high for serial idle, low or via random for modem).
SKILL.md does not say how the generated top should handle these.

**Fix**: `references/top_sv.md` (new) must say: any DUT input not
covered by an agent gets a *parameterized default tie* in the
top.sv generated file, with a comment marking it `// TODO: connect
to <agent> when needed`. Inputs that look like RX of a serial link
default to 1'b1 (idle high). All outputs are simply left dangling
(no driver conflict possible).

## G12 — `presetn` dual-driver compile warning

Default scaffold has:
- `apb_if` declares `logic presetn;`
- `uart_tb_top.sv` does `assign apb.presetn = presetn;`

VCS warns: structural + procedural driver mix. Future versions promote
to error.

**Fix**: declare reset signals as **input ports** on the interface
(`interface apb_if(input logic pclk, input logic presetn);`) — top
drives them once, interface re-exposes. This is the uart_uvm_demo
pattern. `references/apb.md` must mandate this for *all* signals that
are externally driven (clock, reset). SKILL.md scaffold examples
should be updated to show the input-port form.

## G13 — Makefile naming convention

I used lowercase `makefile`, matching uart_uvm_demo. SKILL.md does not
state lowercase-vs-uppercase. Lowercase is the right call (matches
reference project) but should be explicit.

## G14 — `+incdir` enumeration is fragile

The generated makefile has a fixed `TB_INC = +incdir+...` listing
every tb subdir. When the tb adds a new agent or category, the makefile
must be updated. Better: generate `tb/tb.f` listing all tb dirs +
sources, makefile only references the top filelists. Reduces makefile
churn.

## G15 — `uvm-1.2` is hard-coded in CMP_OPTS

The makefile uses `-ntb_opts uvm-1.2`. intake.yaml said uvm version
"1.2" but the makefile doesn't read from intake.yaml — it's pinned at
generation time. If the user later wants to switch versions they edit
the makefile by hand. SKILL.md should say: re-running gen-tb picks
up intake.yaml changes (idempotent regeneration) OR makefile sources
a `uvm_version.mk` snippet that's the only place to edit.

## G16 — Compile success ≠ tb correctness

The minimum scaffold compiles and the placeholder sanity test
"passes" (no UVM_ERROR) but does nothing meaningful — just waits
200 cycles. Phase 6 in SKILL.md says "no UVM_ERROR/FATAL = pass" but
that's a weak criterion. Real sanity should at minimum:
- Drive one APB read of a known reset-value register (e.g., LSR=0x60)
- Assert prdata == expected
- Bail on mismatch

**Fix**: SKILL.md Phase 6 must define a *positive* sanity check, not
just absence of errors. Suggested minimum: `tb_api::expect_reg(LSR, 0x60)`
after reset.

## G17 — `rtl/` symlinked into a read-only fixture caused silent
writes through the symlink

Hard lesson: when the user's IP root contains a symlinked `rtl/`,
file writes traverse it. Before any file write under `<ip>/`, gen-tb
must check `realpath` and refuse to write into a path not under
`<ip>/` (the project root).

**Fix**: All Phase 4 file writes go through a guard:
`assert realpath(target).is_relative_to(realpath(ip_root)) and
 not is_under_symlink(target)`. The guard belongs in scripts/
helper, mentioned in SKILL.md cross-phase constraints.

## G18 — `script/design.f` vs convention shift

uart_uvm_demo has `rtl/design.f`. We moved to `script/design.f` due
to G11/G17. This is a *behavioral change* from the reference project,
which means generated CLAUDE.md and any user familiarity may break.
Decision: keep `script/design.f` as the canonical path; document in
SKILL.md + generated CLAUDE.md that it differs from uart_uvm_demo.

---

## Summary

18 gaps total. Severity buckets:

**Blocking (must fix before v1 ship)**: G1, G2, G3, G7, G10, G11,
G12, G16, G17 (9 items)
  These either produce wrong-result tb or write outside project root.

**Important (correctness in non-trivial cases)**: G4, G8, G9, G15
(4 items)

**Polish / DX**: G5, G6, G13, G14, G18 (5 items)

## What worked

The skill structure (7 phases, audit-trail-in-work, intake.yaml
schema) held up. The dry-run got to a compiling+running tb in roughly
90 minutes of model time with **zero compile fix iterations** — the
single warning was non-blocking. With the 18 gaps addressed, v1 is
within reach of single-pass success on this fixture.
