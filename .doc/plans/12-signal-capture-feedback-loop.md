# Signal Capture & Feedback Loop

## Problem

The ledger captures durable artifacts (facts, preferences, goals) but not
*performance signals* — whether a retrieved note was actually useful, whether
a preference was applied correctly, whether a recommendation landed. Without
feedback, the system can't learn:

- Which notes are high-value vs. noise
- Whether retrieval is surfacing the right content
- Which preferences are actively used vs. stale
- What patterns of interaction lead to good outcomes

PAI captures ratings, sentiment, and success/failure signals from every
interaction and feeds them into a continuous improvement loop. We need a
lighter-weight version that fits the ledger's file-based, inspectable ethos.

## Design

### Signal types

| Signal          | When captured                           | Example                                      |
| --------------- | --------------------------------------- | -------------------------------------------- |
| **retrieval_hit** | Agent uses a retrieved note in response | `query: "deploy config" → used: fact__k8s_deploy.md` |
| **retrieval_miss** | Agent searched but found nothing useful | `query: "deploy config" → no useful results` |
| **correction**  | User corrects agent's use of a note     | `note: pref__concise_answers.md → "that's outdated"` |
| **affirmation** | User confirms agent got it right        | `note: fact__api_keys.md → "yes, exactly"`   |
| **stale_flag**  | Note referenced but content is outdated | `note: goal__learn_rust.md → completed/stale` |
| **preference_applied** | Agent successfully applies a pref | `pref: pref__no_emojis.md → applied in response` |

### Storage format

Append-only JSONL at `notes/08_indices/signals.jsonl`:

```jsonl
{"ts":"2026-04-07T14:30:00Z","type":"retrieval_hit","query":"deploy config","note":"notes/02_facts/fact__k8s_deploy.md","session":"abc123"}
{"ts":"2026-04-07T14:31:00Z","type":"correction","note":"notes/03_preferences/pref__concise_answers.md","detail":"user says outdated","session":"abc123"}
{"ts":"2026-04-07T15:00:00Z","type":"retrieval_miss","query":"oauth scopes","detail":"no relevant notes found","session":"abc123"}
```

Why JSONL:
- Append-only (no merge conflicts)
- Grep-friendly (`rg '"type":"correction"' signals.jsonl`)
- Easy to parse in Python (`json.loads` per line)
- Already precedented by `query_log.jsonl`

### Signal capture mechanics

Signals are captured by agents during normal operation. This is **not**
automatic instrumentation — it requires the agent to recognize when a signal
event occurs and append it. This is intentional: we want high-quality signals,
not noisy telemetry.

The agent captures signals by calling a new `ledger signal` CLI subcommand:

```bash
scripts/ledger signal --type retrieval_hit --query "deploy config" --note notes/02_facts/fact__k8s_deploy.md
scripts/ledger signal --type correction --note notes/03_preferences/pref__concise_answers.md --detail "user says outdated"
scripts/ledger signal --type retrieval_miss --query "oauth scopes"
```

### Feedback consumers

#### 1. Retrieval scoring (direct feedback loop)

Signals feed back into retrieval scoring:

- **retrieval_hit**: Boost note's score for similar future queries
  (lightweight: maintain a `hit_count` per note in a summary file)
- **retrieval_miss**: Flag query patterns that need better coverage
  (surface as open loops during consolidation)
- **correction**: Lower note's effective confidence until updated
- **affirmation**: Increase note's effective confidence (cap at 1.0)

Implementation: `ledger/retrieval.py` reads a precomputed signal summary
(`notes/08_indices/signal_summary.json`) during scoring. This avoids parsing
the full JSONL on every query.

```json
{
  "notes/02_facts/fact__k8s_deploy.md": {
    "hit_count": 12,
    "last_hit": "2026-04-07T14:30:00Z",
    "corrections": 1,
    "affirmations": 3,
    "signal_score": 0.85
  }
}
```

`signal_score` formula (simple, tunable):

```
signal_score = (affirmations - corrections) / (affirmations + corrections + 1)
              × min(hit_count / 10, 1.0)
```

This score is blended into the final retrieval score as a new weight:

```yaml
# config.yaml addition
score_weight_signal: 0.10  # borrow from recency or add as 8th weight
```

#### 2. Electric Sheep (consolidation feedback)

During `sheep sleep`, the consolidation engine reads signals to:

- Flag notes with >2 corrections as needing review (create open loop)
- Flag notes with 0 hits in >60 days as potentially stale
- Promote notes with high hit counts as "core knowledge"
- Surface retrieval_miss patterns as gaps → suggest new notes

#### 3. Eval case generation

Retrieval hits/misses are a natural source of eval cases:

- `retrieval_hit` with high confidence → positive eval case
  ("query X should return note Y")
- `retrieval_miss` after manual resolution → new eval case
  ("query X should return note Z, which we later created")

A periodic script (`scripts/ledger signal eval-cases`) can propose new entries
for `retrieval_eval_cases.yaml` based on signal patterns.

#### 4. Context profile enrichment

Notes with high signal scores should be prioritized in context profiles.
`build_context_profiles.py` can factor in `signal_summary.json` when
selecting which notes to include.

### Signal lifecycle

```
Agent interaction
  → signal event recognized
  → `ledger signal` appends to signals.jsonl
  → session-end hook flushes any buffered signals (plan 11)

Periodic (sheep sleep or manual):
  → `ledger signal summarize` rebuilds signal_summary.json from signals.jsonl
  → summary is consumed by retrieval scoring, context profiles, eval

Periodic (sheep sleep):
  → corrections → flag notes for review
  → misses → surface as knowledge gaps
  → stale notes → flag for archive consideration
```

## Plan

### Step 1: Signal CLI and storage

1. Create `ledger/signals.py` module:
   - `append_signal(signal_type, **kwargs)` → appends JSONL line to signals.jsonl
   - `read_signals(since=None, type_filter=None)` → yields parsed signal dicts
   - `summarize_signals()` → builds signal_summary.json from full JSONL
   - Signal type validation against allowed types enum
2. Add `signal` subcommand to `scripts/ledger`:
   - `ledger signal --type <type> [--query <q>] [--note <path>] [--detail <text>]`
   - `ledger signal summarize` → rebuild summary
   - `ledger signal stats` → print signal counts by type, top notes, coverage gaps
   - `ledger signal eval-cases` → propose eval cases from hit/miss patterns
3. Add `signals.jsonl` and `signal_summary.json` paths to `LedgerConfig`
4. Add signal paths to `.gitignore` if signals should be local-only,
   or track them if they should be portable (recommend: track them —
   they're part of the ledger's learning history)

### Step 2: Agent integration (AGENTS.md + SKILL.md)

1. Add signal capture to the operating loop in AGENTS.md:
   ```
   1. Search → 2. Respond → 3. Persist → 4. Signal → 5. Report
   ```
   Step 4 (Signal): If a retrieved note was used, log retrieval_hit.
   If user corrects agent, log correction. If user affirms, log affirmation.
2. Update SKILL.md with signal capture instructions
3. Define when agents should and shouldn't capture signals:
   - DO: after using a note in a response, after user feedback
   - DON'T: speculatively, for every search result, for trivial queries
4. Keep signal capture lightweight — one CLI call, not a multi-step process

### Step 3: Retrieval integration

1. Add `signal_summary.json` loading to `LedgerConfig` (cached, reload on mtime change)
2. Add `score_weight_signal` config parameter (default: 0.10)
3. In `retrieval.py` scoring phase, look up each candidate in signal summary
   and blend `signal_score` into final score
4. Add signal score to `ScoreComponents` dataclass in `retrieval_types.py`
5. Update A/B testing to include signal-boosted vs. non-signal-boosted comparison

### Step 4: Consolidation integration

1. In `ledger/maintenance.py`, add signal-aware consolidation steps:
   - `flag_corrected_notes()` → notes with >2 corrections → open loop
   - `flag_stale_notes()` → notes with 0 hits in 60+ days → candidate for archive
   - `surface_knowledge_gaps()` → retrieval_miss clusters → suggest new notes
2. Add these checks to `sheep sleep` flow
3. Signal summary rebuild should run as part of `sheep sleep`

### Step 5: Eval case generation

1. Add `generate_eval_cases_from_signals()` to `ledger/signals.py`:
   - retrieval_hit with affirmation → strong positive case
   - retrieval_miss followed by manual note creation → new case
2. Wire into `ledger signal eval-cases` subcommand
3. Output format matches existing `retrieval_eval_cases.yaml` schema
4. Human review required before merging into eval suite

### Step 6: Session hook integration (depends on plan 11)

1. session-end hook calls `ledger signal summarize` if signals.jsonl
   has new entries since last summary
2. session-start hook reports signal stats if notable
   (e.g., "3 corrections pending review")

## Verification

```bash
# Signal capture
scripts/ledger signal --type retrieval_hit --query "test" --note notes/02_facts/fact__test.md
rg "retrieval_hit" notes/08_indices/signals.jsonl

# Summary
scripts/ledger signal summarize
cat notes/08_indices/signal_summary.json | python -m json.tool

# Stats
scripts/ledger signal stats

# Retrieval integration
scripts/ledger query "test" --scope all   # signal_score should appear in debug output

# Eval generation
scripts/ledger signal eval-cases

# Tests
./.venv/bin/pytest tests/test_signals.py -q
./.venv/bin/pytest tests/ -q              # nothing broken
```

## Effort

~3 sessions.
- Session 1: signals.py module + CLI subcommand + storage
- Session 2: retrieval integration + config + A/B testing support
- Session 3: consolidation integration + eval generation + agent docs

## Risks

- **Signal noise**: If agents capture too many low-quality signals, the
  feedback loop amplifies noise. Mitigation: strict capture guidelines in
  AGENTS.md; only capture on clear user feedback or deliberate note usage.
- **Cold start**: Signal scores are meaningless with <20 signals. Mitigation:
  `score_weight_signal` defaults to 0.0 until signals.jsonl has sufficient
  data; config flag `signal_min_entries: 20` gates activation.
- **JSONL growth**: signals.jsonl grows unbounded. Mitigation: `sheep sleep`
  can rotate old signals (>90 days) to `signals_archive.jsonl` after
  summarization. Summary is the durable artifact; raw signals are ephemeral.
- **Circular reinforcement**: Popular notes get more hits → higher scores →
  shown more → more hits. Mitigation: signal_score is capped and weighted
  low (0.10); recency and lexical relevance still dominate.
- **Privacy**: Signals contain query text which may be sensitive. Mitigation:
  same policy as query_log.jsonl — local by default, user decides whether
  to track in git.
