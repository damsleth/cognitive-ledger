# Next steps

Still open after the 2026-10-08 review. Full context: `.plans/codebase-review-2026-10-08.md`
(B1–B12, D1–D4) and `.plans/improvement-plan-2026-10-08.md` (I1–I8).
Checked against the live corpus on 2026-10-15.

## Owner: corpus jobs

- [ ] `ledger signal summarize`. The summary is from 2026-06-20; signals run to 2026-08-27.
- [ ] `ledger --doctor --fix`. Reaps unheld lock files (32 now); held locks are left alone.
- [ ] Remove the 0-byte leftover `~/brain/ledger/08_indices/timeline.jsonl.tmp`.
- [ ] In `~/brain/ledger/08_indices/retrieval_eval_cases.yaml`:
  - [ ] Fix or drop `dev_gth_next_action`. It expects `loop__gth_prod_deployment_and_julia_update.md`, which no longer exists.
  - [ ] Add `as_of` to the history cases:
    - `as_of: 2026-04-27` on `dev_norwegian_llm_loop` and `loop_verditakst`;
    - `as_of: 2026-02-08` on `history_v019_deployment`, `history_une_script` and `history_brkh_visit`.
    - On a copy this took hit@3 from 0.870 to 0.957.
  - [ ] Review the 29 drafted cases in `retrieval_eval_cases.draft.yaml` (12 Norwegian, 5 identity, 4 open loops, 8 negatives) and append the ones you approve. Nothing reads the draft file.

Done since the review: bitemporal migration applied, inbox triaged (1 item left), semantic index rebuilt (2026-10-15), main checkout pulled, `ledger mcp --allow-write` registered in Claude Code.

## Owner: corpus clean-up surfaced by the new lint rules

- [ ] 20 non-loop notes carry `status:` (14 facts, 5 preferences, 1 goal with `status: active`). Drop the field, or turn the note into a loop.
- [ ] 1 note has more than 10 tags.
- [ ] 101 `source: inferred` notes at confidence ≥ 0.7. Lower them below 0.7, or re-source them as `user`/`tool` where that's true.
- [ ] Decide whether slugs may contain non-ASCII characters. Two files break `schema.yaml`'s slug pattern: `fact__sjæb_project.md` and `fact__nocos-sharepoint-editor-sql-over-cosmos.md`. Then either widen the pattern or rename the files.
- [ ] 32 remaining "large file" warnings on atomic notes (over 400 words). Split them or accept.

## Agent work, ready when you are

- [ ] **Identity embedding (`feat/embed-identity`).**
  - In `semantic_hybrid`, 4 of 5 identity notes fall outside the top 20 for queries aimed straight at them.
  - Embedding them regresses on the current 46 cases (hit@1 0.783 → 0.739). With identity cases added, every metric improves.
  - Next: once identity cases are in the live set, re-run `ledger ab run --baseline-ref main --candidate-ref feat/embed-identity` and merge if it isn't a regression.
  - Also check which notes took rank 1 from the two cases that dropped.
- [ ] **Signals.** `ledger_signal` exists now. Watch whether `retrieval_hit` signals accrue through MCP (`ledger signal stats`). That is what unparks ai-memory plans 10 and 16.
- [ ] **ai-memory plan 14 (hierarchical consolidation)** is unblocked now that `synth_backend: claude` / `haiku` is set (about 60 model calls).
- [ ] **agentisk PR 4** still waits on an owner skim of the agent-labelled abbreviation gold set.
- [ ] **I8 (blocking latency gate)** is deferred on purpose: latency is advisory. Pick it up only if a real latency regression slips through. Cheapest first step: compare medians, not p95 of 5 samples.

## Housekeeping

- [ ] Delete merged or stale remote branches: `fix/review-batch1-gate`, `fix/review-batch2-privacy`, `fix/review-batch3-lint`, `fix/improve-i1-ci-gate`, `feat/review-decisions-improvements`, `worktree-review-2026-10-08`. Keep `feat/embed-identity`.
- [ ] Copilot's monthly quota ran out during the review; codex (medium) did the code second reviews instead.
