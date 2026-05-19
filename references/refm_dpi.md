# Reference model + DPI integration (gen-tb reference)

> Loaded during Phase 3 (intake schema lookup) and Phase 4 (DPI
> artifact generation). Applies when
> `intake.yaml: ref_model_language ∈ {c_dpi, py_dpi}`. For SV-only
> user-provided reference models, record `sv_ref_class` in
> `intake.yaml`. Register-level spec-derived behavior belongs in the
> generated RAL/reg block, not in a duplicate reference-model component.

## Why this file exists

The aes128 v1 dry-run validated end-to-end DPI flow but exposed five
specific gaps:

- intake schema was undocumented for DPI
- Makefile DPI sections were being invented per IP
- DPI signatures were triple-sourced in `intake.yaml`, SV, and C with
  no sync point
- STATUS reset semantics could break naive polling loops
- only one scoreboard mode had been described

This file gives one canonical contract that closes all five.

## The intake.yaml schema (G26)

When `ref_model_language: c_dpi`, intake.yaml must contain a
`ref_model_inputs` section with this exact shape:

```yaml
ref_model_language: c_dpi    # or py_dpi, sv, skip
ref_model_inputs:
  # All paths are $PROJ_DIR-relative, even if user RTL lives elsewhere
  c_sources:                  # list of .c files to compile + link
    - $PROJ_DIR/ref_model/tiny_aes.c
    - $PROJ_DIR/ref_model/aes_ref.c
  c_headers:                  # list of .h files (for -I dir extraction)
    - $PROJ_DIR/ref_model/tiny_aes.h
  include_dirs: []            # extra -I dirs beyond auto-extracted ones
  cflags:                     # appended to gen-tb's defaults
    - -O2
    - -Wall
  dpi_exports:                # functions DPI-imported by the SV side
    - c_name: aes128_ecb_encrypt
      sv_name: aes128_ecb_encrypt   # almost always same as c_name
      args:
        - {dir: input,  type: "bit [31:0]", name: key,  packing: "[0:3]"}
        - {dir: input,  type: "bit [31:0]", name: din,  packing: "[0:3]"}
        - {dir: output, type: "bit [31:0]", name: dout, packing: "[0:3]"}
      docstring: "Single-block AES-128 ECB encrypt"
```

For `py_dpi`, swap `c_sources/c_headers/cflags` for
`py_modules: [...]` and `py_path: [...]` (and gen-tb wires through
VCS's Python integration; details in v1.2).

For `sv` (model is a SystemVerilog class), no `ref_model_inputs`
section — instead a `sv_ref_class` field naming the class:
```yaml
ref_model_language: sv
sv_ref_class: my_ip_ref_model        # extends uvm_component
```

## Single source of truth for DPI signatures (G28)

The `dpi_exports` block above is the canonical declaration. gen-tb
regenerates two derived files from it on every scaffold:

```
tb/dpi/{{ip}}_ref_pkg.sv     # SV-side `import "DPI-C"` declarations
                              # ALWAYS regenerated — do not hand-edit
tb/dpi/{{ip}}_dpi_proto.h    # C-side function prototypes for cross-check
                              # ALWAYS regenerated — do not hand-edit
```

The user's actual C implementation (`aes_ref.c` etc.) is **not**
regenerated. The user `#include "<ip>_dpi_proto.h"` from their .c
files; this gives them compile-time enforcement of the signatures.

### Generation rule for `tb/dpi/{{ip}}_ref_pkg.sv`

For each `dpi_exports` entry, emit:

```systemverilog
import "DPI-C" function void {{sv_name}}(
    {{#args}}
    {{dir}}  {{type}} {{name}} {{packing}}{{,}}
    {{/args}}
);
```

Wrap in:

```systemverilog
`ifndef {{IP_UPPER}}_REF_PKG_SV
`define {{IP_UPPER}}_REF_PKG_SV
package {{ip}}_ref_pkg;
    // === Auto-generated from intake.yaml dpi_exports. Do not edit. ===
    // ...imports here...
endpackage
`endif
```

### Generation rule for `tb/dpi/{{ip}}_dpi_proto.h`

```c
/* === Auto-generated from intake.yaml dpi_exports. Do not edit. === */
#ifndef {{IP_UPPER}}_DPI_PROTO_H
#define {{IP_UPPER}}_DPI_PROTO_H

#include <stdint.h>
#include <svdpi.h>     /* if user wants to use svScope etc. */

#ifdef __cplusplus
extern "C" {
#endif

void {{c_name}}(
    {{#args}}
    {{c_arg_decl}}{{,}}
    {{/args}}
);
/* ... */

#ifdef __cplusplus
}
#endif

#endif
```

Where `c_arg_decl` maps from the SV type spec to C:

| SV `type` `packing` | C declaration |
|---|---|
| `bit [31:0]` `[0:N]` | `const uint32_t name[N+1]` (if input) / `uint32_t name[N+1]` (if output) |
| `bit [7:0]` `[0:N]` | `const uint8_t name[N+1]` / `uint8_t name[N+1]` |
| `int` (no packing) | `int name` (input) / `int *name` (output) |
| `bit [63:0]` | `const uint64_t name` / `uint64_t *name` |

The mapping table lives in `scripts/scaffold.py`'s `_dpi_type_map`
function. New types added there propagate to both .sv and .h
generation.

## Makefile DPI section (G27)

The makefile gets two new variables when `ref_model_language ∈ {c_dpi,
py_dpi}`. gen-tb emits them in a clearly-marked block:

```make
# === BEGIN gen-tb DPI section (auto-generated from intake.yaml) ===
# Regenerated on every `gen-tb scaffold`. Do not hand-edit; instead
# edit intake.yaml's ref_model_inputs and re-run.

C_SRCS    = {{#c_sources}}{{.}} \\{{/c_sources}}
            $(PROJ_DIR)/tb/dpi/{{ip}}_dpi_proto.h    # tracked for dep only

C_FLAGS   = {{cflags_joined}}
C_INC     = -CFLAGS "{{include_dirs_joined}} {{c_flags_joined}}"

# === END gen-tb DPI section ===

CMP_OPTS += $(C_INC) $(filter-out %.h,$(C_SRCS))
```

The `include_dirs` are auto-derived: for each `.h` listed in
`c_headers`, add `-I$(dirname header)` to `include_dirs` (dedup).
Plus any explicit `include_dirs` from intake.yaml.

The `filter-out %.h` is so the .h file in `C_SRCS` doesn't get passed
to gcc as a translation unit; it's listed only so `make` regenerates
on header changes (when gen-tb later adds proper dep tracking).

## Wait-pattern: handling "READY=1 at reset" (G29)

Many algorithmic cores publish `STATUS.READY=1` from reset (the core
is idle, hence ready). A naive smoke test that does
```
write(CTRL, START);
while ((status & READY) == 0) read(STATUS, status);
read(RESULT);
```
exits the polling loop **immediately on the first read**, before the
core has even seen the START write, because READY was already 1.

The correct pattern is to wait for READY to go LOW (core acknowledged
the start), then HIGH (core finished). `tb_api::wait_status_flag` has
a `wait_low_first` parameter for exactly this. Generated smoke test
shape:

```systemverilog
// Program inputs
tb_api::write_array(ADDR_KEY0,   4, key);
tb_api::write_array(ADDR_BLOCK0, 4, din);

// Pulse INIT: 1 then 0
tb_api::write(ADDR_CTRL, CTRL_INIT | CTRL_ENCDEC);
tb_api::write(ADDR_CTRL, CTRL_ENCDEC);
tb_api::wait_status_flag(.status_addr(ADDR_STATUS), .bit_idx(STATUS_READY_BIT),
                         .expected(1'b1), .wait_low_first(1'b1), .tag("KEY_EXPAND"));

// Pulse NEXT
tb_api::write(ADDR_CTRL, CTRL_NEXT | CTRL_ENCDEC);
tb_api::write(ADDR_CTRL, CTRL_ENCDEC);
tb_api::wait_status_flag(.status_addr(ADDR_STATUS), .bit_idx(STATUS_VALID_BIT),
                         .expected(1'b1), .wait_low_first(1'b1), .tag("ENCRYPT"));
```

When generating, gen-tb determines `wait_low_first` from the
`registers.yaml` STATUS field's reset:

| STATUS reset bit | wait_low_first |
|---|---|
| 0 (de-asserted at idle) | `1'b0` — wait once for it to rise |
| 1 (asserted at idle) | `1'b1` — wait for drop, then re-assert |

If the spec is unclear (e.g., behavior.md doesn't say), gen-tb
defaults to `wait_low_first` matching the reset bit and flags the
choice in `parse_report.md` so the user can override.

## Scoreboard mode (G31)

Two patterns. gen-tb chooses based on `intake.yaml: refm_mode`
(snapshot or stream). When unset, derive from the IP's class:

| IP behavior | refm_mode |
|---|---|
| Single-shot block cipher (ECB) | snapshot |
| Streaming cipher (CTR, CBC, GCM) | stream |
| FIFO/DMA-style transparent IP | stream |
| Memory-mapped peripheral with side effects | snapshot or skip |

### Snapshot mode

Test computes the expected via DPI once, drives DUT, compares
at end. Code shape (already used in aes128 smoke):

```systemverilog
{{ref_pkg}}::aes128_ecb_encrypt(key, din, refm);
// drive DUT...
read_array(ADDR_RESULT0, 4, 4, dut);
foreach (refm[i])
    if (dut[i] !== refm[i])
        `uvm_error("SB_SNAP", $sformatf("word %0d: dut=%h ref=%h", i, dut[i], refm[i]))
```

### Stream mode

A `<ip>_sb` `uvm_scoreboard` subscribes to:
- monitor's analysis port (decoded transactions from DUT)
- a refm callback (when the test enqueues an input, the refm
  computes the expected immediately and pushes to a queue)

Mismatches are reported transaction-by-transaction. Generate this
scoreboard under `tb/scoreboard/`; do not put scoreboard components
under `test/`.

## Limitations / v1.2 candidates

1. **Python DPI not yet implemented** — schema is defined but
   scaffold.py won't emit working .py loader. VCS's Python integration
   has historically been brittle; deferred until aes128 + a Python-
   heavy fixture (e.g., compression algorithm) both validate.

2. **No multi-context DPI** — each function call is independent. Cores
   with internal state (CBC with chained IV, GCM tag) need a context
   handle passed through DPI. Add `context_arg: <name>` to
   dpi_exports when implemented.

3. **No DPI return values** — all return-via-output-port pattern. C
   functions returning `int` aren't supported. Workaround: add an
   output arg.

4. **C++ ref models** — `c_sources` accepts only `.c`. For `.cpp`
   use a thin C shim that wraps the C++ class. v1.2 may add
   `cpp_sources` directly.

## Cross-references

- `references/tb_api.md` — `wait_status_flag`, `write_array`,
  `read_array` are the primitives used by all DPI smoke tests
- `references/registers_yaml_schema.md` — STATUS-bit reset semantics
  drive the `wait_low_first` choice
- `evals/fixtures/aes128/` — canonical end-to-end DPI fixture; the
  v1.1 implementation must reproduce its dry-run output
- `scripts/scaffold.py` — `_emit_dpi_section` and
  `_render_ref_pkg_sv` produce the artifacts described here
