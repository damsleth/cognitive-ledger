# Plan 13 — Associative PPR retrieval mode (`associative`)

**Target executor:** one Sonnet subagent in `cognitive-ledger` (entities from yaams).
**Report:** C1 (HippoRAG/HippoRAG-2 Personalized PageRank; GAAMA mega-hub warning). **Gate:** **T5**. **Needs:** Plan 01 (multi-hop cases), and Plan 04's T1 must have *not* triggered the "stop" rule.

## Precondition
Report: if T1 (Plan 04) shows no rerank gain, the corpus is too small for two-stage retrieval — **stop, don't build this.** Only proceed if T1 cleared. This is a **larger bet**, bounded and additive.

## Goal — genuinely missing capability
Multi-hop associative retrieval: query seeds Personalized PageRank over a graph of notes + entities; probability flows phrase→passage→phrase. No graph DB needed — build it in-memory from pieces that already exist.

## Status — all inputs exist, the mode doesn't
- Entities: YAAMS spaCy NER (`yaams/enrich/entities.py`) → PERSON/ORG/GPE/LOC in `item_entities`/`entities`. Note↔entity edges come from here (carried into promote frontmatter, or recomputed over note bodies).
- Passages: atomic notes.
- Note↔note edges: `[[links]]` (`ledger/parsing/links.py`, populated by Plan 11) + embedding-similarity edges above a threshold (reuse the BGE-M3 ledger index).
- Retrieval modes registry: `ledger/config.py:767-775` (add `associative`); resolver `retrieval.py:205-210`.

## Steps
1. **Build the graph at sleep time.** Nodes = notes + entities. Edges: note↔entity (NER), note↔note (existing links + embedding similarity > threshold). Build an in-memory sparse adjacency matrix (`scipy.sparse`).
2. **PPR by power iteration** (~10 lines): personalization vector seeded from query-matched nodes (entities mentioned in the query + top embedding matches); iterate `r = (1-α)·M·r + α·s` to convergence. HippoRAG-2 reports sub-second end-to-end — keep it cheap.
3. **Persist** the adjacency matrix as `.npz` in `notes/08_indices/` (alongside the other derived indices), rebuilt by `ledger embed build` / sleep. Deterministic, regenerable.
4. **New retrieval mode `associative`** that runs PPR and **falls back to `semantic_hybrid`** when the graph is cold/empty or the query seeds nothing.
5. **Mega-hub mitigation (required for a personal corpus — Kim *is* a hub).** Cap per-entity degree, or down-weight high-degree nodes in the personalization vector. The Plan-01 multi-hop set must include hub-entity queries to measure this.

## Acceptance gate — T5
Author/Reuse multi-hop cases (Plan 01 `id: hop_*`) where the answer needs linking 2–3 notes across taxonomy folders. Compare `semantic_hybrid` vs `associative` **on the multi-hop subset specifically** (HippoRAG's gains concentrate there):
```bash
ledger ab run --baseline-ref main --candidate-ref HEAD \
  --baseline-mode semantic_hybrid --candidate-mode associative \
  --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 ; echo $?
# Inspect the hop_* subset hit@k delta + the hub-entity cases for mega-hub degradation
```
**Ship rule (report hard gate):** ship `associative` only if it **beats `semantic_hybrid` on multi-hop without mega-hub collapse** (hub-entity queries don't regress). Must not regress the general cases (it falls back, so it shouldn't).

## Regression risk
Moderate. Mega-hub dilution is the real risk on a single-user corpus — explicitly measured and mitigated. Gated behind A/B; ships as an opt-in mode, fallback protects the default path.

## Done when
`associative` mode builds a persisted note+entity PPR graph at sleep, seeds from the query, mitigates hubs, falls back to `semantic_hybrid`; T5 shows a multi-hop win without hub collapse and no general regression; otherwise the mode ships disabled with the finding recorded.
