# Fixture: aes128

Secworks AES core (AES-128 / AES-256, ECB) plus a thin APB wrapper and a
C reference model. Used as the algorithmic-class fixture for gen-tb
evaluation — exercises C-DPI reference-model integration.

## Provenance

| Item | Source | License |
|---|---|---|
| `rtl/aes*.v` (excluding wrapper) | github.com/secworks/aes @ `80dc4718` | BSD-2-Clause (see `LICENSE`) |
| `rtl/aes_apb_wrap.v` | Hand-written for this fixture | Apache-2.0 |
| `ref_model/tiny_aes.{c,h}` | github.com/kokke/tiny-AES-c | Public domain (Unlicense, see `tiny_aes.UNLICENSE.txt`) |
| `ref_model/aes_ref.c` | Hand-written DPI shim | Apache-2.0 |
| `spec/aes_wrapper_spec.md` | Hand-written | Apache-2.0 |
| `spec/aes_regs.xlsx` | Hand-authored from `rtl/aes.v` register map | Apache-2.0 |

The BSD-2-Clause and Unlicense terms allow redistribution; this fixture
is freely shippable as part of gen-tb releases (unlike uart16550).

## Intended use

This fixture exercises:

- 7-file RTL discovery + APB wrapper inclusion
- Register-table-from-xlsx parsing (25 fields across 16 registers, mix of
  RO/RW/WO, multi-word arrays like KEY0..3 / BLOCK0..3 / RESULT0..3)
- Markdown spec ingestion (`aes_wrapper_spec.md`)
- C-DPI reference-model integration (`aes_ref.c` with NIST FIPS-197 vectors)
- Scoreboard comparing DUT ciphertext against `aes128_ecb_encrypt()` output

## DPI contract

The generated tb is expected to declare:

```systemverilog
import "DPI-C" function void aes128_ecb_encrypt(
    input  bit [31:0] key  [0:3],
    input  bit [31:0] din  [0:3],
    output bit [31:0] dout [0:3]);
import "DPI-C" function void aes128_ecb_decrypt(
    input  bit [31:0] key  [0:3],
    input  bit [31:0] din  [0:3],
    output bit [31:0] dout [0:3]);
```

and link `tiny_aes.c` + `aes_ref.c` via the VCS `-CFLAGS`/`.c` mechanism
in the generated makefile.

## Out of scope for this fixture

AES-256, CBC/CTR/GCM modes, performance/throughput checks — only AES-128
ECB bit-accurate equivalence is required.
