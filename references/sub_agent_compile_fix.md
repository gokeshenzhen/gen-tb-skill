# Compile/runtime fix sub-agent prompt (gen-tb reference)

> Loaded during Phase 5 compile-fix and Phase 6 runtime-fix loops.
> The sub-agent fixes generated verification collateral only. It must
> never edit user RTL, user VIP source, specs, or normalized register
> inputs.

## When to use

Use a constrained sub-agent after `make comp` fails, or after a
mandatory runtime test exits non-zero / reports UVM errors. Save every
attempt under:

```text
work/_gen_audit/compile_fix_attempts/attempt_N.log
work/_gen_audit/compile_fix_attempts/attempt_N.diff
```

For runtime failures, use the same directory but include the relevant
`run.log` and test name in the prompt.

## Editable scope

The sub-agent may edit only generated files under:

```text
tb/
top/
test/
script/
work/_gen_audit/
```

It may not edit:

```text
rtl/
ref_model/
vip/
spec/
work/_gen_audit/intake.yaml
work/_gen_audit/rtl_discovery.yaml
work/_gen_audit/spec_normalized/registers.yaml
```

For `<bus>_vip_source: reuse_my_vip`, the user VIP is read-only. Generate
project-local glue instead of patching VIP source.

## Prompt template

```text
You are fixing a generated UVM testbench produced by gen-tb.

Goal:
- Make the current compile/runtime failure pass without editing user
  RTL, user VIP source, specs, or normalized yaml inputs.

Inputs:
- IP root: <absolute ip root>
- Failing command: <make command>
- Log: <path to comp.log or run.log>
- intake.yaml: work/_gen_audit/intake.yaml
- rtl_discovery.yaml: work/_gen_audit/rtl_discovery.yaml
- registers.yaml: work/_gen_audit/spec_normalized/registers.yaml
- Generated filelists: script/design.f, script/tb.f

Editable scope:
- tb/
- top/
- test/
- script/
- work/_gen_audit/

Do not edit:
- rtl/
- ref_model/
- vip/
- spec/
- intake.yaml / rtl_discovery.yaml / registers.yaml

Rules:
- Fix the first real root cause shown in the log, not every cosmetic
  issue.
- Preserve generated directory layout and Makefile public targets.
- Do not remove tests or weaken positive checks to get a pass.
- Do not replace real RTL/VIP behavior with mocks.
- If external VIP reuse is import_only, fix filelist/include/package
  order only.
- If external VIP reuse is drive_with_vip, you may add generated bridge
  modules/classes/sequences under tb/ or test/ and config_db wiring under
  top/ or test/.

Output:
- Apply edits directly.
- Report changed files.
- Explain the root cause in 2-5 bullets.
- State the exact command to rerun.
```

## Attempt policy

Run at most 5 compile-fix attempts and 3 runtime-fix attempts. If still
failing, write `work/_gen_audit/unresolved.md` with:

- failing command
- last log path
- shortest root-cause hypothesis
- files inspected
- recommended next action

Do not claim success unless the rerun passes.
