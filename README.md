# gen-tb

A Claude Code skill that generates a complete UVM testbench scaffold for
a single IP from its specification documents (docx / pdf / md) and
register table (xlsx / csv / md / IP-XACT). Targets dual personas:

- **DV engineers** drive the tb with UVM sequences and `uvm_test`s.
- **DE engineers** drive the tb through a thin `tb_api::` task-style BFM
  without needing UVM expertise.

## Status

**Pre-alpha implementation** — the skill entry point and the core
scaffold/eval scripts are present. The current implementation targets
APB-slave IPs and is exercised by local regression fixtures.

Currently in this repo:

```
evals/fixtures/    # OpenCores-based regression fixtures (uart16550, aes128)
scripts/           # Input discovery, normalization, scaffold, eval helpers
references/        # Progressive-disclosure implementation contracts
SKILL.md           # Skill entry point
```

Local development notes under `docs/` and `plan/` are intentionally not
tracked. User-facing and skill-facing documentation lives in `README.md`
and `references/`.

## Project layout

```
gen-tb/
├── SKILL.md               # the skill entry point
├── references/            # progressive-disclosure detail docs
│   ├── directory_layout.md
│   ├── makefile_contract.md
│   ├── apb.md             # APB agent generation rules
│   ├── apb_external_vip.md
│   ├── ral_gen.md
│   ├── refm_dpi.md        # C/Python DPI ref model integration
│   ├── tb_api.md          # DE-friendly task BFM
│   ├── spec_parsing.md
│   └── sub_agent_compile_fix.md
├── scripts/               # discover_inputs.py, parse_regs.py, scaffold.py, ...
└── evals/
    └── fixtures/          # see evals/fixtures/README.md
```

## Generated testbench layout

The skill always materializes the same directory shape (modeled on
[uart_uvm_demo](https://github.com/uart_uvm_demo)):

```
<ip>/
├── .prj_top
├── rtl/         # Either user RTL discovered in place, or a stub.
├── script/      # makefile, setup.sh, check_env.sh
├── tb/          # UVM env, agents, scoreboard, RAL, ref_model, tb_api
├── test/        # uvm_test classes, sv_list, pkg
├── top/         # tb_top.sv, assertions
└── work/        # Per-case sim outputs (gitignored)
    └── _gen_audit/   # intake.yaml, compile-fix attempts, unresolved.md
```

## Supported scope (v1)

| Dimension | Scope |
|---|---|
| Bus protocol | APB only (AHB / AXI-Lite planned) |
| Simulator | VCS only (xrun / questa planned) |
| Ref model lang | none / SV / C-DPI; Python-DPI schema only |
| RAL input | xlsx / csv / md tables / IP-XACT |
| Spec input | docx / pdf / md |
| RTL state | Existing / external path / stub-from-spec |

## Development and evals

`scripts/run_evals.py` is a development harness, not part of the
runtime skill contract. It builds temporary projects under
`/tmp/gen-tb-evals`, runs the scaffold flow against fixtures, compiles
with VCS, and checks generated-file and sanity expectations.

Run the full local regression before committing changes to parser,
scaffold, compile, or eval behavior:

```bash
source ~/.bashrc >/dev/null 2>&1 || true
python3 scripts/run_evals.py
```

For documentation-only changes, the system `skill-creator`
`quick_validate.py` check is usually enough.

## License

This skill is released under the Apache-2.0 License (see `LICENSE`).
Regression fixtures under `evals/fixtures/` carry their own upstream
licenses — see `evals/fixtures/LICENSE.notes.md` for the breakdown.
The release tarball excludes fixtures with restrictive licenses via
`.gitattributes`.

## Acknowledgements

The skill design draws on patterns from the
[`skill-creator`](https://github.com/anthropics/skills) skill
(progressive disclosure, eval harness, description optimization) and
the project structure of `uart_uvm_demo`.
