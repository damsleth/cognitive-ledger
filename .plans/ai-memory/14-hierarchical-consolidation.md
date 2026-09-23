# Plan 14 — Hierarchical consolidation (RAPTOR / MemTree synthesis tree)

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** D1 (RAPTOR recursive cluster→summarize; MemTree online incremental tree). **Gate:** **T6**. **Needs:** Plan 01, Plan 11 (link infra), and T1 not having triggered the "stop" rule.

## This is the highest-value, highest-rot bet — gate it hard
Report: "highest value, highest rot risk." Synthesis notes are **derived state that can desync** from atomic notes. The roadmap gates this near-last for a reason. Read the invariants below before writing code.

## Goal
Add *derived* abstraction layers over the *manual* 00–09 taxonomy: cluster atomic notes within a folder/tag/entity, synthesize a parent "synthesis note," and at query time return the synthesis (broad query) or atomic children (specific query) — RAPTOR's collapsed-tree mode.

## Status — primitives exist, the tree doesn't
- `synthesized` tag exists (`schema.yaml:236`); synthesized notes already must carry outgoing links (`validation.py:445-454`).
- `auto_file_synthesis` config exists (false); synthesis backend config (`synth_backend`/`synth_command`/`synth_model`) exists for `ledger answer` (plan 45). **No clustering/tree build during sleep.**
- Sleep: `ledger/maintenance.py:1279`. Embeddings index available for clustering.

## Steps
1. **Cluster at sleep.** Within a taxonomy folder (or by tag/entity), cluster atomic-note embeddings (GMM soft-clustering per RAPTOR, or a simpler agglomerative pass — ponytail: start with the simplest clustering that separates topics; don't pull in UMAP unless flat clustering measurably fails).
2. **Synthesize parent notes.** For each cluster, generate a synthesis note (reuse the `synth_backend` machinery) tagged `synthesized`, with `[[links]]` to all children. Store in a **derived layer** (a dedicated subfolder, e.g. `notes/07_synthesis/` — `07_` is currently reserved/empty per layout).
3. **MemTree incremental update over RAPTOR batch rebuild.** Kim's model is continuous-ingest; localized per-node updates (re-synthesize only clusters whose children changed) fit better than full rebuild. Use a content-hash of children to detect staleness.
4. **Collapsed-tree retrieval.** A retrieval option that can surface a synthesis node for broad queries or descend to atomic children for specific ones. Keep it **off the default path until T6 passes** — browse-only first.

## Invariants (NON-NEGOTIABLE — these are how the system avoids rot)
- Synthesis notes are **deterministically regenerable, never hand-edited.** A `// derived, do not edit` marker + lint rule.
- A synthesis note may **never** be a `supersedes` target (it's derived, not authoritative).
- Store a **content-hash of children**; sleep detects staleness and regenerates. Never let a synthesis silently desync.
- Lean on git reversibility.

## Acceptance gate — T6
Compression-ratio vs information-retention:
```bash
# After a synthesis sleep pass, can the SAME eval queries be answered using only synthesis nodes?
ledger eval --cases tests/fixtures/retrieval_eval_cases.yaml --k 3   # synthesis-only retrieval slice
```
Measure: compression ratio (synthesis tokens / child tokens) and information retention (eval queries answerable from synthesis nodes alone). Test the `score_weight_bm25 0.30` path separately — the report (arXiv 2603.13017) notes vector retrieval tolerates heavy compression while BM25 degrades.
**Ship rule (report hard gate):** if **retention < ~90%**, synthesis nodes stay **out of the default retrieval path** (browse-only). Only promote them into default retrieval above that bar.

## Regression risk
**High for code rot** — the entire invariants section exists to contain it. No synthesis node enters default retrieval until T6 clears 90% retention.

## Done when
Sleep builds a regenerable synthesis layer (clustered, child-linked, staleness-hashed, never-hand-edited, never a supersedes target); collapsed-tree retrieval available but browse-only unless T6 retention ≥90%; compression/retention numbers recorded.

## Status — 2026-09-23: clustering slice measured; synthesis blocked on an LLM backend

**Blocked:** `synth_backend` is `dummy` in the live config — no model to write synthesis notes. Configuring one (e.g. the claude CLI adapter) means editing `~/.config/ledger/config.yaml` and spending ~60 model calls; that is the owner's decision, not a default to flip.

**Measured (no LLM):** average-linkage agglomerative clustering, cosine distance, over the 554 live bge-m3 vectors (archive excluded):

| distance | clusters | singletons | clusters ≥3 | notes covered by ≥3 |
|---|---|---|---|---|
| 0.35 | 331 | 205 | 48 | 193 |
| **0.45** | 170 | 76 | **58** | **406** |
| 0.55 | 38 | 10 | 22 | 532 (top two: 132, 114 — too coarse) |

At 0.45 the clusters are coherent topics, not one mega-cluster: NOCOS Azure operations (41), YAAMS autoresearch (27 — seven dated `yaams_autoresearch_junXX` run notes, exactly what a synthesis node should roll up), NOCOS 3.0 (24), owa tooling (20). So T6 is not dead on structure; flat clustering is enough, no UMAP/GMM.

**Next when unblocked:** synthesise the 58 clusters at 0.45 into `07_synthesis/` behind the invariants above, then T6 (retention on the live cases, synthesis-only slice). Start with the autoresearch cluster — its children are near-duplicates by date, the cleanest retention test.
