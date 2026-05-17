# Fixture license notes

Each fixture under this directory is third-party material bundled solely
for the purpose of testing gen-tb. None of these files are part of the
skill's distribution surface — they are inputs to the skill, not outputs
of it. The release tarball produced by `git archive` excludes fixtures
whose upstream license is restrictive (see top-level `.gitattributes`).

| Fixture | License | Redistributable? | Release tarball |
|---|---|---|---|
| `uart16550/rtl/*.v` (excluding `uart_apb_wrap.v`) | OpenCores (custom; see file headers) | Yes, with attribution and source disclosure | **Excluded** (`export-ignore`) |
| `uart16550/rtl/uart_apb_wrap.v` | Apache-2.0 (this project) | Yes | Excluded with rest of fixture dir |
| `uart16550/spec/UART_spec.pdf` | OpenCores | Yes | Excluded |
| `uart16550/spec/uart_regs.xlsx` | Apache-2.0 (this project) | Yes | Excluded |
| `aes128/rtl/aes*.v` (excluding `aes_apb_wrap.v`) | BSD-2-Clause (secworks/aes) | Yes, with copyright notice | Included |
| `aes128/rtl/aes_apb_wrap.v` | Apache-2.0 (this project) | Yes | Included |
| `aes128/ref_model/tiny_aes.{c,h}` | Public domain (Unlicense; see `tiny_aes.UNLICENSE.txt`) | Yes | Included |
| `aes128/ref_model/aes_ref.c` | Apache-2.0 (this project) | Yes | Included |
| `aes128/spec/aes_wrapper_spec.md` | Apache-2.0 (this project) | Yes | Included |
| `aes128/spec/aes_regs.xlsx` | Apache-2.0 (this project) | Yes | Included |

If you add a new fixture, append a row here and update the top-level
`.gitattributes` if the upstream license is non-permissive.
