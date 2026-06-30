# Plan 09 — Attribute-slot state model (`attribute` frontmatter field)

**Target executor:** one Sonnet subagent in `cognitive-ledger` (NER assist from yaams).
**Report:** A3 (STALE/CUPMem current-state management; "Is Agent Memory a Database?"). **Gate:** **T3**. **Needs:** Plan 02 (T3 fixtures), Plan 06 (bitemporal), Plan 08 (contradiction surface).

## Why this is the keystone
A3 connects bitemporal (06) + contradiction (08) into *actionable* current-state management. Today contradiction detection is lexical/semantic; it misses STALE Type-I/II implicit conflicts ("lives in Seattle" vs "new lease in Portland" — no lexical overlap). An explicit `attribute` slot key makes them collide on `residence` even without overlap. The report: this is "the bridge that makes your bitemporal fields and contradiction detection actionable rather than advisory."

## Status — no `attribute` field exists yet
`schema.yaml` (252 lines) has `valid_from`/`valid_to`/`superseded_by`/`supersedes`/`provenance`/`confidence`/`lang`/`source`/`via` — **no `attribute_key`** (confirmed). Schema additions are backward-compatible (the bitemporal rollout proved legacy notes lint clean).

## Steps

### (1) Schema + validation
- Add optional `attribute` frontmatter field to `schema.yaml` (e.g. `attribute: residence`, `attribute: employer`). Free-text slot key, lowercase, single-token. Most relevant on `02_facts` and `03_preferences`.
- Add a validation rule in `ledger/validation.py` (lint clean when absent; if present, must be a non-empty slug).
- Register nothing in config — this is data, not a knob.

### (2) Populate from YAAMS NER (assist, not required)
YAAMS NER (`yaams/enrich/entities.py`) already extracts PERSON/ORG/GPE/LOC into `item_entities`. During promote, when a candidate's entities + type strongly imply a slot (GPE/LOC on a fact → `residence`; ORG on an employment fact → `employer`), suggest `attribute:` in the promote frontmatter. Keep it a *suggestion* the human review can edit — never auto-assert.

### (3) Sleep adjudication
In `ledger/contradiction.py` (the scanner from Plan 08), add an attribute-slot pass: notes sharing the same `attribute` key are candidate-adjudicated by valid-time (06) + confidence + provenance (12). The stale one becomes a supersession candidate → open loop (reuse Plan 08's surface). This catches STALE Type-I/II that pure NLI misses, **without** lexical overlap.
- Reuse existing bitemporal `supersede()` primitive; do not invent new state.
- Identity notes still never auto-superseded.

## Acceptance gate — T3
Measure detection rate of pure NLI vs NLI + attribute-slot keying on the Plan-02 T3 fixtures (Type-I co-referential, Type-II propagated):
```bash
.venv/bin/python -m ledger.contradiction --eval tests/fixtures/contradiction_t3.yaml --compare nli,nli+slot
```
**Expectation (report):** NLI-alone near the STALE ceiling (~55%); NLI+slot meaningfully higher. The delta quantifies A3's value. No retrieval regression:
```bash
ledger ab run --baseline-ref main --candidate-ref HEAD --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 ; echo $?
```

## Regression risk
Moderate. Schema addition is optional/backward-compatible (low). The adjudication logic mutates state (supersession) — keep it routed through Plan 08's open-loop surface (proposal, not silent), and `lang:no` advisory-only.

## Done when
`attribute` field added + lint rule; promote suggests slots from NER (editable); sleep adjudicates same-slot notes into open-loop supersession candidates; T3 shows NLI+slot > NLI-alone; no retrieval regression.
