# Retrieval, eval and signals

Reference for retrieval tuning. `AGENTS.md` carries the rules; this file carries the detail and the measurements behind them.

## Retrieve & Eval

```bash
ledger query "<topic>" --scope all --limit 8
ledger query "<topic>" --scope all --limit 8 --retrieval-mode <mode>
ledger query "<topic>" --scope all --limit 8 --prf   # per-query PRF (dense path only)
ledger query "<topic>" --scope dev --bundle
ledger query "<topic>" --as-of 2025-06-01   # notes valid on that date (includes archive)
ledger discover-source "<topic>" --source-notes-dir <root> --limit 20
ledger embed build --target ledger --backend local --model TaylorAI/bge-micro-v2
ledger embed status --target both
ledger embed search --query "<topic>" --json
printf '%s\n' '{"query":"<a>"}' '{"query":"<b>"}' | ledger embed search --batch   # one encoder load, JSONL out
ledger eval --cases "$(ledger paths --field ledger_notes_dir)/08_indices/retrieval_eval_cases.yaml" --k 3 --strict-cases
ledger signal patterns       # mine failure/strategy patterns from the signal log (docs/wikiskill.md)
ledger loops                 # compact list (default)
ledger notes --type <all|identity|facts|preferences|goals|loops|concepts>
ledger context --format boot # session boot payload
ledger context --format identity  # identity notes only
```

**Stale-index warning.** In `semantic_hybrid` the semantic half of the score
comes from the embedding index: a note that was never embedded scores 0 there
and survives only on lexical overlap (so it usually loses to embedded notes),
and an edited one ranks on its old content. Candidates themselves come from the
notes on disk (`build_candidates`), so the note is reachable, just badly ranked.
The failure is silent — which makes "I imported it and the query
still misses" look like bad retrieval rather than a stale index. `ledger query`
therefore checks the mtimes of the notes the index embeds (facts, preferences,
goals, loops, concepts — not inbox, identity or archive) against the index build
time and warns on **stderr** (so `--json` output stays clean):

```
warning: 2 note(s) changed since the semantic index was built (...). In
semantic_hybrid the candidate pool comes from the index, so a new note is
unreachable and an edited one ranks on its old content. Rebuild with:
ledger sleep index
```

Rebuild with `ledger sleep index` (which also regenerates the semantic index) or
`ledger embed build --target ledger`. The check is one `stat` per note.
`ledger inbox triage` rebuilds by itself when it promotes notes; a failed build
there only warns, the triage stays applied.

**Model loading is cache-first.** The local encoder loads from the Hugging Face cache with the Hub switched off for that attempt, and goes online only when the model is not cached. An online load costs several seconds of Hub requests per CLI call even when nothing needs downloading, and `local_files_only=True` alone still leaves some of them (transformers asks whether a `.bin` checkpoint has a safetensors conversion). Set `LEDGER_EMBEDDINGS_OFFLINE` (`1`, `true`, `yes`, `on`) to forbid the Hub fallback entirely, for air-gapped machines or to rule out a download during `ledger embed build`.

**Usage log (opt-in).** `query_log_enabled: true` in `config.yaml` (or `LEDGER_QUERY_LOG=1`) appends one line per `ledger query`, `ledger answer` and web search to `08_indices/query_log.jsonl`: timestamp, source (`cli`, `answer`, `web`), raw query text, scope, mode, top 3 paths, latency. Eval and A/B runs are never logged. `ledger signal seed --from-history` reads this file. Check it before tuning retrieval: it shows which surfaces and modes are used at all.

## Bitemporal validity

```bash
ledger migrate bitemporal --check     # preview back-fill (no writes)
ledger migrate bitemporal --apply     # write valid_from / valid_to + timeline entry
ledger sleep lint                     # flags invalid timestamps, ordering errors, dangling refs
```

`migrate bitemporal` does **not** bump `updated`. A validity back-fill is
metadata, not a content edit; bumping it re-dated 591 notes for the recency
prior and cost hit@1 (0.783 → 0.761 on a copy of the live corpus, 2026-10-14).

Four optional frontmatter fields track *when a fact was true* (valid-time), separate from
`created`/`updated` (transaction time). Applicable to `01_identity`, `02_facts`,
`03_preferences`, `04_goals`, `06_concepts`, and `09_archive`; `00_inbox` is exempt.
All fields are optional — notes without them lint clean and retrieve identically to today.

| Field | Meaning |
|---|---|
| `valid_from` | ISO 8601 UTC: when the fact became true. Absent on legacy notes is lint-clean; missing only warns when other bitemporal fields exist. |
| `valid_to` | ISO 8601 UTC: when the fact ceased to be true. `null` = currently valid (open interval). |
| `superseded_by` | Logical `notes/…/note.md` path to the replacement note. Requires `valid_to` to be set. |
| `supersedes` | List of logical `notes/…/note.md` paths this note replaces. Written by `supersede()`. |

**Supersession:** when a fact is replaced, use the `supersede()` primitive in `ledger/bitemporal.py`.
It sets `valid_to` on the old note, writes `superseded_by`, copies `supersedes` onto the new note,
and moves the old file to `09_archive/`. The old note is never deleted.
Default retrieval hides notes with an expired `valid_to`; use `--as-of` to query historical state.
A query with an explicit past year after a preposition ("in 2023", "i 2023") engages
`--as-of <year>-07-01` by itself and says so on stderr; a bare year or a relative cue
("before I moved") does not. Typed archive notes (`fact__`, `pref__`, ... in `09_archive/`)
are embedded so an as-of read can rank them, but `embed search` and the contradiction scan
never see them — YAAMS dedups promotions through `embed search`.

**Prior score (cold-start ranking, always-on, applied as a TIE-BREAKER).** Before signal feedback accrues, a prior combining note confidence, half-life recency decay (default 180 days), and query relevance breaks ties between near-equal candidates. It is *not* a flat additive bonus: each candidate's prior contribution scales continuously to zero as its base (pre-prior) score gap to the local leader exceeds `prior_tie_band` (default 0.02, a 2% relative gap), so a clear semantic/lexical winner keeps rank-1 regardless of its prior. The blend is implemented once in `apply_prior_tiebreak` (in `ledger/retrieval.py`) and used by both the lexical and `semantic_hybrid` paths. Config keys (all optional, safe defaults): `prior_enabled` (true), `prior_weight` (0.10), `prior_tie_band` (0.02), `prior_w_importance` (0.30), `prior_w_recency` (0.30), `prior_w_relevance` (0.40), `prior_recency_half_life_days` (180.0). Env overrides: `LEDGER_PRIOR_ENABLED`, `LEDGER_PRIOR_WEIGHT`, `LEDGER_PRIOR_TIE_BAND`, `LEDGER_PRIOR_W_IMPORTANCE/RECENCY/RELEVANCE`, `LEDGER_PRIOR_HALF_LIFE`. Set `prior_enabled: false` to reproduce pre-prior scores as an A/B baseline.

**Fusion mode (`semantic_hybrid`).** `fusion: weighted_sum` (default, byte-identical to previous behaviour) or `fusion: rrf` (Reciprocal Rank Fusion — merges independent lexical + semantic rank lists over the full candidate pool). `rrf_k` controls the smoothing constant (default 60). Env: `LEDGER_FUSION`. **Keep `fusion: weighted_sum`: on the real corpus RRF measures worse (2026-10-14 `ledger ab run`, live cases: hit@1 0.652 vs 0.783, hit@3 0.848 vs 0.870, mrr 0.755 vs 0.830) — `k=60` flattens rank differences on small candidate pools. RRF remains opt-in pending a genuine improvement.**

**YAAMS raw-store invariant.** Raw YAAMS items are immutable and append-only: deterministic ID = `sha256(source:source_id)`, persisted with `INSERT OR IGNORE`. Never compress or rewrite the raw tier; improve retrieval and promote curated atomic notes instead.

**PRF — Pseudo-Relevance Feedback (dense path, default off).** Expands the query vector via the Rocchio formula using top-m pseudo-positive and bottom-n pseudo-negative results before re-ranking. Config: `prf_enabled` (false), `prf_top_m` (3), `prf_bottom_n` (5), `prf_alpha` (1.0), `prf_beta` (0.75), `prf_gamma` (0.15). Env: `LEDGER_PRF_ENABLED`, `LEDGER_PRF_ALPHA/BETA/GAMMA`. Per-query: `--prf` flag. **Keep `prf_enabled: false` until `ledger ab run` proves improvement.** Last run (2026-10-14, live cases, semantic_hybrid): regression, hit@1 0.739 vs 0.783, mrr 0.810 vs 0.830.

## Signals (Feedback Loop)

```bash
ledger signal add --type retrieval_hit --query "deploy" --note notes/02_facts/fact__k8s.md
ledger signal add --type correction --note notes/03_preferences/pref__x.md --detail "outdated"
ledger signal add --type rating --rating 8
ledger signal summarize      # rebuild signal_summary.json
ledger signal stats          # show signal counts, top notes, gaps (reports real_total)

# Bootstrapping the signal gate via LLM judge
ledger signal seed --from-history          # read queries from query_log.jsonl
ledger signal seed --queries-file queries.txt --backend subprocess --judge-command "claude -p"
ledger signal purge --synthetic            # roll back all seeded events
```

Signal types: `retrieval_hit`, `retrieval_miss`, `correction`, `affirmation`,
`stale_flag`, `preference_applied`, `rating`, `llm_judged`, plus the
ledger-written `supersession` and `contradiction_flagged`. Agents log the
feedback types through `ledger signal add`.

**Synthetic signals and the activation gate.** `ledger signal seed` writes events tagged `synthetic: true`. These are down-weighted by `synthetic_weight` (default 0.5) in `summarize_signals` — a seeded hit counts as half a real hit. The 20-signal gate (`signal_min_entries`) counts only real (non-synthetic) signals so seeding cannot artificially unlock ranking. `signal stats` reports `real_total` alongside the overall count. Roll back with `ledger signal purge --synthetic` then `ledger signal summarize`.

**PRF and RRF stay off until A/B validation.** `prf_enabled` and `fusion: rrf` default to off. Enable only after `ledger ab run` shows recall@k improvement without latency regression — same discipline as `score_weight_signal`.

**Scan-and-judge review (TUI):** `ledger review` walks a *prioritized* queue —
the notes most worth judging surface first (corrections pending →
high-traffic-but-never-affirmed → never reviewed → stale-by-age → low
confidence / inferred). One keystroke per note:

```
k keep (affirmation)   w wrong (correction, asks reason)   s stale (stale_flag)
1-9 rate               space/enter skip                    u undo   q quit & save
```

Signals are buffered and flushed (with a summary rebuild) on exit.

```bash
ledger review                       # full queue, interactive TUI
ledger review --type facts --stale-days 90 --unjudged-only
ledger review --queue               # print the prioritized list, no TUI
ledger review --stats               # signal dashboard: coverage, score dist, gaps
```

**Use-time capture (opt-in):** set `signals_auto_capture: true` in
`~/.config/ledger/config.yaml` (or `LEDGER_SIGNALS_AUTO_CAPTURE=1`) and queries
auto-log `retrieval_miss` when nothing scores above `signals_miss_score_floor`
(both CLI `ledger query` and the web `/search`). `ledger query "<topic>" --pick`
prompts for the result that helped and logs a `retrieval_hit`; opening a note
from a web search result does the same. Off by default to avoid noise; even when
on, signal feedback stays inert for ranking until `signal_min_entries` (20)
accrue and `score_weight_signal` is raised above 0.

**Activating signal-aware ranking (validate, don't guess):** the `--stats`
dashboard (and `ledger signal stats`, and the web `/signals` page) reports an
**activation** line — `accruing` (below threshold), `ready` (enough signals but
weight still 0, so ignored), or `active` (weight > 0). When it reads `ready`,
prove the weight helps before flipping it, using the A/B harness with
`LEDGER_WEIGHT_SIGNAL` (wired into the env overrides):

```bash
# baseline = current behavior (weight 0); candidate = signal scoring on
ledger ab run --baseline-ref HEAD --candidate-ref HEAD \
  --candidate-env LEDGER_WEIGHT_SIGNAL=0.1 \
  --cases "$(ledger paths --field ledger_notes_dir)/08_indices/retrieval_eval_cases.yaml" --k 3
```

Only raise `score_weight_signal` in `config.yaml` if recall@k improves without a
latency regression.

`<mode>`: `legacy`, `two_stage`, `scope_type_prefilter`, `precomputed_index`, `progressive_disclosure`, `semantic_hybrid`.

## A/B Testing — Mandatory Eval Gate

**Retrieval-affecting PRs** (touching `ledger/retrieval.py`, `ledger/query.py`, `ledger/scoring.py`, `ledger/signals.py`, or `ledger/config.py`) **must** run `scripts/ab_gate.sh` and paste the output into the PR body. Exit code 2 is a hard stop — do not merge.

```bash
bash scripts/ab_gate.sh   # 0 = beneficial, 3 = neutral (ok if change is off-by-default), 2 = STOP
```

The same gate runs automatically via `.github/workflows/retrieval-gate.yml`:
on PRs, on push to `main` (against the previous commit), and on manual
dispatch. CI pins lexical mode (`MODE=legacy`) because it has no embedding
model. A quality tie is exit 3 even when latency misses its band: latency is
advisory until the harness interleaves its runs (improvement plan I8).

```bash
ledger ab run --baseline-ref main --candidate-ref HEAD   # uses ledger_notes_dir from config.yaml
ledger ab run --corpus ~/Code/ledger-notes --baseline-ref main --candidate-ref HEAD
ledger ab run --baseline-ref main --candidate-ref HEAD --eval-runs 7 --query-runs 5
ledger ab run --baseline-ref main --candidate-ref HEAD --query-runs 5 --cold-query
```

Exit codes: `0` beneficial, `2` regression, `3` neutral, `4` invalid setup.
