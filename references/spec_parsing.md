# Spec parsing and normalization (gen-tb reference)

> Loaded during Phase 1 discovery and Phase 3 normalization. Defines how
> source documents become `intake.yaml`, `registers.yaml`,
> `behavior.md`, and `parse_report.md`.

## Outputs

Phase 3 must write:

```text
work/_gen_audit/intake.yaml
work/_gen_audit/spec_normalized/registers.yaml
work/_gen_audit/spec_normalized/behavior.md
work/_gen_audit/spec_normalized/parse_report.md
```

`registers.yaml` is blocking. If no reliable register table is found,
stop and ask the user for one. `behavior.md` may be partial, but every
omission or assumption must be recorded in `parse_report.md`.

## Source Priority

Use user-provided structured files before prose:

1. IP-XACT XML register map
2. xlsx/csv register table
3. markdown register table
4. docx/pdf register table
5. prose-only spec

When two sources disagree, prefer the more structured source and write a
`## Conflicts` entry in `parse_report.md` with both values and source
locations.

## Input Formats

### xlsx

Use a structured reader such as `openpyxl`; do not parse `xlsx` as text.
Handle:

- merged cells by carrying the last non-empty register-level value
- hex strings with `0x`, `'h`, or bare hex columns
- bit ranges `N`, `M:N`, `[M:N]`
- one-row-per-field and one-row-per-register tables

If rows named `KEY0`, `KEY1`, ... or `BLOCK0`, `BLOCK1`, ... are
contiguous with identical shape, fold them into `array_of` with
`stride`. If folding fails, keep individual registers and warn.

### csv

Use the same column contract as xlsx. Normalize headers by lowercasing
and stripping spaces, `_`, and `-`. Do not infer columns from position
unless a header row is missing and the table exactly matches the fixture
schema.

### markdown

Extract only real pipe tables. Ignore prose bullets that merely mention
register names. If multiple tables exist, classify them as register,
field, reset, or enum tables and join by register name/offset. Ambiguous
joins require a warning.

### IP-XACT XML

Parse with an XML parser. Preserve vendor extensions in
`parse_report.md` but emit only the normalized fields used by
`registers.yaml`. Address offsets are byte offsets from the APB
programmer's view unless the XML explicitly describes a wrapper mapping.

### docx

Use document structure first: headings, tables, captions, then body
paragraphs. Do not rely on visual page layout. Tables under headings like
`Register`, `Address map`, or `Programmer's model` are candidates for
`registers.yaml`; control-flow prose becomes `behavior.md`.

### pdf

PDF parsing is lowest confidence. Prefer embedded text tables over OCR.
If OCR is needed, write a `## PDF extraction warnings` section. Always
show table page numbers and confidence in `parse_report.md`.

## Normalized Register Rules

Follow `references/registers_yaml_schema.md` exactly:

- offsets are post-wrapper APB byte addresses
- field-level reset values win over register-level reset values
- normalize access strings before yaml emission: register `access` is
  `RO`/`RW`/`WO`; field `access` is a canonical UVM 1.2 field policy
- repeated offsets are legal for aliases or disjoint RO/WO pairs
- never invent missing registers
- preserve original register and field names when they are legal SV IDs
- normalize illegal SV IDs and record the rename

For side effects such as read-clear, write-1-clear, sticky status, FIFO
pop-on-read, or indirect INDEX/DATA access, record an optional field:

```yaml
effect: read_clear | write_1_clear | sticky | fifo_pop | indirect
```

If the effect is inferred from prose rather than a structured column,
mark it in `parse_report.md` as inferred.

## Behavior Summary

Write `behavior.md` as a concise machine-readable prose summary:

```markdown
# Behavior Summary

## Classification
- peripheral | algorithmic | streaming | register_only

## Control Flow
- start/control fields
- ready/busy/done/valid fields
- reset/idle behavior

## Data Path
- input registers or streams
- output registers or streams
- byte/word ordering

## Reference Model
- source: user_provided | spec_derived_basic | stub_only
- trust: golden | heuristic | interface_only
- notes:
```

`behavior.md` is not a substitute for `registers.yaml`; it explains how
tests and optional smoke flows should use the registers.

## Reference Model Policy

gen-tb distinguishes integration from synthesis:

| Source | Meaning | Trust |
|---|---|---|
| `user_provided` | User supplies C/Python/SV model | `golden` if user says so |
| `spec_derived_basic` | gen-tb infers simple behavior from structured spec | `heuristic` |
| `stub_only` | gen-tb emits interfaces/TODO only | `interface_only` |

Register-level behavior should live in the generated RAL/reg block, not
in a duplicate standalone reference-model component. This includes:

- reset values
- RO constant/status fields
- RW mirror behavior
- WO write recording
- register arrays
- alias metadata and skip policy

Algorithmic behavior such as AES encryption, FIR filtering, compression,
or protocol packet transformation must not be claimed as golden unless a
user-provided model or known standard implementation is connected. When
only the prose spec exists, generate a stub or a heuristic smoke flow and
flag the limitation.

## Scoreboard Placement

Generated scoreboards belong under `tb/scoreboard/`, not `test/`.
`test/` owns testcase classes, sequences, and test selection only.

For register-only checking, prefer RAL/reg-block prediction and
frontdoor checks. Add a scoreboard only when comparing monitored
transactions against a user-provided model or a non-trivial
spec-derived heuristic.

## Intake Fields

Normalize reference-model decisions into `intake.yaml`:

```yaml
ref_model_language: skip | sv | c_dpi | py_dpi
ref_model_source: user_provided | spec_derived_basic | stub_only
ref_model_trust: golden | heuristic | interface_only
refm_mode: snapshot | stream | skip
```

Defaults:

- no model found and register-only IP: `skip`, `spec_derived_basic`,
  `heuristic`, `skip`
- user C model found: `c_dpi`, `user_provided`, ask whether `golden`,
  derive `snapshot` or `stream`
- user SV model found: `sv`, `user_provided`, ask whether `golden`
- algorithmic prose only: `skip`, `stub_only`, `interface_only`, `skip`

For C/Python DPI-specific fields, load `references/refm_dpi.md`.

## parse_report.md

Use stable headings so later phases can scan it:

```markdown
# Parse Report

## Inputs
## Selected Sources
## Assumptions
## Conflicts
## Reset value mismatches
## Alias decisions
## Array folding
## Side-effect inference
## Reference model decision
## Warnings
```

Every warning should include the source file and, when possible, sheet,
row, page, heading, or XML path.

## Blocking Conditions

Stop before scaffold when:

- no register map can be normalized
- APB address width cannot contain the largest offset
- register width exceeds generated data width and no split rule is known
- field ranges overlap in a way that is not marked as alias/banked
- spec says bus protocol is not APB

Ask the user for the missing source or an explicit override. Do not
guess a register map from RTL signal names.

## Cross-references

- `references/registers_yaml_schema.md` — normalized register schema
- `references/refm_dpi.md` — C/Python DPI integration
- `references/ral_gen.md` — generated RAL/reg-block behavior
- `references/directory_layout.md` — generated file ownership
