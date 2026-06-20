# autoresearch-ab-loop

_Created 2026-06-19_

Port the **autoresearch** pattern — A/B-tested incremental improvement against a measurable
metric — into cognitive-ledger.

## TL;DR / the lazy framing

cognitive-ledger already has 90% of autoresearch built: `ledger ab run` does worktree
isolation, isolated subprocess probes, hit@1/hit@k/MRR quality metrics, latency, a
regression/beneficial/neutral decision rule, per-side config overrides (`--baseline-env` /
`--candidate-env`), corpus fingerprinting, and JSON+MD reports. That **is** autoresearch's
"propose → test → measure → accept" — minus the autonomous loop.

autoresearch's actual novel parts that we're missing:
1. **One headline metric** to optimize (we have three: hit1/hitk/mrr — need a scalar).
2. **An autonomous loop** that proposes a variant, runs the test, keeps winners, repeats.
3. **An append-only results log** (their `results.tsv`) so progress is inspectable.
4. **A proposal source** — autoresearch edits `train.py`; ours sweeps config params
   (everything tunable is already env-overridable, so *no code edits needed* to propose).

So the build is a thin driver around the existing harness, not a new harness.

**Do NOT rebuild ab.py.** Reuse `run_cli_harness` / its decision logic and exit codes.

## Goal

A `ledger ab loop` command that, given a parameter search space, autonomously runs
A/B experiments against the existing eval cases, keeps improvements (advancing the
"champion" config), discards regressions, and logs every trial to an append-only file —
runnable unattended, resumable, stoppable.

## The 5 autoresearch principles → cognitive-ledger mapping

| autoresearch | cognitive-ledger equivalent | status |
|---|---|---|
| `val_bpb` (single scalar, lower=better) | composite quality score (higher=better) | **build** (step 1) |
| edit `train.py` to propose | emit `--candidate-env KEY=VALUE` set | reuse env-override path |
| `uv run train.py` (fixed budget) | `ledger ab run` (fixed eval cases / runs) | **reuse** |
| `grep val_bpb` from log | parse `ab_eval.json` decision block | **reuse** (ab.py emits it) |
| keep (git commit) / discard (git reset) | advance champion env-set / drop candidate | **build** (step 2) |
| `results.tsv` append-only log | `ab_loop_results.jsonl` | **build** (step 3) |
| run forever until interrupted | `ledger ab loop` driver | **build** (step 4) |
| seed=42, pinned val shard | pinned eval cases + `--eval-runs N` averaging | **reuse** |

## Decisions (locked 2026-06-19)
- **Objective**: `0.6*mrr + 0.4*hitk` (weights configurable via `ab_objective_weights`).
- **Eval sets**: three already exist in `~/brain/ledger/08_indices/` — use the train/screen/
  holdout split (see Step 0). Optimize on **screen (31)**, gate on **holdout (14) + main (45)**.
- **Scope**: build v1 (mechanical sweep) AND design v2 (LLM-proposed mutations) now — v2 is
  a `proposal_stream` implementation, not a rewrite. See Step 6 + Step 8.

## Steps

### Step 0 — Eval-set roles: the overfitting guard (the part autoresearch lacks)
This is the most important correction. autoresearch optimizes and measures on the *same*
pinned val shard — fine for a single metric on a huge corpus, **dangerous** on our 45 small
cases: a loop that proposes, measures, and accepts all against one set will overfit to it.
We have three sets already — assign distinct, non-overlapping roles:

| File | Cases | Role in the loop |
|---|---|---|
| `retrieval_eval_screen.yaml` | 31 | **Optimization set.** The loop's accept/reject (`--cases`) runs here. Champion advancement is decided on screen alone. |
| `retrieval_eval_holdout.yaml` | 14 | **Holdout gate.** Never drives advancement. A new champion is only *confirmed* if its objective on holdout is also non-negative vs the prior champion. Screen↑ but holdout↓ ⇒ overfitting → reject + flag. |
| `retrieval_eval_cases.yaml` | 45 | **Canonical benchmark.** What existing `ledger ab run` / past `.claude-mem/ab-results` report on. Measure the *final* champion here for continuity; do NOT optimize against it. |

- `ponytail:` no new eval data needed — the sets exist; this step is pure wiring + a holdout
  check in the accept rule (Step 2). The whole overfitting defense is ~10 lines.
- Note: all 90 cases are positive-only (no `expected_none`). So the harness's negative-case
  path is inert for now — don't build anything that depends on it (YAGNI). If precision-style
  regressions become a concern, add `expected_none` cases later and the objective can grow a
  precision term then, not now.
- Guardrail still stands: if any set's count drops below ~10–15, that set gets too noisy to
  gate on — halt and regrow before trusting results.

### Step 1 — Define ONE headline metric (`ledger/ab.py`)
autoresearch's discipline is a *single* number. We have three. Add a scalar objective so
"better" is unambiguous and the loop is fully autonomous (no human tie-breaking).
- Add `compute_objective(quality: dict) -> float` to `ab.py`. **Default: `0.6*mrr + 0.4*hitk`**
  (locked). Make the weights a config knob (`ab_objective_weights`) so the objective itself
  is A/B-able.
- Keep the **existing** `decide_outcome` regression guard as a hard gate: a candidate that
  regresses *any* individual quality metric is rejected even if the composite ticks up.
  (Objective decides *among non-regressions*; the existing guard prevents pathological wins.)
- Emit `objective` into the `decision` block of `ab_eval.json` (one line in the report dict).
- Test: `test_ab_objective` — monotonic in each metric, regression guard still fires.

### Step 2 — Champion/challenger advancement (the keep/discard rule)
autoresearch keeps the git commit on improvement, resets on regression. Our "state" is a
config env-set, not a commit — simpler, no git mutation.
- The **champion** is a dict of `KEY=VALUE` config overrides (starts empty = current config
  defaults = baseline).
- Each trial: baseline = champion env-set; candidate = champion + the proposed mutation.
- Run via the existing `run_cli_harness` path with `--baseline-env`/`--candidate-env`.
- Decision (two-gate, screen then holdout):
  1. **Screen gate** — run on `retrieval_eval_screen.yaml`. Require `decision == beneficial`
     (existing regression guard) AND `candidate.objective > champion.objective + ab_loop_min_delta`
     (default ~0.002 — autoresearch's "strictly better" plus our noise floor).
  2. **Holdout gate** — only if the screen gate passes, re-probe the candidate vs champion on
     `retrieval_eval_holdout.yaml`. Require holdout objective delta `>= -ab_loop_holdout_tol`
     (small tolerance, ~0 — holdout must not *regress*; it needn't improve as much as screen).
  - Champion advances only if **both** gates pass. Screen↑ / holdout↓ ⇒ log `overfit` and
    discard. This is the single most important rule in the whole design.
- `ponytail:` holdout is only re-probed for screen-winners (a few per run), so the extra
  eval cost is negligible — no need to run holdout on every trial.
- `ponytail:` champion is in-memory + persisted to `champion.json`; no git branches because
  params are runtime config. Upgrade path: for code-level proposals, switch the proposal
  source to git refs (ab.py already supports `--candidate-ref`).

### Step 3 — Append-only results log (their `results.tsv`)
- Write `ab_loop_results.jsonl` (JSONL > TSV: structured, harness already speaks JSON).
  One line per trial: `{trial, timestamp, mutation, candidate_env, screen_objective,
  screen_quality, holdout_objective (null if screen gate failed), latency_p95, decision
  (beneficial|regression|neutral|overfit), accepted}`. The `overfit` decision (screen↑
  holdout↓) is the new state worth surfacing — count it in the summary.
- This file IS the audit trail + resume source (step 5) + chart source (reuse `ab_charts.py`,
  point it at this log or convert to its `performance_series.json` shape).

### Step 4 — The loop driver (`ledger ab loop`)
New subcommand in `cli.py` (argparse, same registration as `ab run`/`ab charts`).
Delegate to a new `ledger/ab_loop.py::main_cli`. Core loop:

```
champion = load_champion() or {}                     # empty = current defaults
champion_obj = trial(champion, champion, SCREEN).cand_obj   # baseline on screen
for mutation in proposal_stream(space, history):     # see step 6
    cand = {**champion, **mutation}
    screen = run_cli_harness(champion, cand, cases=SCREEN)   # reuse!
    accepted = False
    if screen.decision == "beneficial" and screen.cand_obj > champion_obj + min_delta:
        hold = run_cli_harness(champion, cand, cases=HOLDOUT)  # gate, screen-winners only
        if hold.cand_obj - hold.base_obj >= -holdout_tol:
            champion, champion_obj = cand, screen.cand_obj
            save_champion(champion); accepted = True
        else:
            screen.decision = "overfit"               # screen↑ holdout↓
    log_jsonl(screen, hold?, mutation, accepted)      # else discard (autoresearch's reset)
    if budget_exhausted(): break
# final: measure champion on MAIN (45) for benchmark continuity; print summary
```

Flags (mirror autoresearch's fixed-budget ethos):
- `--space FILE` — YAML search space (step 6). Required.
- `--max-trials N` — hard cap (`0` = until interrupted; default finite for overnight/CI).
- `--screen-cases` / `--holdout-cases` / `--benchmark-cases` — default to the three files in
  `~/brain/ledger/08_indices/`; the loop's accept rule uses screen + holdout, final report
  uses benchmark.
- `--k`, `--eval-runs`, `--query-runs` — passed straight through to `ab run`.
- `--out-dir` — where champion.json + results.jsonl live.
- `--resume` — continue from existing results.jsonl + champion.json (step 5).
- Handle SIGINT cleanly: finish current trial, flush log, print champion summary, exit 0
  (autoresearch's "loop until the human interrupts you, period.").

### Step 5 — Resume / idempotency
autoresearch is checkpointed by git commits. Ours: champion.json + results.jsonl are the
checkpoint.
- On `--resume`: load champion.json as the starting champion; skip mutations already in
  results.jsonl (dedup on mutation key); continue.
- Same eval cases + same env-set → harness already deterministic-enough (eval averaged over
  `--eval-runs`). No extra seed work beyond what `ab run` already does.

### Step 6 — Proposal stream (the search space)
autoresearch's proposals are open-ended LLM ideas over `train.py`. Ours start mechanical and
bounded — the lazy, safe version first.
- **v1 (ship this): grid / coordinate-descent search over a declared space.** A `space.yaml`:
  ```yaml
  score_weight_bm25:    [0.20, 0.30, 0.40]
  score_weight_recency: [0.10, 0.15, 0.20]
  prior_weight:         [0.05, 0.10, 0.15]
  fusion:               [weighted_sum, rrf]
  ```
  Each maps to a `LEDGER_*` env override (reuse the env-var names config.py already defines
  for every tunable). `proposal_stream` yields one mutation at a time.
- `ponytail:` coordinate descent (change one param off the champion at a time) over a full
  grid — cheaper, matches autoresearch's "one idea per experiment" rhythm. Full grid /
  Bayesian opt only if coordinate descent plateaus. Skipped: Optuna/Ax dependency — YAGNI
  until a hand-rolled sweep measurably underperforms.
- **v2 (designed now, see Step 8): LLM-proposed mutations.** An agent reads the
  results.jsonl trend and proposes the next env-set (closer to true autoresearch). The
  driver doesn't care where mutations come from — `proposal_stream` is just an iterator, so
  v2 is a drop-in replacement, not a rewrite. Keep the iterator interface narrow:
  `proposal_stream(space, history) -> Iterator[dict]` so both v1 and v2 satisfy it.

### Step 7 — Guardrails & reporting
- **Validate proposed env-sets** before running (don't burn a trial on a typo'd key). Reuse
  config.py's loader to parse-check each candidate.
- **Latency is a constraint, not the objective** (reuse existing `latency_tol_pct/ms`): a
  candidate that wins quality but blows latency tolerance is `neutral` → not accepted.
- **Summary at end**: champion env-set + objective delta vs starting baseline, measured on
  **all three sets** (screen / holdout / canonical 45-case benchmark) so transfer is visible
  at a glance; trial count, acceptance rate, and **overfit-rejection count** (how often
  screen wins didn't hold up — a high count means the screen set is too easy or the
  min-delta too loose). Optionally `ledger ab charts` over the loop log.
- **No silent caps**: if `--max-trials` truncates an unfinished grid, log how many of the
  space remain (the ponytail "no silent truncation" rule).

### Step 8 — v2: LLM-proposed mutations (the true-autoresearch path)
autoresearch's real magic is open-ended ideas, not a fixed grid. v2 swaps the mechanical
`proposal_stream` for an agent-driven one, reusing **everything** else (driver, champion,
log, harness, objective).
- **Interface (shared with v1)**: `proposal_stream(space, history) -> Iterator[dict]`. v1
  ignores `history`; v2 consumes it.
- **The proposer**: a single subagent call per trial (or per batch of N). Input: the
  `space.yaml` bounds (what's tunable + valid ranges), the current champion env-set, and the
  last K rows of `ab_loop_results.jsonl` (what's been tried + objective deltas). Output:
  one proposed mutation dict, strictly within the declared bounds.
- **Why bounded**: the agent proposes *values for known keys*, not arbitrary code — keeps
  the safety/validation story identical to v1 (Step 7 validation applies unchanged). No
  code-mutation path, no new attack surface. `ponytail:` the agent is a smarter search
  policy over the *same* space, not a new capability.
- **Stop condition**: same as v1 (`--max-trials`, SIGINT). Plus an optional "the agent
  declares the space exhausted / no promising direction left" early-exit, logged explicitly.
- **Cost control**: one LLM call per trial is cheap next to the eval runs; gate behind a
  `--proposer llm|grid|coord` flag (default `coord` for unattended/CI; `llm` when a human
  wants exploration). Don't make LLM the default — keeps overnight runs deterministic.
- **Reuse note**: this is ~the only place the loop touches an LLM. The harness, objective,
  champion logic, and log are all proposer-agnostic — confirm no v1 code needs changing to
  add v2 (if it does, the interface in Step 4/6 was drawn wrong).

## Notes / decisions

- **Biggest risk is the eval set, not the loop.** A loop optimizing a 5-case eval set
  overfits to those 5 cases. Step 0 is the real work; the loop is ~150 lines of glue.
- **Reuse over rebuild**: the only genuinely new files are `ledger/ab_loop.py` (driver) and
  a `space.yaml`. `ab.py` gets one new function (`compute_objective`) + one report field.
- **Why config-sweep not code-edit**: autoresearch edits `train.py` because ML architecture
  isn't config-exposed. cognitive-ledger already exposes every retrieval knob via `LEDGER_*`
  env vars + `--candidate-env`, so the proposal mechanism is free. Don't add a code-mutation
  path we don't need.
- **Statistical rigor**: autoresearch does single deterministic runs (no significance test).
  We're noisier (real corpus, small eval set) — `--eval-runs N` averaging is our cheap
  variance reduction. Skip t-tests/CIs until the min-delta gate proves insufficient (YAGNI).
- **Activation gates already exist** (signals ≥20, contradiction off, PRF/provenance
  off-by-default). The loop is the *mechanism* to retire those gates: prove a feature
  beneficial via the loop, then flip it on in live config. Directly serves the open thread in
  memory (signal weight still 0.0 awaiting A/B validation).

## Build order (all questions resolved)
1. Step 1 — `compute_objective` + `ab_objective_weights` knob + test.
2. Steps 2–5 — `ledger/ab_loop.py` driver: champion advancement, JSONL log, `ledger ab loop`
   subcommand, resume, SIGINT handling.
3. Step 6 v1 — `space.yaml` + coordinate-descent `proposal_stream(space, history)`.
4. Step 7 — validation + latency-constraint + end-of-run summary.
5. Step 8 v2 — LLM proposer behind `--proposer llm`, reusing the v1 iterator interface.
