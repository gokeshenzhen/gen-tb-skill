# Generated directory layout (gen-tb reference)

> Loaded during Phase 4 — scaffold.py uses this layout verbatim.
> SKILL.md describes the high-level shape; this file is the
> authoritative file-by-file inventory.

## The tree

```
<ip>/
├── .prj_top                              empty marker; setup.sh walks up to find it
├── rtl/                                  ONLY if gen-tb generated stubs (G1)
│   └── <ip>_stub.sv
├── ref_model/                            user-supplied DPI C/C++/Py sources (input)
│   └── (user files, gen-tb never writes here)
├── script/
│   ├── makefile                          lowercase; the public API
│   ├── setup.sh                          walks to .prj_top; exports PROJ_DIR + WORK_DIR + UVM_HOME
│   ├── check_env.sh                      validates vcs / UVM_HOME / PROJ_DIR pre-compile
│   ├── design.f                          DUT filelist; uses $PROJ_DIR/<actual-rtl-dir>
│   ├── tb.f                              tb-side filelist + incdirs (decouples from makefile)
│   └── vcm.cfg                           coverage scope (optional)
├── top/
│   ├── <ip>_tb_top.sv                    clocks, reset, DUT, bus interface instantiation
│   └── <ip>_assertions.sv                bound assertions (deferred to v1.2)
├── tb/
│   ├── <bus>_if.sv                       APB/AHB/AXI-Lite interface with input clk/rst (G12)
│   ├── tb_api/
│   │   ├── tb_api_pkg.sv                 package header (regenerated each scaffold)
│   │   └── tb_api_primitives.svh         load-bearing tasks (preserved by default; see tb_api.md)
│   ├── <bus>_agt_top/                    generated APB/AHB/AXI-Lite UVM agent
│   ├── seq_lib/                          v1.2 — DV-persona sequences
│   ├── ral/
│   │   └── <ip>_reg_block.sv             RAL (v1.1 stub; v1.2 full per ral_gen.md)
│   ├── dpi/                              ONLY if ref_model_language == c_dpi
│   │   ├── <ip>_ref_pkg.sv               SV-side imports (regenerated)
│   │   └── <ip>_dpi_proto.h              C-side prototypes (regenerated)
│   ├── ref_model/                        v1.2 — SV-side ref model wrapper component
│   └── external_vip.f                    ONLY when user has existing VIP
├── test/
│   ├── <ip>_pkg.sv                       sanity + reg_access (+ smoke if DPI)
│   ├── sv_list                           one test name per line; regression driver reads this
│   └── de_*.sv                           DE-persona directed tests (preserved across regens)
├── work/                                 created by setup.sh; gitignored
│   ├── work_<SV_CASE>_/                  per-test artifacts: simv, csrc, run.log, cov.vdb
│   └── _gen_audit/                       skill audit trail
│       ├── intake.yaml                   from Phase 2
│       ├── rtl_discovery.yaml            from Phase 1
│       ├── spec_normalized/              from Phase 3
│       │   ├── registers.yaml
│       │   ├── behavior.md
│       │   └── parse_report.md
│       ├── scaffold_audit.json           list of files written by scaffold.py
│       ├── compile_fix_attempts/         from Phase 5 (sub-agent runs)
│       ├── sanity_result.json            from Phase 6
│       └── unresolved.md                 from Phase 7 (always present, may be empty)
└── CLAUDE.md                             for future agent sessions in this dir
```

## File ownership and regeneration policy

| File | gen-tb writes? | Regenerated on each scaffold? | User-edit-safe? |
|---|---|---|---|
| `.prj_top` | yes (empty) | idempotent | no (don't edit) |
| `script/makefile` | yes | yes | no (edit intake.yaml + re-scaffold) |
| `script/setup.sh` | yes | yes | host-tweaks: edit, but rerunning will overwrite |
| `script/check_env.sh` | yes | yes | no |
| `script/design.f` | yes | yes (from rtl_discovery.yaml) | no |
| `script/tb.f` | yes | yes | no |
| `top/<ip>_tb_top.sv` | yes | yes | mostly no; manual instance ties between regens get clobbered |
| `tb/<bus>_if.sv` | yes | yes | no |
| `tb/tb_api/tb_api_pkg.sv` | yes | yes (header only) | no |
| `tb/tb_api/tb_api_primitives.svh` | yes | **only on `gen-tb refresh-primitives`** | yes — edits preserved |
| `tb/ral/<ip>_reg_block.sv` | yes | yes | no (edit registers.yaml + re-scaffold) |
| `tb/dpi/*` | yes | yes (from intake.yaml dpi_exports) | no |
| `ref_model/*.c/.h` | NEVER | n/a | yes — user owns this |
| `test/<ip>_pkg.sv` | yes | yes | no |
| `test/sv_list` | yes | yes | no |
| `test/de_*.sv` | NEVER (user adds) | n/a | yes |
| `work/_gen_audit/*` | yes | append/update | no (read-only audit trail) |
| `CLAUDE.md` | yes (on first scaffold) | only if user opts in | yes after first write |

The principle: **anything derived from intake.yaml + registers.yaml + rtl_discovery.yaml is regenerated**; everything else is the user's domain.

## Top-level paths reserved by gen-tb

These names are reserved — refuse to scaffold (Phase 0 prefight) if any
already exist as user content with a different shape:

- `rtl/` (only OK to coexist if it contains user RTL — gen-tb writes there only if stub-mode)
- `script/`
- `tb/`
- `test/`
- `top/`
- `work/`
- `.prj_top`

Names *not* reserved (user is free to keep):

- `spec/`, `doc/`, `docs/` — gen-tb reads, never writes
- `ref_model/` — gen-tb reads (C-DPI sources), never writes
- anything else the user has

## Diverges from uart_uvm_demo

| Item | uart_uvm_demo | gen-tb |
|---|---|---|
| filelist location | `rtl/design.f` | `script/design.f` (G11 / G17 — user rtl/ may be read-only) |
| tb-side filelist | inlined in makefile | `script/tb.f` (G14) |
| agent dir name | `tb/uart_agt_top/` | `tb/apb_agt_top/` (protocol-named, IP-agnostic) |
| Makefile case | lowercase `makefile` | lowercase `makefile` (kept) |

The generated `CLAUDE.md` documents these divergences for future agent
sessions in the project.
