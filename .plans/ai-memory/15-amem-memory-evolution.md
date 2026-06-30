# Plan 15 — A-MEM memory evolution (note mutation, proposal-only)

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** D2 (A-MEM "memory evolution"; Belief-Memory drift warning, arXiv 2605.05583). **Gate:** the eval harness must be able to catch drift; **proposal-only** until then. **Needs:** Plan 11 (links), Plan 01, ideally Plan 14's staleness machinery.

## This is the entropy risk — it is gated LAST on purpose
The report and the TODO invariants both name note **mutation** as the single biggest rot risk ("self-reinforcing error / drift"). This plan is the *mutation* half of A-MEM (the *link* half is Plan 11). It does not auto-mutate anything. If you are tempted to make it auto-apply, stop — re-read the invariants.

## Goal
When a new note arrives related to an existing note, A-MEM re-summarizes the existing note's contextual description to reflect the new neighbor ("memory evolution"). Powerful for keeping context current; dangerous because it mutates curated state on every related write.

## Status
- Link infra from Plan 11 gives the "related notes" signal.
- `ledger answer` synth backend can draft the re-summarized Context section.
- Write modes: `ask-to-write` (review) vs auto. Git history is the reversibility guarantee.

## Steps
1. **Detect evolution candidates.** During sleep, when Plan 11 proposes a new link A→B, also check whether B's "Context"/description section is now stale relative to A (embedding drift or an explicit Context section that predates A).
2. **Generate a proposed re-summary** of B's Context section (synth backend), as a **diff**.
3. **Proposal-only — never auto-apply.** Surface the diff as an open loop / review item (`ask-to-write` posture). The human accepts/rejects. **No silent mutation, ever**, until the eval harness can demonstrably catch a quality regression introduced by an evolution edit.
4. Add a guard: an evolution edit must be a no-op on a note's `statement`/frontmatter — it may only touch a clearly-delimited "Context"/notes section, never the atomic claim itself.

## Invariants (NON-NEGOTIABLE)
- **Proposal-only** until the eval harness proves it can detect drift regressions. This is the explicit gate from the report and the TODO.
- Never mutate the atomic `statement` or required frontmatter — Context section only.
- Every applied edit is a git commit (reversible).
- If an evolution edit would touch a `synthesized` (derived) note → skip; those regenerate via Plan 14, not evolution.

## Acceptance gate
There is no "ship it on" gate yet — the deliverable is the **proposal pipeline + a drift-detection eval**:
```bash
# Construct a fixture where an evolution edit is known to degrade retrieval, confirm the harness catches it
ledger ab run --baseline-ref main --candidate-ref HEAD --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 ; echo $?
.venv/bin/python -m pytest tests/ -k "evolution"   # proposes a diff, never auto-applies, respects statement/frontmatter guard
```
Auto-apply remains disabled regardless of results. A future plan may enable auto-apply *only after* a drift-detection eval reliably flags bad edits.

## Regression risk
**Highest in the roadmap.** Contained by: proposal-only, Context-section-only, git reversibility, derived-note skip. Do not relax any of these.

## Done when
Evolution *proposals* are generated as reviewable diffs during sleep, never auto-applied, scoped to Context sections only, with a drift-detection eval fixture demonstrating the harness can catch a bad edit. Auto-apply stays off.
