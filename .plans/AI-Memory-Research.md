# Porting AI-Memory Research into YAAMS + cognitive-ledger: Optimizations, Features, A/B Tests, and a Prioritized Roadmap

## TL;DR
- The two "known gaps" you flagged are **already half-closed in code**: cognitive-ledger ships a bitemporal axis (`valid_from`/`valid_to`/`supersedes`/`superseded_by`, `ledger query --as-of`, `ledger/bitemporal.py`, v0.6.0) and an NLI contradiction scanner (`contradiction_enabled`, mDeBERTa-v3-base-mnli-xnli), both **off by default and unvalidated**; and cold-start ranking is already handled by a Generative-Agents-style `prior` tie-breaker while signal scoring sits inert behind `score_weight_signal: 0.0` plus a 20-real-signal gate. The highest-leverage near-term work is **validating, tuning, and turning on what you already built**, not green-field construction.
- The genuinely *missing* research-grade capabilities are **associative retrieval** (HippoRAG-2 Personalized PageRank / query-to-triple), **recognition-memory reranking** (an LLM/cross-encoder filter over the candidate set), **hierarchical consolidation** (RAPTOR/MemTree summary trees in the semantic tier), **A-MEM-style link evolution** between atomic notes, and **memory admission control** on the YAAMS firehose (A-MAC) — each portable onto your existing SQLite/BGE-M3/FTS5/spaCy/markdown stack with no new storage engine.
- Sequence to protect against entropy: (1) build the eval substrate and turn on what exists behind A/B gates; (2) cheap bolt-ons (recognition rerank, admission control, Ebbinghaus reinforcement); (3) larger bets (associative graph layer, hierarchical consolidation) only after the eval harness can prove they don't regress retrieval.

---

## Key Findings

1. **You are further along than the "gaps" framing suggests.** cognitive-ledger already implements bitemporal validity and contradiction detection (verified against `schema.yaml`, `config.sample.yaml`, and CHANGELOG; v0.6.0 added the four bitemporal frontmatter fields + `supersede()` primitive). The work is hardening and validation, not invention. The real risk is shipping unvalidated NLI/contradiction logic that silently corrupts the curated tier.

2. **Retrieval is the dominant bottleneck, not storage or compression.** "Diagnosing Retrieval vs. Utilization Bottlenecks in LLM Agent Memory" (arXiv 2603.02473) finds retrieval is the dominant performance stage and that raw chunk-based storage remains highly effective. (Headline framing is from a Feb–Mar 2026 preprint — treat as directional.) This argues for investing in **reranking and associative retrieval** over more elaborate consolidation/compression — and validates YAAMS's append-only raw-item design.

3. **Zep/Graphiti uses the same embedding/reranking model you do (BGE-M3)** and, per the Zep paper (Rasmussen et al., arXiv 2501.13956, Jan 2025), "hybrid search combining semantic embeddings, BM25 keyword search, and graph traversal achieves P95 retrieval latency of 300ms with no LLM calls during retrieval." Zep beats MemGPT on Deep Memory Retrieval (94.8% vs 93.4%) and reports up to 18.5% accuracy gains plus ~90% latency reduction on LongMemEval. Its bitemporal design (event time vs ingestion time) and **non-lossy "close the validity window, don't delete"** invalidation — "maintaining a timeline of facts and relationships, including their periods of validity" — is the reference implementation for what cognitive-ledger's bitemporal fields should do.

4. **Generative Agents' retrieval score is already in your codebase** as the `prior` tie-breaker. Park et al. (arXiv 2304.03442) combine recency, importance, and relevance "with equal weights," each "normalize[d] to the range of [0,1] using min-max scaling"; recency is "an exponential decay function… [with] decay factor… 0.995," importance is an LLM-rated 1–10 score, relevance is query-memory cosine similarity. Your config mirrors this (`prior_w_importance 0.30`, `prior_w_recency 0.30`, `prior_w_relevance 0.40`, half-life 180d). This is the correct cold-start substitute for disabled signal scoring; it needs validation that `prior_tie_band 0.02` is doing useful work.

5. **The consolidation bridge (Electric Sheep) is where the most novel, defensible value lives** and where the literature is richest: RAPTOR hierarchical summarization, A-MEM link/evolution, MemoryBank Ebbinghaus reinforcement, and Mem0 extract→ADD/UPDATE/DELETE/NOOP all map cleanly onto a background "sleep" process that promotes YAAMS episodic items into cognitive-ledger atomic notes.

---

## Details — Grouped by Theme

### Theme A — Temporal validity & contradiction (your "gap 1 & 2", actually already partially built)

**A1. Bitemporal validity — finish wiring it to the consolidation/retrieval path.**
- *Source:* Zep/Graphiti (arXiv 2501.13956) — every fact carries event time (when true in the world) and ingestion time (when observed); on change, the old fact is invalidated in a non-lossy manner so history stays queryable. Mem0g (arXiv 2504.19413) marks conflicting edges invalid rather than deleting.
- *Tier:* cognitive-ledger (semantic), with event-time provenance sourced from YAAMS item timestamps.
- *Implementation path:* You already have `valid_from`/`valid_to`/`supersedes`/`superseded_by` and `supersede()` in `ledger/bitemporal.py`. Missing pieces: (a) have Electric Sheep **populate event-time from the originating YAAMS item's source timestamp** (email date, message ts, calendar event time) rather than note-creation time — this is the event-time vs ingestion-time distinction; (b) make `--as-of` retrieval the default lens when query parsing detects a temporal scope; (c) ensure retrieval down-ranks (never deletes) notes where `valid_to < now`.
- *Effort:* Near-term — fields and primitive exist; this is plumbing + a parse rule.
- *Regression risk:* Low-to-moderate. Risk is event-time inference errors from YAAMS (e.g., a forwarded email's date). Mitigate by storing event-time provenance and defaulting to ingestion-time when confidence is low. The "append before overwrite / move to 09_archive" golden rule already aligns with non-lossy invalidation.

**A2. Contradiction detection — validate before enabling, and adopt the "supersession candidate" framing.**
- *Source:* "Contradiction Detection in RAG Systems" (arXiv 2504.00180) — NLI/FC/LLM validators have high precision but weak recall on smaller models; lexically-similar conflicts are hardest. STALE (arXiv 2605.06527) — "implicit conflict" (Type I co-referential, Type II propagated) is the dominant real-world failure mode, and the best evaluated model reaches only 55.2% overall accuracy; CUPMem resolves it via write-time consolidation + propagation-aware search. ECon (arXiv 2410.04068) — LLMs resolving conflicts tend to favor one side without justification.
- *Tier:* cognitive-ledger (NLI scan in sleep), bridge (write-time check during promote).
- *Implementation path:* You have `contradiction_enabled` (false), `contradiction_model: MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`, `contradiction_neighbors_k: 8`, auto/review thresholds (0.85/0.60), and `contradiction_protect_higher_confidence: true`. Before enabling: (a) build a labeled contradiction eval set from your own corpus (T2/T3 below); (b) given STALE, **treat NLI "contradiction" as a *supersession candidate* surfaced as an open loop ("needs decision"), not an auto-archive** — this matches your existing AGENTS.md "surface conflicts as explicit open loops" rule and avoids ECon's silent one-sided resolution; (c) for Type I/II implicit conflicts, add an entity+attribute key (from YAAMS spaCy NER) so "lives in Seattle" vs "signed a lease in Portland" collide on the `residence` slot even without lexical overlap.
- *Effort:* Near-term to enable behind a flag; attribute-slot extension is medium.
- *Regression risk:* **High if auto-archiving is enabled.** mDeBERTa-mnli-xnli has no Norwegian XNLI data (you flagged `lang:no`), so `contradiction_auto_threshold_lang_no: 0.95` is appropriately conservative — keep auto-resolution off for `lang:no` entirely until validated. Keep `contradiction_protect_higher_confidence: true`.

**A3. Attribute-slot state model (current-state management).**
- *Source:* STALE/CUPMem (arXiv 2605.06527) — reframe memory as *current-state management*: each user attribute (location, employer, dietary preference) is a slot; new observations adjudicate the slot's active value and propagate to dependents. "Is Agent Memory a Database?" (arXiv 2605.26252) argues for a declarative conflict-resolution policy language over field histories with provenance, rather than record-level CRUD, and explicitly critiques both overwrite-on-conflict (Mem0) and silent LLM-gated edge invalidation (Zep).
- *Tier:* cognitive-ledger semantic, especially `03_preferences` and `02_facts`.
- *Implementation path:* Add an optional `attribute_key` frontmatter field (e.g., `attribute: residence`). During sleep, notes sharing an `attribute_key` are adjudicated by valid-time + confidence + provenance; the stale one gets `superseded_by`. This is the bridge that makes your bitemporal fields and contradiction detection *actionable* rather than advisory.
- *Effort:* Medium — new field + sleep adjudication logic, reusing existing bitemporal primitives.
- *Regression risk:* Moderate; the schema addition is optional/backward-compatible (legacy notes lint clean, as your bitemporal rollout already proved).

### Theme B — Reranking & cold-start (your "gap 3")

**B1. Recognition-memory reranking (the single highest-ROI retrieval add).**
- *Source:* HippoRAG 2 (Jiménez Gutiérrez et al., "From RAG to Memory," arXiv 2502.14802, ICML 2025) introduces "recognition memory" — an LLM filter over candidate triples before ranking — and reports roughly a 7-point F1 gain on associative tasks over embedding retrievers. The original HippoRAG (arXiv 2405.14831, NeurIPS 2024) hit 89.1% Recall@5 on 2WikiMultiHopQA vs 68.2% for ColBERTv2. (The paper's ablation reports query-to-triple and recognition-filter components separately — commonly cited as ~+12.5% Recall@5 for query-to-triple and ~+0.7% for the recognition filter; verify these exact splits against the ablation table before quoting.) Zep composes a cross-encoder rerank stage; generic hybrid-search practice retrieves 50–100 candidates via BM25+dense+RRF then cross-encoder reranks the top 20–50 (reranking only 20 is ineffective; 50→100 improves sharply).
- *Tier:* Both tiers; most impactful in YAAMS where the candidate pool is large.
- *Implementation path:* You already shortlist (`shortlist_min_candidates: 24`, `shortlist_max_candidates: 36`). Add an optional rerank stage: BGE-M3 supports reranking (Zep uses BGE-M3 for exactly this), so reuse the loaded model — `bge-reranker-v2-m3` is the cross-encoder sibling. Rerank only the shortlist. For YAAMS, gate behind `--rerank` to preserve the fast FTS-only `--no-vector` path.
- *Effort:* Near-term cheap bolt-on (~50 LOC; model load already paid for).
- *Regression risk:* Low. Adds latency (mitigate by reranking ≤36 candidates and caching by query hash). Keep optional so the fast path is untouched.

**B2. RRF is *not* a free win on your corpus — keep weighted_sum, but document why.**
- *Source:* RRF literature (operates on ranks, sidesteps score-scale mismatch). But your own A/B data shows `semantic_hybrid` weighted_sum (hit@1 0.733, MRR 0.830) beats RRF (hit@1 0.467) on the real corpus — RRF discards the calibrated signal of your hand-tuned weights (`score_weight_bm25 0.30 + lexical 0.15 + tag 0.15 + scope 0.15 + recency 0.15 + confidence 0.10`).
- *Tier:* Both.
- *Implementation path:* You already have `fusion: weighted_sum|rrf`, `rrf_k: 60`. Leave the default; **document the weighted_sum>RRF decision as deliberate** so a future contributor doesn't "fix" it.
- *Regression risk:* None — this is an anti-entropy documentation action.

**B3. Cold-start: the `prior` tie-breaker is correct — validate it and add Ebbinghaus reinforcement as the bridge to signal scoring.**
- *Source:* Generative Agents (arXiv 2304.03442) score = recency+importance+relevance (your `prior`). MemoryBank (Zhong et al., arXiv 2305.10250) Ebbinghaus forgetting curve uses R = e^(−t/S), where t is time since last use and S is memory strength "initialized to 1 and incremented each time a memory is used"; on recall, "S is increased by 1 and Δt is reset to 0." U-Mem (arXiv 2602.22406) mitigates cold-start retrieval bias with semantic-aware Thompson sampling rather than pure similarity.
- *Tier:* cognitive-ledger.
- *Implementation path:* Two moves. (a) Validate the `prior` tie-breaker is active and beneficial via the `prior_enabled: false` ablation you already expose. (b) Bridge to signal scoring: instead of a hard binary at 20 signals (`signal_min_entries`), treat `retrieval_hit`/`preference_applied` signals as **Ebbinghaus reinforcement events** (R = e^(−t/S), S incremented on recall, Δt reset) that bump a per-note `strength`, so the system warms up smoothly. This converts your `accruing→ready→active` state machine into a continuous ramp and directly addresses cold-start. U-Mem's Thompson-sampling idea is the advanced version: occasionally surface lower-similarity notes to gather signal (exploration), avoiding the rich-get-richer trap.
- *Effort:* (a) near-term; (b) reinforcement medium; Thompson sampling a larger bet.
- *Regression risk:* Moderate for (b) — reinforcement can create feedback loops. Cap the boost (you already do this with `validation_boost_cap: 0.15`) and keep synthetic signals down-weighted (`synthetic_weight: 0.5`, real-only activation gate) as you already do.

### Theme C — Associative retrieval (genuinely missing)

**C1. Personalized PageRank / associative graph layer over the semantic tier.**
- *Source:* HippoRAG (arXiv 2405.14831) and HippoRAG 2 (arXiv 2502.14802) — OpenIE triples → schema-free KG with phrase + passage nodes; query seeds Personalized PageRank; probability flows phrase→passage→phrase for multi-hop. GAAMA (arXiv 2603.27910) warns of "mega-hub" entities (recurring people accumulate hundreds of edges, diluting PPR precision). HippoRAG reports up to 20% improvement over prior RAG on multi-hop QA.
- *Tier:* cognitive-ledger semantic; computed at sleep time.
- *Implementation path:* You have everything needed without a graph DB: YAAMS spaCy NER gives entities; atomic notes are passages; `[[links]]` between notes are edges. Build an in-memory sparse adjacency matrix (scipy) at sleep time: nodes = notes + entities, edges = note↔entity (NER) and note↔note (existing links + embedding-similarity above threshold). Run PPR (power iteration, ~10 lines) seeded from query-matched nodes. Cheap (HippoRAG 2 reports sub-second end-to-end retrieval); persist the matrix as a `.npz` in `08_indices/`. Ships as a new retrieval mode (`associative`) that falls back to `semantic_hybrid`.
- *Effort:* **Larger bet** — bounded and additive.
- *Regression risk:* Moderate. Mega-hub dilution is real for a personal corpus (you are a hub). Mitigate: cap per-entity degree, or down-weight high-degree nodes in the personalization vector. Gate behind A/B — ship only if it beats `semantic_hybrid` on multi-hop eval cases.

### Theme D — Consolidation bridge (Electric Sheep) — the defensible moat

**D1. Hierarchical summary tree (RAPTOR / MemTree) in the semantic tier.**
- *Source:* RAPTOR (Sarthi et al., arXiv 2401.18059, Stanford) — recursively embed→cluster (GMM soft-clustering + UMAP)→summarize, bottom-up; collapsed-tree retrieval evaluates all layers; "by coupling RAPTOR retrieval with the use of GPT-4, we can improve the best performance on the QuALITY benchmark by 20% in absolute accuracy"; scales linearly. MemTree (arXiv 2410.14052) — dynamic tree, each node {content, embedding, pointers, depth}; route new info through the hierarchy and update ancestor summaries online.
- *Tier:* Bridge + semantic. Your numbered taxonomy (00–09) is already a *manual* hierarchy; this adds *derived* abstraction layers.
- *Implementation path:* During sleep, cluster atomic notes within a taxonomy folder (or by tag/entity) and synthesize a parent "synthesis note" (you already have `synthesized` as a recommended tag and `auto_file_synthesis` config). Store synthesis notes in a derived layer with pointers to children. At query time, return either the synthesis (broad query) or the atomic children (specific query) — RAPTOR's collapsed-tree mode. MemTree's online incremental update (localized per-node updates rather than full rebuild) fits your continuous-ingest model better than RAPTOR's batch rebuild.
- *Effort:* **Larger bet** — high value, most entropy-prone.
- *Regression risk:* **High for code rot** — synthesis notes are derived state that can desync from atomic notes. Mitigate: make them deterministically regenerable (never hand-edited), store a content-hash of children so sleep detects staleness (you already do dirty-path detection conceptually), and never let a synthesis note be a `supersedes` target. Prove compression-ratio vs information-retention (T6) before trusting them in retrieval.

**D2. A-MEM-style link generation & memory evolution during sleep.**
- *Source:* A-MEM (Xu et al., arXiv 2502.12110) — each note carries keywords/tags/contextual description + embedding; on add, the system finds semantically related notes and creates bidirectional links; crucially, new memories *trigger updates to the contextual descriptions/attributes of existing linked notes* ("memory evolution").
- *Tier:* Bridge (sleep) + semantic.
- *Implementation path:* Your notes already have tags/links/frontmatter. Add a sleep step: for each newly promoted note, embed it, find top-k similar existing notes (BGE-M3 over the ledger index you already build), and propose `[[links]]`. The "evolution" step — re-summarizing an existing note's Context section when a related note arrives — is powerful but riskiest; gate behind review (your `ask-to-write` mode) initially.
- *Effort:* Link generation near-term; evolution medium-to-large.
- *Regression risk:* Link generation: low. Evolution: **high** — mutating existing notes on every related write is the "self-reinforcing error"/drift failure mode (cf. Belief Memory, arXiv 2605.05583). Keep evolution proposal-only (open loop / diff for review) until the eval harness can detect quality regressions; lean on git history (your existing reversibility guarantee).

**D3. Memory admission control on the YAAMS→ledger promotion.**
- *Source:* A-MAC "Adaptive Memory Admission Control for LLM Agents" (arXiv 2603.04549) — without admission control, long-term stores get polluted by low-quality/redundant noise; A-MAC scores writes on ~five interpretable factors (future utility, confidence, novelty, etc.) and intercepts before write, reporting simultaneous latency and quality gains on LoCoMo. U-Mem (arXiv 2602.22406) uses a cost-aware extraction cascade. Your AGENTS.md already encodes the philosophy ("Noise kills retrieval. If none apply: don't write.").
- *Tier:* Bridge (promote) primarily; the YAAMS firehose stays append-only (correctly).
- *Implementation path:* `yaams promote generate` already scores candidates. Make scoring explicit and multi-factor: novelty (max embedding similarity to existing ledger notes — reject near-dups), future-utility proxy (entity/tag overlap with identity notes and open loops), confidence, and source-trust (you have `provenance` trust classes). Surface the factors in the review UI so acceptance is informed.
- *Effort:* Near-term — augments existing promote scoring.
- *Regression risk:* Low — *reduces* entropy by construction. Keep human-in-the-loop acceptance (already required) so admission control is advisory, not silently lossy.

### Theme E — Architecture & hygiene (anti-entropy)

**E1. Keep raw storage append-only and chunk-based — the literature backs your instinct.**
- *Source:* arXiv 2603.02473 (raw chunk storage remains highly effective; retrieval is the bottleneck). MemGPT (arXiv 2310.08560) two-tier main/external context (RAM/disk paging via function calls) is the canonical pattern your YAAMS↔ledger split already instantiates.
- *Action:* Resist compressing/rewriting raw YAAMS items. Your append-only, idempotent, deterministic-ID design (`sha256(source:source_id)`, `INSERT OR IGNORE`) is correct and should be a stated invariant.

**E2. Provenance-weighted confidence — you've already designed it; validate the weights.**
- *Source:* "From Lossy to Verified: A Provenance-Aware Tiered Memory for Agents" (arXiv 2602.17913, TierMem); SSGM (arXiv 2603.11768) pre-merge validation rejecting updates that contradict core facts.
- *Action:* Your `provenance_weighting_enabled` (off), provenance ordering (explicit 1.0 > validated 0.95 > corrected 0.9 > observed 0.85 > imported 0.8 > inferred 0.7), and formula `effective = confidence × provenance_weight + min(boost_per × affirmations, cap)` (`validation_boost_per_signal 0.03`, cap 0.15) are well-formed. This is an A/B candidate, not new code.

---

## A/B Test Cases (runnable on your existing `ledger eval` / `ledger_ab` harness)

Your harness already measures MRR, hit@1, hit@k, p95 latency with exit codes (0 beneficial / 2 regression / 3 neutral / 4 invalid), supports `--cold-query`, env overrides (`LEDGER_WEIGHT_SIGNAL`), negative cases (`expected_none`, `false_positive_rate`, `abstain_accuracy`), and the `ab loop` objective `0.6·mrr + 0.4·hitk`. Use it as the gate for every change above.

**T1 — Recognition reranking ablation (B1).** Hold out 50–100 labeled query→note cases. Compare `semantic_hybrid` vs `semantic_hybrid + bge-reranker-v2-m3` over the shortlist. Metrics: MRR, hit@1, p95 latency. Ship only if MRR gain > latency-budget cost. Vary candidate-pool size (24 vs 36 vs 60) to confirm the literature's "rerank ≥50 candidates" finding on your corpus.

**T2 — Contradiction-detection precision/recall (A2).** Build a labeled set: inject 30–50 synthetic supersession pairs (location/job/preference changes) into a ledger fork, plus 50 non-conflicting near-duplicate pairs as negatives. Measure NLI precision/recall at `contradiction_auto_threshold 0.85` and `0.60`. Report separately for `lang:en` and `lang:no` (expect `no` to fail — that's the finding). Gate auto-resolution on precision > 0.95.

**T3 — Implicit-conflict (STALE-style) probe (A2/A3).** Construct Type I (co-referential: "lives in Seattle" → "new lease in Portland") and Type II (propagated: "leg injury" → invalidates "bikes to work") pairs. Measure detection rate of pure NLI vs NLI + attribute-slot keying. Quantifies A3's value; expect NLI-alone near the STALE ceiling (~55%).

**T4 — Cold-start ramp (B3).** Using `--cold-query` and `LEDGER_WEIGHT_SIGNAL`, compare: (a) signals off (`0.0`, today's default), (b) hard-on at 20 signals, (c) Ebbinghaus continuous reinforcement. Metric: MRR trajectory as signal count grows 0→50. The continuous ramp should dominate in the 10–25 signal regime.

**T5 — Associative retrieval on multi-hop (C1).** Author multi-hop eval cases (answer requires linking 2–3 notes across taxonomy folders). Compare `semantic_hybrid` vs `associative` (PPR). Metric: hit@k on the multi-hop subset specifically (HippoRAG's gains concentrate there). Include queries about high-degree entities to measure mega-hub degradation.

**T6 — Consolidation compression vs retention (D1).** After a RAPTOR/MemTree sleep pass, measure compression ratio (synthesis tokens / child tokens) and information retention (can the same eval queries still be answered using only synthesis nodes?). Borrow the structured-distillation finding (arXiv 2603.13017) that vector retrieval tolerates heavy compression while BM25 degrades — test retention separately on your `score_weight_bm25 0.30` path.

**T7 — Admission-control quality (D3).** Promote a fixed window with and without multi-factor admission scoring; measure downstream retrieval MRR and duplicate rate (notes with embedding similarity > 0.95) in the resulting ledger. A-MAC predicts simultaneous quality + latency gains.

---

## Recommendations — Staged Roadmap

**Stage 0 — Eval substrate first (do before anything else; ~days).**
Expand `retrieval_eval_cases.yaml` with multi-hop, temporal, and negative cases; build the T2/T3 contradiction fixtures. Nothing below ships without passing through `ledger_ab` (exit 0/2/3). This is the single most important anti-entropy investment: it makes every later change reversible and measurable.

**Stage 1 — Quick wins (cheap bolt-ons, low regression risk).**
1. **Recognition reranking (B1)** behind `--rerank`, reusing BGE-M3 — likely the biggest retrieval-quality win per LOC.
2. **Multi-factor admission control on `promote` (D3)** — reduces entropy by construction; human-in-loop preserved.
3. **Document the weighted_sum>RRF decision (B2)** and the append-only invariant (E1) — pure anti-rot.
4. **Wire event-time into bitemporal fields from YAAMS timestamps (A1)** — finishes a feature you already built.
5. **Validate the `prior` tie-breaker (B3a)** via the `prior_enabled` ablation.
*Threshold to proceed:* T1 shows MRR gain at acceptable latency; T7 shows duplicate-rate reduction.

**Stage 2 — Medium bets (validate-then-enable existing features + reinforcement).**
1. **Turn on contradiction detection as *supersession-candidate→open-loop* (A2), `lang:en` only**, after T2 clears precision > 0.95. Keep `lang:no` off.
2. **Attribute-slot state model (A3)** — the keystone connecting bitemporal + contradiction into actionable current-state management.
3. **Ebbinghaus reinforcement bridge to signal scoring (B3b)** — smooths cold-start.
4. **A-MEM link generation during sleep (D2, links only)**.
5. **Provenance-weighted confidence (E2)** as an A/B candidate.
*Threshold to proceed:* T3/T4 show the attribute model and reinforcement beat baselines; no regression in negative-case `false_positive_rate`.

**Stage 3 — Larger bets (only behind proven eval gates).**
1. **Associative PPR retrieval mode (C1)** — ship only if T5 beats `semantic_hybrid` on multi-hop without mega-hub collapse.
2. **Hierarchical consolidation (RAPTOR/MemTree, D1)** — highest value, highest rot risk; require deterministic regeneration + staleness hashing + T6 retention proof.
3. **A-MEM memory evolution (D2, note mutation)** — proposal-only until eval can catch drift; lean on git reversibility.
4. **U-Mem Thompson-sampling exploration in retrieval (B3)** — advanced cold-start; only after reinforcement is stable.

**Benchmarks that change the plan:** If T1 (reranking) shows no gain, the corpus is too small for two-stage retrieval — stop and revisit at larger scale. If T2 contradiction precision stays < 0.9 even for `lang:en`, keep contradiction advisory-only indefinitely (surface to open loops, never auto-resolve). If T6 retention drops below ~90%, do not let synthesis nodes into the default retrieval path — keep them browse-only.

---

## Caveats

- **The "known gaps" framing is partly stale.** Bitemporal and contradiction code already exist (v0.6.0); this report is calibrated to that reality. Re-confirm the exact code state before implementing — schema/config/CHANGELOG were read verbatim, but the raw `.py` retrieval/scoring source was robots-blocked, so the precise `semantic_hybrid` weight wiring (reported as ≈0.55 semantic / 0.30 lexical+bm25 / 0.10 / 0.05 from a CHANGELOG quote of a hardcoded fallback in `rank_query_semantic_hybrid()`) should be verified directly in `retrieval.py` / `ledger/scoring.py`.
- **Verify the HippoRAG-2 ablation splits before quoting.** The ~7-point associative-task F1 gain is paper-confirmed; the finer per-component splits (query-to-triple ~+12.5% Recall@5, recognition filter ~+0.7%) are commonly cited but should be checked against the paper's ablation table, not abstract-level summaries.
- **Many cited 2026 papers are recent and some single-source.** The Awesome-AI-Memory entries dated Feb–Mar 2026 (A-MAC, U-Mem, STALE, the "Diagnosing Bottlenecks" diagnostic, "Is Agent Memory a Database?") are arXiv preprints, not peer-reviewed; treat headline numbers (A-MAC's LoCoMo gains, the "retrieval = dominant bottleneck" claim, STALE's 55.2% ceiling) as directional, not settled.
- **NLI on Norwegian is unsupported.** mDeBERTa-mnli-xnli has no Norwegian training data; any `lang:no` contradiction/supersession must stay manual until you validate or swap models. Your `contradiction_auto_threshold_lang_no: 0.95` already reflects this.
- **Evolution/mutation features are the entropy risk.** A-MEM evolution and RAPTOR synthesis both mutate or derive state; without the Stage-0 eval harness and git-based reversibility they are how this system rots. The roadmap deliberately gates them last.
- **Personal-corpus mega-hubs.** PPR associative retrieval works well in the literature on document corpora; a single-user personal memory has extreme hub entities (you, your main projects). Validate hub handling explicitly (T5) before trusting it.
- **External benchmarks are conversational; your corpus is multi-source personal exhaust** (email/iMessage/calendar/GitHub/Teams). LoCoMo/LongMemEval numbers transfer only directionally — your own held-out eval set is the authority.