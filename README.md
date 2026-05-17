# gen-tb

A Claude Code skill that generates a complete UVM testbench scaffold for
a single IP from its specification documents (docx / pdf / md) and
register table (xlsx / csv / md / IP-XACT). Targets dual personas:

- **DV engineers** drive the tb with UVM sequences and `uvm_test`s.
- **DE engineers** drive the tb through a thin `tb_api::` task-style BFM
  without needing UVM expertise.

## Status

**Pre-alpha** — design under discussion. Currently in this repo:

```
evals/fixtures/    # OpenCores-based regression fixtures (uart16550, aes128)
scripts/           # Helper scripts (xlsx generators, etc)
references/        # Reserved for protocol/refm/spec-parsing reference docs
assets/templates/  # Reserved for generated-tb templates
```

The `SKILL.md` is **not yet written**; this directory currently holds
the fixture infrastructure that the skill will be tested against. See
`docs/` for the in-progress design notes (when added).

## Project layout (planned)

```
gen-tb/
├── SKILL.md               # the skill entry point (forthcoming)
├── references/            # progressive-disclosure detail docs
│   ├── directory_layout.md
│   ├── makefile_contract.md
│   ├── apb.md             # APB agent generation rules
│   ├── refm_dpi.md        # C/Python DPI ref model integration
│   ├── tb_api.md          # DE-friendly task BFM
│   ├── spec_parsing.md
│   └── sub_agent_compile_fix.md
├── assets/templates/      # .sv.tmpl, makefile.tmpl, etc.
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
| Ref model lang | SV / C-DPI / Python-DPI (user picks at intake) |
| RAL input | xlsx / csv / md tables / IP-XACT |
| Spec input | docx / pdf / md |
| RTL state | Existing / external path / stub-from-spec |

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
