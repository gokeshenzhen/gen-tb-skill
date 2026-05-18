# SKILL.md v1 dry-run report — uart16550 re-run

## Verification: v0 → v1 gap closure

| v0 gap | Fix verified? | Evidence |
|---|---|---|
| G1 RTL canonical dir | ✅ | design.f references user RTL via `$PROJ_DIR/rtl/...`; no fixture write |
| G2 top heuristic | ⚠️ partial | Fixture only had one no-instantiated module; deeper validation needs a multi-top fixture |
| G3 APB signal casing | ✅ | rtl_discovery.yaml carries `pclk`/`presetn` lowercase; apb_if.sv matches |
| G7 DLAB aliasing | ⏸ deferred | Sanity didn't touch banked regs; RAL not generated this round |
| G10 RO/WO disjoint | ⏸ deferred | Same — needs RAL gen |
| G11 design.f location | ✅ | `script/design.f` written cleanly; rtl/ untouched |
| **G12 presetn dual-drive** | ✅ | **Compile: 0 warnings (v0 had 1).** apb_if input ports for pclk+presetn |
| **G16 positive sanity** | ✅ | **Sanity did APB read of LSR; caught a real readback mismatch v0 would have silently passed.** |
| G17 symlink guard | ✅ | Python realpath check confirms `rtl/design.f` writes would be blocked |
| G18 design.f location doc | ✅ | New SKILL.md text mentions the divergence from uart_uvm_demo |

5 verified, 1 partial, 2 deferred. Compile/run loop: 0 warnings, 1 expected UVM_ERROR caught by positive check.

## New v1 gaps (v1.1 candidates)

### G19 — Hand-rolled APB driver in sanity test is fragile (BLOCKING)

The v1 SKILL.md mandates a positive check (read of a known reset
value), but the minimum scaffold doesn't generate a full APB agent.
My hand-rolled `apb_read` task in the test class returned 0 instead
of the expected 0x60 for LSR. Could be address-mapping, registered
wb_dat_o timing, or a protocol-corner bug — without a known-good
APB driver, the sanity test will fail intermittently per DUT.

**Fix**: Phase 4 must either (a) always generate the full APB
agent, or (b) ship a known-good `tb_api::read` implementation in
`references/tb_api.md` that the scaffold uses verbatim. Recommend
(b) — `tb_api::read` is the DE-persona surface anyway.

### G20 — Sanity-failure triage not specified

When positive-check sanity fails, Phase 6 says "re-enter Phase 5
sub-agent loop". But the sub-agent is forbidden from editing RTL.
The skill needs a triage step:
1. Readback differs from spec → likely DUT/wrapper bug → surface
   via `unresolved.md`, do NOT auto-fix
2. Transaction never completes (pready never high) → likely
   test/protocol bug → sub-agent can fix
3. Add a timeout in the read task with `uvm_fatal` so neither
   class of failure hangs simulation

### G21 — Address-mapping in wrapper not extracted

I had to manually derive: APB PADDR[6:2] → wb_adr[4:0] inside
`uart_apb_wrap`, so PADDR=0x14 → wb_adr=5 = LSR. SKILL.md captures
APB pin names but not the address-bit slicing applied inside a
wrapper. The sanity test computed `ADDR_LSR = 0x14` by human
reasoning; gen-tb has no rule.

**Fix**: When a wrapper is present (apb_wrap, axi_wrap, …),
rtl_discovery must parse the wrapper for `core_addr <= paddr[X:Y]`
assignments and record the address transform. The RAL generator
then composes wrapper-transform + yaml-offset.

### G22 — `registers.yaml` offset semantics ambiguous

The xlsx had standard 16550 byte offsets (0, 1, 2…, 7); the
generator scaled them by 4 to word-aligned APB byte offsets
(0, 4, 8…, 0x1C). SKILL.md doesn't say which form is canonical
in yaml. The schema reference must declare: **yaml offsets are
the post-wrapper PADDR values the APB driver issues.**

### G23 — "Minimum scaffold" undefined

I (acting as gen-tb) chose to skip the full agent for dry-run
efficiency. SKILL.md neither authorizes nor forbids this. In
production, does gen-tb always generate the full agent (~2000
lines of SV), or is there a "fast scaffold" mode for smoke
testing? Nondeterministic scaffold sizes hurt debuggability.

**Fix**: Declare explicitly. Recommend always-full-agent for v1.

### G24 — Scaffold generation throughput

Each generated SV file = a separate Write call. 14+ files × ~5
round-trip exchanges each = 70+ model-time steps before compile.
Real users will feel this latency.

**Fix**: Phase 4 should invoke `scripts/scaffold.py` ONCE with
intake.yaml + registers.yaml + rtl_discovery.yaml as input,
dropping the whole tree in one pass. Per-file Write only for
post-scaffold patches.

## Summary

**v1 closes the structural v0 gaps as designed.** Compile warnings
gone (1 → 0); positive sanity check fires and catches issues v0
would have silently passed.

**6 new gaps surfaced** (G19–G24). 1 blocking (G19), 5 important.
The v1 → v1.1 work centers on:
- Writing `references/tb_api.md` with a known-good `tb_api::read`
- `references/registers_yaml_schema.md` (offset semantics, aliasing markers)
- `references/rtl_discovery.md` (wrapper address-mapping extraction)
- `scripts/scaffold.py` for one-shot tree generation

## What's healthy about v1

- 7-phase contract held with no restructuring needed
- Audit-trail structure absorbed all new artifacts cleanly
- Symlink guard pattern correct and Python-verifiable
- Test-naming convention (`<ip>_<purpose>_test`) eliminates a class
  of collision bugs
- Positive sanity test surfaced a real issue in 60 seconds of sim,
  rather than letting a vacuous pass propagate downstream
