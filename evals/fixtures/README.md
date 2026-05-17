# gen-tb regression fixtures

Each subdirectory is a self-contained IP — RTL + spec + reg table
(+ optional reference model) — that gen-tb is expected to consume and
turn into a passing UVM testbench.

## Fixtures (v1)

| Name | Class | Bus (presented to tb) | Ref model | Bundled in release |
|---|---|---|---|---|
| `uart16550` | Peripheral | APB (via wrapper over OpenCores Wishbone core) | none | No (see LICENSE.notes) |
| `aes128` | Algorithmic | APB (via wrapper over secworks core) | C-DPI | Yes |

DMA / bus-master class fixtures are deferred until AHB / AXI-Lite
support lands (v2).

## Adding a new fixture

A fixture is a directory containing:

```
<name>/
├── README.md              # provenance, license, what it exercises
├── LICENSE                # original upstream license (if applicable)
├── rtl/                   # synthesizable RTL — must include any
│                          # wrapper that presents an APB slave
├── spec/
│   ├── <name>_spec.{md,pdf,docx}    # at least one behavior doc
│   └── <name>_regs.{xlsx,csv,md}    # register table
├── ref_model/             # optional, for algorithmic IPs
└── expected/
    ├── files.txt          # minimum generated-file checklist
    └── sanity.json        # which sanity cases must pass
```

The fixture's RTL **must present an APB slave interface to the
testbench-facing side** (wrappers are part of the fixture, written by
us; the upstream RTL is treated as a black box). v2 will drop this
constraint for AHB / AXI-Lite.

Also update:
- `evals/fixtures/LICENSE.notes.md` with the new license row
- `.gitattributes` if the upstream license is non-permissive
