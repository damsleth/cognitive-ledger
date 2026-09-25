# cognitive-ledger TODO / Plan

Semantic tier + the Electric Sheep consolidation bridge. Source of truth for all planning below: **"Porting AI-Memory Research into YAAMS + cognitive-ledger"** (`.plans/ai-memory/AI-Memory-Research.md`). Per-item plans and status: `.plans/ai-memory/00-INDEX.md`.

**State 2026-09-25:** Stages 0-2 are done except the reinforcement pair, which is parked on data. What is left: plan 14 (needs an owner decision on `synth_backend`), and the parked 10/16. Plan 15 is killed. Each item cites the report section it draws from. Don't start an item without re-reading its cited section.

The eval harness (`ledger eval` / `ledger_ab`, exit 0/2/3) lives here, so this repo owns the gate for the whole system. Bridge items that depend on YAAMS are marked **[bridge]**.

---

## Stage 0: eval substrate (blocking, do this first)

Nothing in Stage 1-3 ships without passing `ledger_ab`. This is the single most important anti-entropy investment: it makes every later change reversible and measurable.

- [x] **Expand `retrieval_eval_cases.yaml`** with multi-hop, temporal, and negative cases. Shipped in `a481e5c`. Source: report Stage 0 + T5.
- [x] **Build T2 contradiction fixtures**: 30-50 synthetic supersession pairs (location/job/preference changes) + 50 non-conflicting near-duplicate negatives. Shipped in `a481e5c`. Source: report T2.
- [x] **Build T3 implicit-conflict fixtures**: Type I co-referential and Type II propagated pairs. Shipped in `a481e5c`. Source: report T3 + STALE (arXiv 2605.06527).
- [x] **Make the `ledger_ab` exit-code gate (0/2/3) mandatory** in the workflow for every item below. Shipped in `a481e5c`. Source: report Stage 0.

---

## Stage 1: quick wins

- [x] **Validate and tune the existing `semantic_rerank` mode.** Shipped in `395c74d` after T1 improved hit@1 by 0.0256; `LEDGER_RERANK_INPUT_K` and the tuning space landed in `a02920e`. Source: report B1.
- [x] **Run T1**: `semantic_hybrid` vs `semantic_rerank`, including candidate-pool tuning. Gate passed (exit 0) in `395c74d`. Source: report B1 + T1.
- [x] **Document the weighted_sum > RRF decision in-repo.** Recorded in `AGENTS.md`, `config.sample.yaml`, and `LedgerConfig.fusion`; raw YAAMS append-only storage is also explicit. Source: report B2.
- [x] **Consume event-time into the bitemporal fields.** (a)+(c) shipped earlier; (b) auto-as-of shipped `26b2b61` 2026-09-23. Populate `valid_from` from the YAAMS source timestamp **[bridge]** (see YAAMS emit task), make `--as-of` the default retrieval lens when query parsing detects a temporal scope, and ensure notes with `valid_to < now` down-rank but never delete. Source: report A1.
- [x] **Validate the `prior` tie-breaker** — 2026-09-23: neutral on live + fixture, defaults kept (verdict in `.plans/done/ai-memory/07-*.md`). via the `prior_enabled: false` ablation. Confirm recency+importance+relevance is doing useful work and that `prior_tie_band 0.02` earns its place. Source: report B3a + T4.

---

## Stage 2: medium bets (validate-then-enable)

- [x] **Run T2 and gate contradiction auto-resolution on precision > 0.95** — 2026-09-23: en 0.862, no 1.0 (n=14). Advisory-only., `lang:en` only. Report precision/recall separately for `en` and `no` (expect `no` to fail, that's the finding). Source: report A2 + T2.
- [x] **Enable contradiction detection as supersession-candidate -> open-loop** — shipped as inbox conflict note (not loop: loops sync to Things); auto-supersede opt-in, off. ("needs decision"), never auto-archive. Matches the existing "surface conflicts as explicit open loops" rule and avoids silent one-sided resolution. Keep `lang:no` off and `contradiction_protect_higher_confidence: true`. Source: report A2.
- [x] **Add optional `attribute_key` frontmatter field.** — shipped as `attribute` 2026-09-23; slot pass in the contradiction scan. During sleep, notes sharing a key get adjudicated by valid-time + confidence + provenance; the stale one gets `superseded_by`. This is the keystone that makes bitemporal + contradiction actionable (current-state management). Backward-compatible: legacy notes lint clean. Source: report A3 + STALE/CUPMem.
- [x] **Run T3** — NLI alone 0.769 recall (co-ref 0.923, propagated 0.615); +slot is tautological on this fixture. to quantify pure NLI vs NLI + attribute-slot keying on implicit conflicts. Expect NLI-alone near the ~55% STALE ceiling. Source: report T3.
- [ ] **Bridge signal scoring to Ebbinghaus reinforcement.** — PARKED 2026-09-25: 1 retrieval_hit / 0 preference_applied live; recall runs through yaams MCP, which has cited a ledger note only 3 times ever. Revisit when that passes ~25 (see plan 10). Treat `retrieval_hit` / `preference_applied` signals as R = e^(-t/S) events (S incremented on recall, dt reset) that bump a per-note `strength`, replacing the hard 20-signal binary gate with a continuous ramp. Cap the boost (reuse `validation_boost_cap: 0.15`); keep synthetic signals down-weighted. Source: report B3b + MemoryBank (arXiv 2305.10250).
- [ ] **Run T4 cold-start ramp** — blocked with the item above.: signals-off (today) vs hard-on-at-20 vs continuous reinforcement, MRR trajectory as signal count grows 0 -> 50. Continuous ramp should dominate in the 10-25 signal regime. Source: report T4.
- [x] **Add A-MEM link generation in sleep (links only).** — `ledger sleep links`, 2026-09-23; neutral on live eval. For each newly promoted note, embed it, find top-k similar existing notes, propose `[[links]]`. No note mutation yet. Source: report D2 (links).
- [x] **Set up provenance-weighted confidence as an A/B candidate.** Shipped off by default after a quality-tied A/B in v0.9.0 (`05dda15`, merged by `1575e45`). Source: report E2.

---

## Stage 3: larger bets (only behind proven eval gates)

- [x] **Associative PPR retrieval mode.** — built + KILLED 2026-09-23 (T5: live hit@1 0.783→0.652, mrr 0.836→0.747). Verdict in `.plans/done/ai-memory/13-*.md`. Build a sparse adjacency matrix at sleep time (nodes = notes + entities from YAAMS NER **[bridge]**; edges = note<->entity, note<->note via existing `[[links]]` + embedding-sim above threshold), run Personalized PageRank by power iteration seeded from query-matched nodes, persist as `.npz` in `08_indices/`. Ships as mode `associative`, falls back to `semantic_hybrid`. Cap per-entity degree to fight mega-hubs. Source: report C1 + HippoRAG (arXiv 2405.14831 / 2502.14802).
- [x] **Run T5** — see above; hop_* fixture cases were already at ceiling.: `semantic_hybrid` vs `associative` on a multi-hop subset, including high-degree-entity queries to measure mega-hub degradation. Ship only if it beats baseline without hub collapse. Source: report C1 + T5.
- [ ] **Hierarchical consolidation (RAPTOR / MemTree).** — clustering measured 2026-09-23 (58 coherent clusters at 0.45); synthesis waits on an owner decision to set `synth_backend` (~60 model calls). See plan 14. In sleep, cluster atomic notes within a taxonomy folder/tag and synthesize a parent note with pointers to children (reuse `synthesized` tag + `auto_file_synthesis`). At query time return synthesis (broad query) or atomic children (specific). Must be deterministically regenerable (never hand-edited), store a child content-hash for staleness detection, and never be a `supersedes` target. Prefer MemTree's online incremental update over RAPTOR's batch rebuild. Source: report D1.
- [ ] **Run T6 retention gate**: compression ratio (synthesis tokens / child tokens) + can the eval queries still be answered from synthesis-only? Keep synthesis browse-only if retention < ~90%. Test BM25 path retention separately (vector tolerates compression, BM25 degrades). Source: report T6.
- [x] **A-MEM memory evolution (note mutation).** — KILLED 2026-09-25: highest drift risk, and the link infra it builds on (plan 11) was neutral. Archived in `.plans/done/ai-memory/15-*.md`. Re-summarize an existing note's Context section when a related note arrives, but proposal-only (diff for review), gated behind `ask-to-write`, leaning on git reversibility. This is the highest drift risk in the system; keep it last. Source: report D2 (evolution).
- [ ] **U-Mem Thompson-sampling exploration in retrieval.** — PARKED with plan 10. Occasionally surface lower-similarity notes to gather signal and avoid the rich-get-richer trap. Only after reinforcement (Stage 2) is stable. Source: report B3 + U-Mem (arXiv 2602.22406).

---

## Decision thresholds (these change the plan)

- If T1 shows no rerank gain, the corpus is too small for two-stage retrieval. Stop and revisit at larger scale.
- If T2 contradiction precision stays < 0.9 even for `lang:en`, keep contradiction advisory-only indefinitely (open loops, never auto-resolve).
- If T6 retention drops below ~90%, synthesis nodes stay out of the default retrieval path (browse-only).

## Invariants to protect (do not regress)

- Non-lossy invalidation: close the validity window or move to `09_archive`, never delete. Aligns bitemporal with the existing golden rule (report A1).
- Contradiction handling stays advisory for `lang:no` until NLI is validated/swapped; mDeBERTa-mnli-xnli has no Norwegian data (report A2 + caveats).
- Evolution and synthesis are derived/mutating state and are the entropy risk. They're gated last on purpose, behind the eval harness and git reversibility (report D1, D2, caveats).

## Notes

- The "known gaps" framing was partly stale: bitemporal (v0.6.0) and contradiction code already exist but are off-by-default and unvalidated. Several Stage 1-2 items are "validate and turn on what you built," not greenfield. Re-confirm exact code state before implementing; the report read schema/config/CHANGELOG verbatim but the `.py` retrieval/scoring source was robots-blocked.
- Verify the HippoRAG-2 ablation splits against the paper's ablation table before quoting them anywhere.
- 2026 papers cited (A-MAC, U-Mem, STALE, the bottleneck diagnostic, "Is Agent Memory a Database?") are preprints. Treat headline numbers as directional; your own held-out eval set is the authority.
