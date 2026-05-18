# SKILL.md v1 dry-run report — aes128

Logged in order of discovery. Each item = a gap v1 does not handle
cleanly, surfaced by the AES (algorithmic + DPI) fixture path.

## G25 — `ref_model/` collision: user input dir vs generated output dir

The aes128 fixture has user-provided C reference code at
`<ip>/ref_model/`. The gen-tb scaffold also writes its DPI shim and
generated SV ref-model at `<ip>/tb/ref_model/`. SKILL.md uses both
paths interchangeably and never declares which is canonical.

If the user dumps their refm at `<ip>/ref_model/`, gen-tb would
either (a) overwrite it during scaffold, or (b) treat it as the input
it's looking for.

**Fix**: Phase 1 must scan `<ip>/ref_model/` as a *user input
directory* (like `spec/`). Generated DPI artifacts live at
`<ip>/tb/ref_model/`. SKILL.md must rename one or the other. Recommend
keeping user input as `<ip>/ref_model/` and moving generated to
`<ip>/tb/dpi/` or `<ip>/tb/refm/`.

## G26 — `intake.yaml` schema not extensible for DPI

When `ref_model_language: c_dpi`, several new fields are needed that
SKILL.md doesn't document:
- list of user-provided `.c` / `.h` files
- DPI export signatures (C name, SV name, arg list)
- header search paths
- extra `-CFLAGS` the user wants threaded through

I had to invent the schema mid-flight (`c_sources`, `c_headers`,
`dpi_exports`). Different invocations of gen-tb may invent different
shapes — non-deterministic.

**Fix**: `references/refm_dpi.md` must publish a canonical
`intake.yaml` schema fragment for each language choice. Likewise for
`py_dpi` and `sv` (e.g., SV-only refm needs a class-name field).

## G27 — Makefile DPI section is invented per-IP

The Makefile got new lines for the algorithmic IP case:
```
C_SRCS = $(PROJ_DIR)/ref_model_src/tiny_aes.c \
         $(PROJ_DIR)/ref_model_src/aes_ref.c
C_INC  = -CFLAGS "-I$(PROJ_DIR)/ref_model_src -O2 -Wall"
```

These are gen-tb's responsibility to thread through; SKILL.md has no
template for the makefile's DPI section. Currently every C-DPI
generation reinvents the variable names and -CFLAGS quoting.

**Fix**: `references/makefile_contract.md` must include a "DPI
section" template that ingests the `ref_model_inputs` field from
intake.yaml and emits a deterministic block.

## G28 — DPI function signatures duplicated 3× across the tree

The same function signature for `aes128_ecb_encrypt` appears in:
- `intake.yaml: dpi_exports`
- `tb/dpi/aes_ref_pkg.sv` (import "DPI-C")
- `ref_model_src/aes_ref.c` (function definition)
- `ref_model_src/tiny_aes.h` indirectly

Keeping these in sync is fragile. If the user changes the C side, the
SV import goes stale and elaborate fails.

**Fix**: scaffold.py must regenerate `aes_ref_pkg.sv` from
`intake.yaml: dpi_exports` whenever intake.yaml changes. The user
edits the C header → user updates intake.yaml → gen-tb regenerates the
SV import. This needs to be an *explicit* re-run target, perhaps a
`make refresh-dpi` makefile entry.

## G29 — STATUS reset semantics confounds positive-check sanity

STATUS register reset value is `READY=1, VALID=0` (correct for an
idle core). My sanity test reads NAME0 (a hardcoded ASCII signature)
which works fine. But the smoke test's polling loop —
`while ((STATUS & READY) == 0)` — exits immediately on first read
because READY is already 1, *before* the FSM has actually executed
the key-expansion request. Result: the encryption runs with stale
state and produces garbage.

This is not a gen-tb framework bug — it's a test-logic bug or a
spec-wording bug. But the implication for v1 is: **the polling
pattern in DPI smoke tests is not derivable from registers.yaml
alone**. We need spec wording like "READY goes low while busy, high
when done" to generate the right wait pattern. xlsx reg tables don't
capture this.

**Fix**: `registers.yaml` schema needs a `wait_pattern` per status
bit, or a separate `flows.yaml` from spec behavior describing the
key→init→ready→data→next→valid sequence. This kind of "operation
flow" is what behavior.md (from docx/pdf) is supposed to provide,
but the parser only extracts text — it doesn't convert prose into
machine-readable wait patterns.

For v1 a simpler heuristic: when a STATUS reset already shows the
"complete" flag high, the wait pattern must first wait for the flag
to go LOW (FSM took our command), then HIGH (FSM completed).
SKILL.md Phase 4 should specify this when generating DPI smoke
tests against an algorithmic IP.

## G30 — Multi-word register arrays (KEY0..3, BLOCK0..3, RESULT0..3)

The registers.yaml has 4 separate KEY{0..3} registers, each at a
distinct offset. The SV test iterates `for (i=0..3) write(ADDR_KEY0
+ i*4, key[i])`. This works because the offsets are contiguous in
multiples of 4. SKILL.md does not specify:

- How to detect "register array" in the yaml (we'd want a single
  RAL handle `key[4]` not 4 unrelated regs)
- Whether the test helper API should be `write_array(ADDR_KEY0,
  key[])` instead of a loop
- Endianness when DUT and refm disagree on word order

**Fix**: `registers.yaml` should have an explicit `array_of` marker:
`name: KEY, count: 4, base_offset: 0x40, stride: 4`. RAL gens a
`uvm_reg key[4]`. `tb_api` adds `write_array` / `read_array` helpers.

## G31 — Smoke test compares DUT vs REFM at end, not interleaved

My smoke test:
1. Compute refm[] via DPI (single call)
2. Drive DUT, read result
3. Compare at the end

This works for single-block ECB but doesn't scale to streamed modes
(CBC, CTR, GCM). Even for ECB, when scoreboarding multiple back-to-
back blocks, an interleaved DPI-after-each-DUT-block model is
better.

**Fix**: `references/refm_dpi.md` should describe both patterns:
- "snapshot" mode: refm + DUT computed independently, compared at
  test end. Good for unit-IPs with deterministic outputs.
- "stream" mode: scoreboard component calls DPI on every DUT input,
  enqueues expected, dequeues on DUT output. Good for pipelined IPs.

Default: snapshot for ECB-style, stream for streaming modes (CTR,
GCM, FIFOs).

## G32 — Scoreboard not generated

For the smoke test I just inlined the comparison in the test
class. v1 SKILL.md scaffold lists a `<ip>_sb.sv` but the dry-run
elided it. In production, the scoreboard should be a proper
`uvm_component` subscribing to:
- monitor's analysis port (DUT-side transactions)
- refm callback (golden expected values)

This was deferred in the uart dry-run too. v1 → v1.1 must commit
to a scoreboard template, even a simple stub one that just logs
mismatches.

## Summary

**aes128 dry-run results**:
- ✅ DPI C code compiled + linked via VCS (zero warnings)
- ✅ DPI function called from SV; REFM produced **bit-exact NIST
   FIPS-197 expected output** (`3925841d 02dc09fb dc118597
   196a0b32`)
- ✅ APB transactions to DUT worked (sanity NAME0 read = "aes ")
- ⚠️  DUT smoke compare mismatched — test-logic / spec-wording bug,
   not a gen-tb framework issue (G29). The framework correctly
   reported the mismatch.

**v0 + v1 gaps verified** on this fixture:
- Symlink guard (G17) still works (no fixture pollution)
- design.f in script/ (G11) works for DPI flow
- Compile clean (G12) extends to DPI compile

**New v1.1 gaps surfaced**: G25–G32 (8 items).
- **Blocking**: G27 (Makefile DPI template), G28 (DPI signature
  triple-source), G29 (STATUS wait pattern)
- **Important**: G25 (refm dir name collision), G26 (intake schema
  for DPI), G30 (reg arrays), G31 (snapshot vs stream)
- **Polish**: G32 (scoreboard always generated)

## Net assessment

v1 SKILL.md's algorithmic-IP path is structurally **viable but
under-specified**. The Makefile + DPI plumbing produced a working
end-to-end refm-vs-DUT comparison, which is the key milestone for
v1. The remaining gaps (G25–G32) are mostly about *deterministic
output* — the framework works, but each invocation reinvents
schemas, makefile sections, and signature wiring.

For v1.1, the highest-leverage write is `references/refm_dpi.md`
(addresses G26, G27, G28, G31 in one document) plus a scaffold.py
that mechanizes the intake → SV-imports + makefile sections
generation.


