# Plan 11 — A-MEM link generation during sleep (links only, no mutation)

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** D2 (A-MEM link generation; the *evolution*/mutation half is Plan 15, gated last). **Gate:** no-regression A/B + a links unit test. **Needs:** sleep pipeline + embeddings (both exist).

## Scope discipline (ponytail)
This is the **safe half** of A-MEM. Link *generation* only: propose `[[links]]` between related notes. **No note mutation** — re-summarizing existing notes on related writes is Plan 15 and is deliberately gated last (it's the drift/self-reinforcing-error risk). Do not blur the two.

## Status — pieces exist, the sleep step doesn't
- Links: `ledger/parsing/links.py` `extract_links()` parses `[[target|display]]`, `NoteLink` dataclass. **No dedicated link index** — links live inline in bodies, extracted at runtime.
- Embeddings: `ledger embed build` builds the semantic index (`semantic_manifest.json`, vectors in `.smart-env/semantic/ledger/{model}/`); BGE-M3 already the user's configured model.
- Sleep: `ledger/maintenance.py:1279` `cmd_sleep` — the place to add a step.
- Synthesized-note validation already requires outgoing links (`validation.py:445-454`), so the codebase already treats links as first-class.

## Steps
1. **Add a sleep step "propose links."** For each note promoted/changed since last sleep (reuse the dirty-path / `query --changed-since` detection that already exists), embed it (reuse the loaded index — don't reload the model), find top-k most-similar existing notes (BGE-M3 over the ledger index), filter by a similarity floor.
2. **Propose, don't auto-write.** Respect the existing write-mode posture: in `ask-to-write` mode, surface proposed `[[links]]` as a diff/open-loop for review; only auto-insert in explicit auto-write mode. Bidirectional: propose the backlink too. Skip links that already exist (idempotent).
3. **No new index needed** (ponytail). Links stay inline `[[...]]` in bodies — that's the existing model; don't build a links.jsonl. If link traversal later needs an index, that's a separate plan with its own justification.
4. Cap proposals per note (e.g. top-5 above floor) to avoid link spam on hub notes.

## Acceptance gate
Link generation shouldn't regress retrieval and should improve multi-hop (more edges → better associative reach, feeds Plan 13):
```bash
ledger ab run --baseline-ref main --candidate-ref HEAD --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 ; echo $?  # 0/3
.venv/bin/python -m pytest tests/ -k "link"   # unit test: proposes correct links on a fixture, idempotent, respects floor + cap
```
Unit test on a fixture corpus: two semantically-related notes with no link → proposal generated; run twice → no duplicate proposal (idempotent); unrelated notes → no proposal.

## Regression risk
Low (link generation is additive, proposal-gated). The dangerous sibling — evolution/mutation — is explicitly **not** in this plan.

## Done when
Sleep proposes `[[links]]` for changed notes via embedding similarity (top-k, floored, capped, bidirectional, idempotent), proposals respect write-mode (review in ask-to-write), no new index, no retrieval regression, link unit test passes.
