# AI-Memory Research → Implementation: Orchestration Index

**Source:** `.plans/AI-Memory-Research.md` ("Porting AI-Memory Research into YAAMS + cognitive-ledger") and `.plans/cognitive-ledger-AI-Memory-Research-TODO.md`.

**What this is:** 16 self-contained implementation plans, each runnable by a single Sonnet subagent inside the `cognitive-ledger` repo (a few also touch the separate `yaams` repo at `/Users/damsleth/code/yaams`). Each plan carries its own report context inline, real file:line anchors, an eval gate, and explicit accept/reject criteria — so a subagent needs *only* its plan file, not the whole report.

**Folding rule:** the report's "Run Tn" eval-test items are not standalone plans — you cannot run T1 without the rerank stage existing. Each Tn is folded into the feature plan it gates, as that plan's **Acceptance gate**. Stage-0 fixture authoring (which *builds* the data the Tn tests consume) is standalone.

---

## How a subagent runs one plan

1. Read **only** your assigned plan file (`NN-*.md`) + this index's "Shared conventions".
2. Branch: `git switch -c ai-mem/<plan-slug>` off `main`. One plan = one branch = one PR.
3. Implement against the file:line anchors. Anchors are stamped from a code read on 2026-06-30 — if a line moved, re-locate by the quoted symbol name (anchors are hints, symbol names are truth).
4. Run the plan's **Acceptance gate** (an `ledger ab run` / `ledger eval` invocation with a hard exit-code check). Paste the before/after metric table into the PR body.
5. **Reject-on-regression is mandatory.** Exit code 2 from `ledger ab run` = stop, do not merge, report the delta. This is the project's core anti-entropy rule.

## Shared conventions (every plan inherits these)

**Test runner:** `.venv/bin/python -m pytest` (never bare `python`/`pytest`). New modules carry unit tests at the repo's existing bar.

**Eval gate — the universal accept/reject mechanism.** Defined in `ledger/ab.py:24-27`:
| Exit | Meaning |
|---|---|
| 0 | beneficial (a metric improved, no regressions) — OK to merge |
| 2 | regression (any metric down >1e-9) — **hard stop, do not merge** |
| 3 | neutral (quality tie, latency gates pass) — merge only if the change is behaviour-neutral / off-by-default |
| 4 | invalid setup (bad refs/config/corpus) — fix harness, not a result |

**Running an A/B (code change on a branch):**
```bash
ledger ab run --baseline-ref main --candidate-ref HEAD \
  --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 \
  --eval-runs 5 --query-runs 3
echo "exit=$?"   # 0 ship / 2 reject / 3 neutral / 4 invalid
```

**Running an A/B (config knob only, no code diff)** — both arms on the same ref, flip one env var:
```bash
ledger ab run --baseline-ref HEAD --candidate-ref HEAD \
  --baseline-env  "LEDGER_PRIOR_ENABLED=0" \
  --candidate-env "LEDGER_PRIOR_ENABLED=1"
```
Env knobs are registered in `ledger/config.py:200-316` (full list there). Names used by these plans: `LEDGER_WEIGHT_SIGNAL`, `LEDGER_PRIOR_ENABLED`, `LEDGER_PRIOR_TIE_BAND`, `LEDGER_CONTRADICTION_ENABLED`, `LEDGER_CONTRADICTION_AUTO_THRESHOLD[_LANG_NO]`, `LEDGER_PROVENANCE_WEIGHTING`, `LEDGER_VALIDATION_BOOST_PER/_CAP`, `LEDGER_RETRIEVAL_MODE`, `LEDGER_FUSION`, `LEDGER_SHORTLIST_MIN/MAX`. To A/B a *new* knob, register it here first.

**Eval cases file:** `tests/fixtures/retrieval_eval_cases.yaml` (currently comments-only — Plan 01 populates it). Case schema (validated in `ledger/eval.py:207-286`):
```yaml
- query: "..."            # required
  id: unique_id           # optional (auto case_N); unique if --strict-cases
  scope: all|work|personal # optional, default "all"
  expected_any: ["notes/02_facts/foo.md", ...]  # positive case: one must rank ≤k
# OR
- query: "..."
  id: neg_1
  expected_none: true     # negative case: top score must be ≤ negative_eval_max_score (0.5)
```
Metrics (`ledger/eval.py:326-512`): `hit@1`, `hit@k`, `mrr`, plus `false_positive_rate`/`abstain_accuracy` for negatives. `ab loop` objective = `0.6·mrr + 0.4·hitk` (`ledger/ab.py:36-54`, overridable via `ab_objective_weights`).

**Autonomous tuning** (for sweeping a weight, not a code change): `ledger ab loop --space space.yaml --screen-cases ... --holdout-cases ... --benchmark-cases ...` (`ledger/ab_loop.py`). Use for T1 candidate-pool sweeps and weight tuning.

**Golden invariants (never violate — these are project non-negotiables):**
- **Non-lossy invalidation.** Close the validity window (`valid_to`) or move to `09_archive`. **Never delete.** Lean on git history for reversibility.
- **`lang:no` stays advisory.** mDeBERTa-mnli-xnli has no Norwegian data. No auto-resolution / auto-archive for Norwegian notes until NLI is validated or swapped. `contradiction_auto_threshold_lang_no: 0.95` reflects this — keep it.
- **Contradiction = supersession *candidate* surfaced as an open loop ("needs decision"), never silent auto-archive** (avoids ECon one-sided resolution). Matches AGENTS.md "surface conflicts as explicit open loops".
- **Derived/mutating state is the entropy risk.** Synthesis notes (D1) and note-evolution (D2 mutation) are gated last, behind the eval harness + git reversibility. Synthesis notes must be deterministically regenerable, never hand-edited, never a `supersedes` target.
- **Admission control is advisory.** Human-in-the-loop acceptance on `promote` stays required; scoring informs, never silently drops.
- **Tests use fixture corpora, never Kim's live `~/brain/ledger` notes.** Strip private fenced content from any prompt/JSON/web output.
- **README/CHANGELOG** updated only for shipped user-facing behaviour, not for plan edits. Keep README present-focused (no historical notes).

---

## Dependency DAG & ordering

```
Stage 0 (BLOCKING — nothing below merges until 01+02 land):
  01 eval-cases ─┐
  02 contradiction-fixtures ─┤
  03 eval-gate-mandatory ────┘  (03 is pure process/docs, parallel)

Stage 1 (quick wins, parallel after Stage 0):
  04 rerank-stage          [gate: T1, needs 01]
  05 document-weighted-sum-vs-rrf   (docs only, no deps)
  06 bitemporal-event-time [needs 01 temporal cases; touches yaams]
  07 validate-prior-tiebreaker      [needs 01]

Stage 2 (medium bets):
  08 contradiction→open-loop  [gate: T2, needs 02]
  09 attribute-slot-model     [gate: T3, needs 02+03, builds on 06+08]
  10 ebbinghaus-reinforcement [gate: T4, needs 01]
  11 amem-link-generation     [needs sleep pipeline]
  12 provenance-weighted-confidence  [A/B only, needs 01]

Stage 3 (larger bets — only behind proven gates):
  13 associative-ppr-retrieval [gate: T5, needs 01 multi-hop; touches yaams NER]
  14 hierarchical-consolidation [gate: T6, needs 01; depends on 11 link infra]
  15 amem-memory-evolution      [proposal-only; builds on 11]
  16 umem-thompson-sampling     [needs 10 stable first]
```

**Hard gates that change the plan** (from report "Benchmarks that change the plan"):
- T1 (Plan 04) shows no rerank gain → corpus too small for two-stage retrieval; **stop, revisit at scale.** Don't proceed to associative/consolidation bets.
- T2 (Plan 08) precision stays <0.9 even for `lang:en` → contradiction stays **advisory-only indefinitely** (open loops, never auto-resolve).
- T6 (Plan 14) retention <~90% → synthesis nodes stay **out of the default retrieval path** (browse-only).

## The 16 plans

| # | Plan | Report § | Stage | Gate | Touches yaams |
|---|---|---|---|---|---|
| 01 | Expand retrieval eval cases (multi-hop / temporal / negative) | T1–T7, Stage 0 | 0 | — | no |
| 02 | Contradiction + implicit-conflict fixtures (T2/T3 data) | A2/A3, T2/T3 | 0 | — | no |
| 03 | Make the `ledger ab` exit-code gate mandatory | Stage 0 | 0 | — | no |
| 04 | Recognition-memory rerank stage (`--rerank`, bge-reranker-v2-m3) | B1 | 1 | T1 | shared cap |
| 05 | Document weighted_sum > RRF + append-only invariant | B2, E1 | 1 | — | no |
| 06 | Bitemporal event-time wiring (valid_from from source ts) | A1 | 1 | — | **yes** |
| 07 | Validate the `prior` tie-breaker (prior_enabled ablation) | B3a | 1 | — | no |
| 08 | Contradiction detection → supersession-candidate open loop | A2 | 2 | T2 | no |
| 09 | Attribute-slot state model (`attribute_key`) | A3 | 2 | T3 | NER assist |
| 10 | Ebbinghaus reinforcement bridge to signal scoring | B3b | 2 | T4 | no |
| 11 | A-MEM link generation during sleep (links only) | D2 | 2 | — | no |
| 12 | Provenance-weighted confidence as A/B candidate | E2 | 2 | — | no |
| 13 | Associative PPR retrieval mode (`associative`) | C1 | 3 | T5 | **yes** (NER) |
| 14 | Hierarchical consolidation (RAPTOR / MemTree synthesis tree) | D1 | 3 | T6 | no |
| 15 | A-MEM memory evolution (note mutation, proposal-only) | D2 | 3 | — | no |
| 16 | U-Mem Thompson-sampling exploration in retrieval | B3 | 3 | — | no |

**Orchestration suggestion:** run Stage 0 (01–03) first, sequentially-ish (02 can parallel 01; 03 parallel both). Then Stages 1–3 fan out by stage, respecting the DAG. Plans within a stage with no edge between them are safe to run as parallel subagents on separate branches. Cross-repo plans (04 cap, 06, 13) coordinate a `yaams` change — call those out in the PR.

**Runner:** `./run.sh` dispatches plans to headless Sonnet agents, one git branch per plan. `run.sh dag` (show map), `run.sh check` (self-check), `run.sh stage <0-3>` / `run.sh plan <NN>` (dry-run; add `--apply` to actually spawn), `run.sh status` (branch/merge state). Dispatch is sequential within a stage; for true parallelism give each agent its own `git worktree` (the Agent tool's `isolation: "worktree"`) — add that when stage latency matters, not before.
