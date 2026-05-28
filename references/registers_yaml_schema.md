# registers.yaml schema (gen-tb reference)

> Loaded during Phase 3 (parser output spec) and Phase 4 (RAL +
> sanity-target derivation). This file is authoritative for how
> registers are represented after spec normalization.

## The schema

```yaml
registers:
  - name: CTRL                  # SystemVerilog identifier (uppercase by convention)
    offset: 0x20                # post-wrapper PADDR byte offset (see "Offset semantics" below)
    width: 32                   # register width in bits; usually matches DATA_W
    access: RW                  # RO | RW | WO  (register/map-level summary)
    reset: 0x00000000           # reset value; field-level wins on conflict (G8)
    aliased_by: null            # see "Address aliasing" below; null when not aliased
    array_of: null              # see "Register arrays" below; null when not an array
    fields:
      - name: init
        bits: "0"               # single bit
        access: WO              # UVM 1.2 field access policy
        reset: 0x0
        desc: "1 = start key expansion"
      - name: keylen
        bits: "3"
        access: RW
        reset: 0x0
        enum: {0: "128-bit", 1: "256-bit"}   # optional; for cov bin generation
        desc: "Key length select"
```

`registers` is a list. Each entry MUST have `name`, `offset`, `width`,
`access`, `reset`, `fields`. The `aliased_by` / `array_of` / `enum`
fields are optional.

## Access normalization

The parser must normalize spec-table access strings before writing
`registers.yaml`; generated RAL passes these strings directly to UVM.

Register-level `access` is a map-rights summary and MUST be one of
`RO`, `RW`, or `WO`, because `uvm_reg_map::add_reg()` rights only use
those three values. Field-level `access` may use any UVM 1.2
predefined field policy:

`RO`, `RW`, `RC`, `RS`, `WRC`, `WRS`, `WC`, `WS`, `WSRC`, `WCRS`,
`W1C`, `W1S`, `W1T`, `W0C`, `W0S`, `W0T`, `W1SRC`, `W1CRS`,
`W0SRC`, `W0CRS`, `WO`, `WOC`, `WOS`, `W1`, `WO1`.

Common spec spellings must be canonicalized, including:

| Spec spelling examples | YAML field access | YAML register access |
|---|---:|---:|
| `R`, `read`, `read only`, `read-only` | `RO` | `RO` |
| `W`, `write`, `write only`, `write-only` | `WO` | `WO` |
| `R/W`, `read-write`, `read write` | `RW` | `RW` |
| `read clear`, `read-to-clear` | `RC` | `RO` |
| `write 1 clear`, `write-one-to-clear` | `W1C` | `RW` |
| `write 0 set`, `write-zero-to-set` | `W0S` | `RW` |
| `write-only clear` | `WOC` | `WO` |

If a register-level spec cell contains a UVM field policy such as
`W1C`, preserve that policy for fields that inherit the register
access, but derive the register-level summary as `RW`. After all
fields are parsed, the register-level summary is recomputed from the
field policies so mixed RO/WO/RW fields produce the narrowest legal
map rights. Unsupported access strings fall back to the
inherited/default access and must be reported in `parse_report.md`.

## Offset semantics (G22)

The `offset` field is **the post-wrapper PADDR value the APB driver
issues**. Examples:

| Register at | offset in yaml | What the APB driver does |
|---|---|---|
| Reg 0 in a byte-wise IP, 8-bit interface | `0x00` | `paddr = 12'h000` |
| Reg 5 in a 16550-style word-packed IP | `0x14` | `paddr = 12'h014` (=5*4) |
| Reg at 0x80 in a memory-mapped algorithmic IP | `0x80` | `paddr = 12'h080` |

If the DUT has an internal wrapper that re-maps bits (e.g.,
uart_apb_wrap mapping PADDR=N*4 to a packed byte-lane), the **yaml
offset reflects what the user sees from the APB**, not what the
inner WB/AXI bus sees. rtl_discovery.yaml records the wrapper
transform separately.

## Reset value cross-check (G8)

When both register-level `reset` and field-level `reset` are present,
the parser must:

1. Compute `expected_reg_reset = OR over fields of (field.reset << bit_lsb)`
2. If `expected_reg_reset != reg.reset`, write a warning to
   `parse_report.md` (heading: `## Reset value mismatches`)
3. **Field-level values win** for RAL generation. The register-level
   value is informational only.

The OpenCores 16550's IIR register is a real-world example: the bare
spec says reset=0x01 (only pending bit), but the implementation
prepends `4'b1100` to bottom nibble, giving 0xC1 at reset. The yaml
should carry whichever value matches what the bus actually reads;
the parser flags the discrepancy.

## Address aliasing (G7)

Two registers may share the same `offset` for legitimate reasons:

### Case A — bank-selected aliasing (DLAB-style)

The 16550 has RBR (read), THR (write), and DLL (R/W when DLAB=1) all
at offset 0x00:

```yaml
- name: RBR
  offset: 0x00
  access: RO
  aliased_by: LCR.DLAB        # this reg active when LCR.DLAB==0
  ...
- name: THR
  offset: 0x00
  access: WO
  aliased_by: LCR.DLAB        # this reg active when LCR.DLAB==0 (and write)
  ...
- name: DLL
  offset: 0x00
  access: RW
  aliased_by: LCR.DLAB        # this reg active when LCR.DLAB==1
  aliased_by_value: 1         # only when DLAB==1
  ...
```

When `aliased_by != null`:
- RAL emits **three separate `uvm_reg` instances** with the same
  offset, each constrained to its active condition
- **The default `<ip>_reg_access_test` skips aliased regs** —
  walking them randomly produces spurious failures
- A separate `<ip>_bank_access_test` stub is generated for the user
  to fill in with a sequence that toggles the bank-select then
  accesses the appropriate variant

### Case B — disjoint RO/WO at same offset (IIR/FCR-style)

The 16550 also has IIR (RO) and FCR (WO) at offset 0x08, unconditionally
(no bank select):

```yaml
- name: IIR
  offset: 0x08
  access: RO
  # aliased_by stays null — disjoint by access, not by bank
- name: FCR
  offset: 0x08
  access: WO
```

When two `uvm_reg`s share `offset` with disjoint access modes:
- RAL emits both with `add_mem`/`add_reg` to the same map at the
  same offset, each using `UVM_NO_ACCESS` for the modes it
  doesn't own
- `<ip>_reg_access_test` reads the RO and writes the WO

## Register arrays (G30)

When the spec has consecutive identical registers (KEY0..3, BLOCK0..3,
RESULT0..3), the parser should fold them into one entry with
`array_of`:

```yaml
- name: KEY
  offset: 0x40                # base address
  width: 32
  access: WO
  reset: 0x0
  array_of: 4                 # 4 elements
  stride: 4                   # byte gap between elements; usually = width/8
  fields:
    - name: data
      bits: "31:0"
      access: WO
      reset: 0x0
```

RAL generation creates `uvm_reg key[4]` (an array handle). `tb_api`
adds compatibility:

```systemverilog
tb_api::write_array(ADDR_KEY, 4, key_data);
tb_api::read_array (ADDR_KEY, 4, 4, dut_data);
```

The parser's xlsx ingestion folds entries named `<basename><N>` for
consecutive N (e.g., KEY0, KEY1, KEY2, KEY3) into a single
`array_of: 4` entry, provided:
- the field names match (single field "data" of full width is the
  common case)
- the offsets are stride-aligned and contiguous

If folding fails (heterogeneous fields, gaps), the parser keeps the
N individual entries and writes a warning.

## Sanity-target picker (used by scaffold.py)

Iterates `registers` in declaration order and returns the first
entry where:
- `access in ("RO", "RW")`
- `reset != 0`
- `aliased_by == null`
- `array_of == null`

If no such register exists, falls back to register 0 with expected
reset = 0 — still proves the bus is responsive.

## Field bit-range syntax

Two accepted forms:
- `"N"` — single bit (closed range `[N:N]`)
- `"M:N"` — multi-bit range (high-to-low, MSB:LSB)

The parser normalizes both into `(lsb, width)` for code generation.
Bit ranges that go LSB:MSB are flipped silently with a warning to
`parse_report.md`.

## Reserved field names

These cannot be used as field names (collide with SV/UVM macros):

- `data`, `value` — OK but cause noise; prefer `payload` or domain name
- `addr`, `paddr`, `pwdata`, `prdata` — reserved
- `reset`, `clock`, `clk`, `rst`, `presetn` — reserved
- `uvm_*` — reserved by UVM macros

The parser doesn't reject these but emits a warning per field.

## What gen-tb does NOT model in this schema (v1.2 candidates)

- **HW-side-effect fields** (read-clears, write-clears, write-1-to-clear).
  v1.1 treats all RW fields as plain registers. Add `effect: rcw1c`
  etc. in v1.2.
- **Indirect register access** (cores with INDEX + DATA pair). Add a
  `pair_with: <reg>` link in v1.2.
- **Locked registers** (write requires a key sequence). Out of scope.
- **Coverpoint hints** (`covergroup_only: true`). v1.2 cov plan
  generation.

## Cross-references

- `references/ral_gen.md` — how each yaml entry becomes uvm_reg / uvm_reg_field
- `references/spec_parsing.md` — how xlsx / csv / md / IP-XACT become this yaml
- `references/refm_dpi.md` — STATUS.reset bit → wait_low_first derivation
- `scripts/_gen_fixture_xlsx.py` — example: how the fixture xlsx is built
