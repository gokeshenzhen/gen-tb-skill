# RTL stub generation (gen-tb reference)

> Loaded during Phase 1 discovery when no usable RTL exists. Defines the
> narrow case where gen-tb may generate synthesizable placeholder RTL.

## Policy

Generate RTL stubs only when all are true:

- no user RTL or usable external RTL path exists
- the user explicitly confirms stub generation
- the stub is needed to make the generated testbench compile
- generated files are clearly marked as non-golden placeholders

Do not generate a stub when user RTL exists but fails to compile. That is
a compile-fix problem; user RTL remains read-only.

## Location

Generated stubs live under:

```text
rtl/<ip>_stub.sv
```

This is the only mode where gen-tb writes `rtl/`. If `rtl/` contains
user RTL, it is read-only and gen-tb must not add files there.

Record stub mode in:

```yaml
intake.yaml:
  rtl_state: generated_stub
rtl_discovery.yaml:
  mode: generated_stub
  filelist_origin: generated
```

## Stub Scope

The stub is a bus-facing APB slave shell, not an implementation of the
IP's real behavior. AHB-Lite and AXI4-Lite stub generation are not yet
in scope — for those buses, ask the user for real RTL or an external
filelist; do not fabricate a stub.

It may implement:

- APB handshake
- reset behavior
- register storage for RW fields
- constant readback for RO reset fields
- write capture for WO fields
- basic register arrays
- `pslverr=0`

It must not claim to implement:

- algorithmic output such as AES/FIR/CRC/compression
- timing-accurate FIFOs or DMA
- interrupts beyond simple register-driven placeholders
- undocumented side effects

If the spec is algorithmic, the stub should expose the register map and
leave output data/status behavior conservative. Mark every unsupported
behavior in `parse_report.md` and generated `CLAUDE.md`.

## APB Skeleton

Generated module ports mirror the canonical APB interface:

```systemverilog
module <ip>_stub (
    input  logic        pclk,
    input  logic        presetn,
    input  logic        psel,
    input  logic        penable,
    input  logic        pwrite,
    input  logic [AW-1:0] paddr,
    input  logic [31:0] pwdata,
    output logic [31:0] prdata,
    output logic        pready,
    output logic        pslverr
);
```

Rules:

- `pready` may be tied high for zero-wait-state APB
- sample writes on `psel && penable && pwrite && pready`
- sample reads combinationally or registered, but keep the testbench
  access timing consistent with `tb_api::read`
- reset all mirrored storage to field-derived reset values
- reads to unmapped addresses return zero and optionally set `pslverr`
  only if the user asks for error behavior

## Register Derivation

Use `registers.yaml` as the sole source. Do not infer hidden registers
from prose or RTL names.

For each register:

| Access | Stub behavior |
|---|---|
| RO | returns reset/constant value unless a simple status expression is explicitly specified |
| RW | stores written value and resets to derived reset |
| WO | records last write internally; reads return zero unless spec says otherwise |
| alias | implement only when `aliased_by` and `aliased_by_value` are explicit |
| array | generate packed/unpacked storage indexed by stride |

Field-level reset values win over register-level resets, matching
`references/registers_yaml_schema.md`.

## Non-bus Pads

If the real IP has non-bus pads but no RTL exists, include only pads that
are required by the user-provided interface/spec. Otherwise keep the
stub APB-only. Do not invent serial/modem/interrupt ports from an IP
name alone.

If pads are emitted, `rtl_discovery.yaml: other_pads` must list them so
`top/<ip>_tb_top.sv` connects them consistently.

## Audit Trail

Write a `parse_report.md` section:

```markdown
## RTL stub generation
- User confirmed stub mode: yes
- Stub file: rtl/<ip>_stub.sv
- Implemented behavior: APB register shell
- Not implemented: <algorithm/status/interrupt details>
```

Generated `CLAUDE.md` must repeat that the RTL is a placeholder and
must be replaced by real RTL before meaningful verification.

## Blocking Cases

Do not generate a stub when:

- bus protocol is not APB
- register map is missing
- paddr/data width is unknown
- reset polarity/timing is unknown
- the user expects algorithmic correctness from the stub

Ask for the missing data or stop with `unresolved.md`.

## Cross-references

- `references/spec_parsing.md` — normalized registers and behavior
- `references/registers_yaml_schema.md` — reset/access/array semantics
- `references/rtl_discovery.md` — stub discovery fields
- `references/top_sv.md` — top wiring for generated stub
