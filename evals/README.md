# gen-tb evals

Operator's walkthrough for the three-layer eval harness. The contract
itself lives in `../SKILL.md` → "Evaluation"; this file is the
runnable-recipe side.

## Layout

```
evals/
  README.md                     # this file
  evals.json                    # mechanical assertion spec
  agents/                       # grader / comparator / analyzer contracts
  fixtures/                     # frozen IP inputs
  iteration-N/                  # outputs of one harness run (gitignored)
```

`iteration-*/` is `.gitignore`'d — each run is local to the operator.

## Prerequisites

- VCS in `PATH` (or sourced via `eda-environment`); the fixtures
  compile/sim on Synopsys VCS V-2023.12-SP2.
- `claude` CLI on `PATH` (Grader / Comparator / Analyzer all spawn
  `claude -p`). Without it the harness still produces mechanical
  assertion results; LLM layers will surface a `skipped` reason and
  the run keeps going.

## Layer 1 — mechanical assertions only

```bash
python3 scripts/run_evals.py run --out evals/iteration-N/
```

Useful flags:

- `--filter <eval-name>` — run just one fixture (saves ~2 min/fixture)
- `--mode baseline` — skip scaffold.py; only useful once we have a
  "vanilla Claude without the skill" baseline path (not implemented
  yet — see `plan/eval-design.md` §三)
- `--scratch /path` — override the scratch workspace
  (default `/tmp/gen-tb-evals`)

Output:

```
evals/iteration-N/
  benchmark-with-skill.json          # roll-up index across evals
  <eval-name>/
    outputs/                         # snapshot: tb/ test/ top/ script/ work/ CLAUDE.md .prj_top
    transcript.md                    # prompt + scaffold log + compile + sim tails
    assertions_result.json           # per-assertion verdicts
```

Exit code is non-zero if any assertion fails — wire this into CI.

## Layer 2 — add the quality Grader

```bash
python3 scripts/run_evals.py run --out evals/iteration-N/ --grade
```

After each eval, the harness spawns `claude -p` reading
`evals/agents/grader.md`. Result lands at
`evals/iteration-N/<eval-name>/grading.json` with:

- 8 quality dimensions, each `strong | ok | weak | broken`
- per-finding `severity` `high | medium | low`
- `eval_feedback.suggested_assertions` — concrete proposals to harden
  `evals/evals.json` so the next iteration can catch the same issues
  mechanically

Typical grader run: 2–8 min per fixture (variance is the model side, not
the harness).

Useful flags:

- `--grader-model <id>` — pin a model (default: user's configured)
- `--grader-timeout <s>` — defaults 600s; raise if you see timeouts

If `claude` is not on `PATH`, every eval prints
`grader skipped: claude CLI not found in PATH` and the mechanical
verdicts still go through.

## Layer 3 — blind A/B + Analyzer (optional)

Two scenarios are supported today:

- **Scenario 2 — new vs prior iteration**: A = previous, B = current.
- **Scenario 3 — candidate variants**: A and B are two competing skill
  variants you want to pick between.

Scenario 1 (with-skill vs without-skill) is not implemented — see
`plan/eval-design.md` §三 if you want to add it.

### Run two iterations, then compare

```bash
# 1. Run the previous skill version
git checkout <prior-rev>
python3 scripts/run_evals.py run --out evals/iteration-1/ --grade

# 2. Run the new version
git checkout <new-rev>
python3 scripts/run_evals.py run --out evals/iteration-2/ --grade

# 3. Blind A/B with Analyzer follow-up
python3 scripts/run_evals.py compare \
    evals/iteration-1 evals/iteration-2 --analyze
```

The Comparator sees each eval as `left/` and `right/` (random
assignment per eval). Path tokens like `iteration-N` are scrubbed
from the staged text files before the agent reads them.

Outputs land at:

```
<b_dir>/_comparison/<a_label>__vs__<b_label>/
  summary.json                       # per-eval winner + confidence
  <eval-name>.comparison.json        # de-blinded axes + winner
  <eval-name>.analysis.md            # only if --analyze and winner != tie
```

`comparison.json` schema:

```json
{
  "eval_name": "...",
  "axes": [{"axis": "...", "winner": "A|B|tie", "evidence": "..."}],
  "winner": "A|B|tie",
  "confidence": "high|medium|low",
  "anti_leak_failure": false,
  "summary": "...",
  "mapping": {"left": "A|B", "right": "A|B"}
}
```

Useful flags:

- `--filter <eval-name>` — compare just one fixture
- `--out <dir>` — override default output location
- `--staging <dir>` — override staging area (default
  `/tmp/gen-tb-compare`)
- `--timeout <s>` — Comparator timeout, defaults 900s
- `--analyzer-timeout <s>` — Analyzer timeout, defaults 900s
- `--model <id>` — pin the model for both Comparator and Analyzer

### Tie short-circuit

When the Comparator returns `winner: tie`, the Analyzer **does not
spawn** `claude -p`. It writes a one-paragraph `analysis.md`
explaining no analysis is warranted and returns in milliseconds. This
keeps the loop cheap when iterations are byte-identical or
indistinguishable.

## Reading the suggestions

Two output channels feed back into the skill:

1. `grading.json` → `eval_feedback.suggested_assertions` — additions
   to `evals/evals.json` so a future iteration would catch the same
   issue mechanically. Curate before committing; the model sometimes
   suggests assertions that overlap with existing ones.

2. `<eval>.analysis.md` → "Suggested skill changes" — proposed edits
   to `SKILL.md` or `references/`. Treat these as a PR description,
   not a patch — review each item before applying.

Neither output rewrites the skill on its own. The harness is
proposal-only by design.

## Troubleshooting

- **`comparator did not write JSON`** — usually a transient `claude -p`
  failure; re-running `compare` with the same `--staging` directory
  reuses the staged inputs and just retries the agent call.
- **Grader times out at 600s** — raise `--grader-timeout`. Long
  outputs (aes128 fixture) can push past the default.
- **Comparator anti-leak warning** — `anti_leak_failure: true` in the
  output means a version token slipped through the scrubber.
  Inspect the staged files under `--staging` and add the token to
  `_VERSION_TOKEN_RE` in `scripts/run_evals.py`.

## Cost notes

Per full 3-fixture run with all layers enabled, expect roughly:

| Layer | Wall time | `claude -p` calls |
|---|---|---|
| Layer 1 (mechanical) | 3–5 min | 0 |
| Layer 2 (Grader) | +6–15 min | 3 |
| Layer 3 (Comparator + Analyzer) | +5–10 min | 3–6 |

If you're iterating fast, run Layer 1+2 in the inner loop and reserve
Layer 3 for promotion gates.
