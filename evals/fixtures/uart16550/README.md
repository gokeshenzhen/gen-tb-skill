# Fixture: uart16550

OpenCores 16550-compatible UART core. Used as the peripheral-class reference
fixture for gen-tb evaluation.

## Provenance

| Item | Source |
|---|---|
| `rtl/*.v` | github.com/freecores/uart16550 @ `2b0ad80d` |
| `spec/UART_spec.pdf` | Same upstream `doc/UART_spec.pdf` |
| `spec/uart_regs.xlsx` | Hand-authored from the 16550 register definitions in the PDF, in the schema gen-tb's parser consumes |

## License

The RTL is OpenCores material. The original headers in each `.v` file
describe the upstream license terms. This directory is bundled only as an
evaluation input; it is excluded from the gen-tb release tarball via
`.gitattributes` and is not redistributed as part of the skill.

## Intended use

This fixture exercises:

- RTL discovery (12 verilog files, Wishbone-style bus)
- Register-table-from-xlsx parsing
- PDF spec ingestion
- Generation of a UVM testbench wrapping an asymmetric paired DUT
  (see `expected/files.txt` for required outputs)
- Sanity test (reset + idle + a basic loopback transfer)

## Note on bus protocol

uart16550 uses a Wishbone interface natively. gen-tb's one-shot scope is
APB; for evaluation this fixture is wrapped on the fly by the generator
as if it were an APB peripheral (the address/data width mapping is 1:1
and there is no Wishbone-specific handshake the APB driver cannot emit).
The expected output set assumes APB.
