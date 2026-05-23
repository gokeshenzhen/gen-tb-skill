# Makefile contract (gen-tb reference)

> Loaded during Phase 4. The Makefile is the public API of every
> generated tb — regression scripts, CI, TraceWeave, and human users
> all expect a fixed set of variables and targets. This file
> declares them.

## Public variables

| Variable | Default | Meaning |
|---|---|---|
| `SV_CASE` | `<ip>_sanity_test` | name passed to `+UVM_TESTNAME=`; one entry from `test/sv_list` |
| `seed` | `$(shell date +1%N)` | random seed; nanosecond-timestamp default for repeatability |
| `cov` | `0` | set to `1` to enable `tgl+line+cond+fsm+branch` collection |
| `UVM_VER` | `1.2` | `-ntb_opts uvm-$(UVM_VER)`; honors intake.yaml |
| `VCS` | `vcs` | simulator executable (VCS makefile); override on cmdline if non-standard |
| `XRUN` | `xrun` | simulator executable (xrun makefile); override on cmdline if non-standard |
| `FLIST` | `$(PROJ_DIR)/script/design.f` | RTL filelist |
| `TBLIST` | `$(PROJ_DIR)/script/tb.f` | tb-side filelist |

## Public targets

| Target | Effect |
|---|---|
| `make comp` | compile only; output to `$(SIM_DIR)/simv` |
| `make run SV_CASE=<case>` | run an existing `simv` with the chosen test |
| `make all SV_CASE=<case>` | `comp` + `run` (the most common command) |
| `make all SV_CASE=<case> seed=42 cov=1` | reproducible seeded run with coverage |
| `make clean` | remove `work/work_*` (not `work/_gen_audit`) |
| `make wave` | launch verdi with sources |
| `make merge` | urg-merge all `work/*/cov/*.vdb` → `work/cov_report` |
| `make help` | print this list |

## Per-case directory layout

Each `SV_CASE` value gets its own `$(SIM_DIR)`:

```
work/work_<SV_CASE>_/
├── simv                  compiled exec
├── csrc/                 VCS intermediate
├── comp.log              compile log
├── run.log               simulation log
└── cov/COVERAGE.vdb      coverage (if cov=1)
```

The trailing `_` after `<SV_CASE>` is **intentional** — it preserves
the convention from uart_uvm_demo where a second optional segment
(`<SV_CASE>_<C_CASE>`) may follow for parameterized C-side variants.
v1.1 doesn't use the second slot; v1.2 might.

## DPI section (auto-generated, `ref_model_language == c_dpi` only)

```make
# === BEGIN gen-tb DPI section (auto-generated from intake.yaml) ===
C_SRCS    = $(PROJ_DIR)/ref_model/tiny_aes.c \\
            $(PROJ_DIR)/ref_model/aes_ref.c
C_INC     = -CFLAGS "-I$(PROJ_DIR)/ref_model -O2 -Wall"
# === END gen-tb DPI section ===

CMP_OPTS += $(C_INC) $(C_SRCS)
```

VCS picks up `.c` files on the compile command line automatically and
runs `gcc -c` per file. The `-CFLAGS "..."` argument is quoted at the
make level so it survives as a single VCS argument.

If `c_sources` is empty, omit the whole DPI section.

## Variables NOT in the public API

Internal helpers (subject to change without notice):

- `SIM_DIR`, `COMP_LOG`, `SIM_LOG`, `SIMV` — derived paths
- `CMP_OPTS`, `SIM_OPTS` — VCS argument bundles
- `dpi_section`, `extra_cmp` — scaffold.py template fragments

Do not invoke or override these from regression scripts.

## Multi-simulator support (`intake.yaml: simulators`)

`scaffold.py` reads the `simulators` list from `intake.yaml` (default
`[vcs]`, allowed values `vcs` and `xrun`) and emits one makefile per
selected simulator side-by-side under `script/`:

| Simulator | File | Invocation |
|---|---|---|
| `vcs` | `script/makefile` | `make all SV_CASE=<case>` |
| `xrun` | `script/makefile_xrun` | `make -f makefile_xrun all SV_CASE=<case>` |

Both files honor the same public variables (`SV_CASE`, `seed`, `cov`,
`UVM_VER`, `FLIST`, `TBLIST`) and the same targets (`comp`, `run`,
`all`, `clean`, `wave`, `merge`, `help`). The xrun variant compiles
+ elaborates with `xrun -elaborate` and runs with `xrun -R`, layering
the Cadence-bundled UVM via `-uvmhome CDNS-$(UVM_VER)`. The DPI
section is regenerated for xrun using `-cflags` instead of VCS's
`-CFLAGS`.

`scripts/compile_and_sanity.py` drives VCS only; for xrun-only
configurations the user runs the mandatory tests manually with
`make -f makefile_xrun all SV_CASE=<case>`.

## Why lowercase `makefile`

Both `Makefile` and `makefile` are accepted by GNU make. We use
lowercase to match the uart_uvm_demo reference project (and most
Linux conventions). The choice is sticky — once a fixture is shipped
with `makefile`, switching to `Makefile` would break user muscle
memory.

## Symlink guard hits this file

When the user's `rtl/` directory is a read-only symlink (e.g., into
an IP library), gen-tb cannot write `rtl/design.f` there. The Makefile
contract therefore puts `design.f` in `script/` — gen-tb-owned space
that's always writable. This rule came from the uart16550 dry-run,
where writing a generated file into a user-owned `rtl/` symlink was
both fragile and semantically wrong.

## Adding a target (forward-compat)

Future versions may add targets. To stay forward-compatible:

- Add only targets — never remove or rename existing ones
- New variables get a `?=` so users can override
- Document in this file before merging

## Cross-references

- `references/directory_layout.md` — where each file in the tree lives
- `references/refm_dpi.md` — DPI section deep-dive
- `scripts/scaffold.py` `emit_makefile()` — the implementation
