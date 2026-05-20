# gen-tb regression fixtures

Each subdirectory is a self-contained IP — RTL + spec + reg table
(+ optional reference model) — that gen-tb is expected to consume and
turn into a passing UVM testbench.

## Fixtures (v1)

| Name | Class | Bus (presented to tb) | Ref model | Bundled in release |
|---|---|---|---|---|
| `uart16550` | Peripheral | APB (via wrapper over OpenCores Wishbone core) | none | No (see LICENSE.notes) |
| `aes128` | Algorithmic | APB (via wrapper over secworks core) | C-DPI | Yes |
| `axi_lite_simple_slave` | Peripheral | AXI4-Lite slave (2 regs) | none | Yes |
| `axi_lite_simple_master` | Bus master | AXI4-Lite master (one-shot write) | none | Yes |
| `ahb_simple_master` | Bus master | AHB-Lite master (one-shot NONSEQ write) | none | Yes |

The AXI4-Lite and AHB-Lite master fixtures exercise the DUT-as-master
path: a memory-backed slave responder runs in the generated TB and
`<ip>_responder_smoke_test` asserts on `tb_api::wait_for_write`. The
AXI4-Lite slave fixture additionally covers DUT-as-slave (generated
master BFM + RAL + reg_access) and ships a stub `user_axi_lite_vip/`
package driving the `reuse_my_vip` eval path.

## Adding a new fixture

A fixture is a directory containing:

```
<name>/
├── README.md              # provenance, license, what it exercises
├── LICENSE                # original upstream license (if applicable)
├── rtl/                   # synthesizable RTL — must include any
│                          # wrapper that presents an APB slave, AHB-Lite
│                          # (slave or master), or AXI4-Lite (slave or
│                          # master) interface
├── spec/
│   ├── <name>_spec.{md,pdf,docx}    # at least one behavior doc
│   └── <name>_regs.{xlsx,csv,md}    # register table
├── ref_model/             # optional, for algorithmic IPs
└── expected/
    ├── files.txt          # minimum generated-file checklist
    └── sanity.json        # which sanity cases must pass
```

The fixture's RTL **must present one of the supported bus interfaces
(APB slave, AHB-Lite slave, or AXI4-Lite slave/master) to the
testbench-facing side** (wrappers are part of the fixture, written by
us; the upstream RTL is treated as a black box). AXI4 full (bursts,
IDs, outstanding) remains out of scope.

Also update:
- `evals/fixtures/LICENSE.notes.md` with the new license row
- `.gitattributes` if the upstream license is non-permissive
