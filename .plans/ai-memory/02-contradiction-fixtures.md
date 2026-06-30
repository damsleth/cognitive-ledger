# Plan 02 — Contradiction + implicit-conflict fixtures (T2/T3 data)

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** A2/A3, builds T2 + T3 datasets. **Gate:** none — fixture authoring. **Blocks:** Plans 08, 09.

## Why
Plans 08 (turn on contradiction detection) and 09 (attribute-slot model) cannot be accepted without a labeled contradiction set to measure NLI precision/recall against. The report's hard gate: contradiction auto-resolution ships only if **precision > 0.95 on `lang:en`**; if precision stays <0.9 even for English, contradiction stays advisory-only forever. That decision needs this data.

## Deliverables (two labeled sets, fixture corpus only — never Kim's live notes)

### T2 — explicit supersession pairs (`tests/fixtures/contradiction_t2.yaml`)
- **30–50 positive pairs:** same attribute, conflicting value, lexically similar enough that NLI should fire. Cover location, employer, dietary preference, project status. Each pair: `{note_a, note_b, relation: contradiction, attribute: residence|employer|..., lang: en|no}`.
- **50 negative pairs:** near-duplicate or merely-related notes that are *not* contradictions (same topic, compatible facts). These measure false-positive rate — the thing that makes auto-resolution dangerous.
- **Tag each pair `lang: en` or `lang: no`.** Include a `lang:no` block specifically expected to *fail* NLI (mDeBERTa-mnli-xnli has no Norwegian data) — that failure is a finding Plan 08 must respect, not a bug to fix.

### T3 — implicit conflicts (`tests/fixtures/contradiction_t3.yaml`)
STALE-style, where pure NLI is expected to underperform (~55% ceiling) and the attribute-slot key (Plan 09) is what rescues it:
- **Type I (co-referential):** "lives in Seattle" → "signed a lease in Portland" — no lexical overlap, same `attribute: residence` slot.
- **Type II (propagated):** "leg injury" → should invalidate "bikes to work" — conflict only via a dependency, not direct.
- Label each with the `attribute` slot key that *should* let it collide, so Plan 09's eval can measure "NLI alone" vs "NLI + attribute-slot keying."

## Format
Reuse the eval-case style so the same loader/tooling applies where possible; otherwise a flat YAML list the contradiction eval harness (Plan 08) reads. Each entry self-describes its expected verdict and language. Keep entries short and synthetic — no private content.

## Acceptance gate
No retrieval metric here (these aren't retrieval cases). Instead:
```bash
.venv/bin/python -m pytest tests/ -k "contradiction_fixtures"   # a loader/shape test you add
```
Add a tiny test that loads both YAMLs, asserts every entry has the required keys (`note_a`, `note_b`, `relation`, `attribute`, `lang`), counts ≥30 positives / ≥50 negatives for T2, and ≥10 Type-I + ≥10 Type-II for T3. **This shape test is the only logic here — ponytail: one assert-based loader test, no framework.**

## Done when
Both YAMLs exist with the counts above, the loader test passes, and the `lang:no` expected-fail subset is clearly marked.
