# WikiSkill concepts in the Cognitive Ledger

*WikiSkill: Compiling Agent Experience into Persistent Knowledge for Skill
Evolution* ([arXiv:2608.27454](https://arxiv.org/abs/2608.27454)) separates an
agent's improvement loop into three co-evolving layers — immutable **raw**
execution traces, a persistent **wiki** of distilled knowledge, and the active
**skills** the agent runs with — plus a validation-gated evolutionary loop.
Its two key findings: a wiki that persists across skill rollbacks is what makes
knowledge compound (removing it cost 15 points in their ablation), and a
proposer informed by that wiki stops re-trying modifications that already
failed.

The ledger already had two of the three layers and the gate. This document
maps the paper's concepts onto the ledger, in the two places they apply.

## Mapping

| WikiSkill concept | Ledger — retrieval-config evolution (`ledger ab loop`) | Ledger — memory corpus (`ledger signal …`) |
|---|---|---|
| Raw layer (execution traces τ) | `ab_loop_results.jsonl` (one line per trial, accepted or not) | `08_indices/signals.jsonl`, `timeline.jsonl` (append-only) |
| Wiki layer (pattern directory) | `<out_dir>/wiki/patterns.json` — per-param impact tracker + verdicts | `08_indices/patterns.{json,md}` — failure modes + strategies |
| Wiki layer (evolution log) | `<out_dir>/wiki/evolution.md` (append-only, never rolled back) | `timeline.md` (pre-existing) |
| Skill layer (skill set S) | `champion.json` — the active retrieval config overrides | the notes themselves + the `/notes` agent skill |
| Wiki maintainer | `Wiki.observe()` folds every trial into the impact tracker | `ledger signal patterns` distills the signal log |
| Wiki-informed proposer | `next_proposal(…, wiki)` ranks params by impact record | pattern entries carry a `suggested_action` for the agent/review flow |
| Validation gating (score ℛ) | screen + holdout probes (pre-existing) | `ledger eval` / `ledger ab run` (pre-existing) |
| Rollback spares the wiki | rejected candidates still update `wiki/`; only the champion reverts | pattern directory is derived, never reverted with note edits |

## 1. Persistent wiki for the A/B loop (`ledger/ab_wiki.py`)

`ledger ab loop` already implemented WikiSkill's gate: propose one mutation,
screen-probe, holdout-probe, accept or discard. What it lacked was the wiki —
proposals came from blind coordinate descent that happily re-ground through a
parameter whose every value had already regressed.

The wiki adds, under `<out_dir>/wiki/`:

- **Impact tracker** (`patterns.json`): per-param counts of tries, accepts,
  regressions, overfits, and screen-objective deltas — rebuilt from the raw
  trial log at startup (the raw layer stays the source of truth), updated
  after every trial including rejected ones.
- **Verdicts**: each param is classified `prioritize` (has an accepted
  mutation), `explore` (untried, or mixed evidence such as an overfit that won
  its screen), `struggling` (only failures, too few to condemn), or `avoid`
  (≥3 tried values, zero accepts, no screen improvement at all).
- **Wiki-informed proposer**: `next_proposal` sweeps params in verdict order —
  prioritize (best mean delta first) → explore → struggling → avoid. Avoided
  params rank last rather than being dropped, so under a `--max-trials` budget
  they are effectively skipped while the space stays exhaustively searchable.
  With an empty wiki the order degrades to plain space order, so a fresh run
  behaves exactly as before.
- **Evolution log** (`evolution.md`): one append-only line per live trial,
  plus a note whenever a param's verdict changes. Never truncated, never
  rolled back.

Disable with `--no-wiki` to get the old blind coordinate descent.

## 2. Wiki maintainer over the signal log (`ledger signal patterns`)

The signal log is the ledger's raw experience layer for the memory corpus
itself: hits, misses, corrections, affirmations, stale flags, contradictions.
`ledger signal patterns` plays the paper's wiki-maintainer role: it analyses
that log and writes a pattern directory to `08_indices/patterns.json` (machine)
and `patterns.md` (human), containing:

- **Failure modes** — `repeated_retrieval_miss` (a query the corpus keeps
  failing to answer), `correction_prone_note`, `stale_note`,
  `contradicted_note`.
- **Strategies** — `high_value_note` (frequently retrieved, positively
  signalled).

Each pattern carries its evidence counts, first/last-seen timestamps, and a
`suggested_action` (create a note for the missed query, supersede the
correction-prone note, …). Thresholds use the weighted counts from
`summarize_signals`, so synthetic (LLM-seeded) signals are down-weighted and
unlikely to cross a threshold on their own.

```bash
ledger signal patterns                     # write patterns.json + patterns.md
ledger signal patterns --json              # print the directory as JSON
ledger signal patterns --min-misses 5      # tune thresholds
```

The command never mutates notes: like the paper's wiki maintainer, it names
the pattern and hands the acting agent (or `ledger review`) a concrete next
action. The directory is derived from `signals.jsonl`, so regenerating it is
always safe.

## What was deliberately not adopted

- **Skill files (SKILL.md/PURPOSE.md) inside this repo** — the executable
  `/notes` skill lives in the separate [SKILLS](https://github.com/damsleth/SKILLS)
  repository; evolving its text is out of scope here.
- **Restricting the inference agent from wiki access** — the paper keeps the
  wiki away from the executing agent to isolate skill quality. The ledger's
  equivalent separation already exists: retrieval scoring reads
  `signal_summary.json`, not the pattern directory, so patterns inform
  curation without confounding ranking.
