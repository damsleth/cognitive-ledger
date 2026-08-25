# TODO

## Reviewed 2026-06-10

Full plan-queue refresh. Every non-finished plan under `.plans/` was verified
against the codebase + git history and rewritten in place to be implementable
by Sonnet-level agents (file:line anchors, signatures, named test cases,
acceptance commands). Plan 37 (batch triage) shipped in `96e1e07` and moved to
`done/`. A fresh codebase review was written to `codebase-review-2026-06-10.md`;
its privacy findings (raw private-fenced content reaching the embeddings API,
LLM-judge prompts, contradiction conflict notes, and web backlinks) jump the
queue because privacy stripping is a project non-negotiable. Earlier review
notes (2026-05-15, 2026-06-04) are summarized in git history of this file.

## Active plan order

> 2026-06-17 reconciliation: the plan files for this section are no longer on
> disk (`.plans/` is gitignored except this TODO, so `git pull` never touched
> them — they were removed some other way and aren't recoverable from git).
> Statuses below are reconstructed from `CHANGELOG.md`, not from the plan files.

Shipped (CHANGELOG 0.8.0, 2026-06-10) — treat as done, files gone:
- ✅ `unify-signal-handling-across-signals-and-review-mo.md` — "Signal
  activation unified in signals.py" (review.py delegates to signals.py).
- ✅ `cognitive_ledger_improvement_tasks_2026-05-03.md` — "Codebase review
  R1–R8" batch.
- ✅ `refactor-retrieval-scoring-into-composable-scorers.md` — "ledger/scoring.py
  composable scoring primitives" (bit-identical scoring).
- ✅ `things3-open-loops-sync.md` — "Things3 ↔ open-loops bidirectional sync".
- ✅ `codebase-review-2026-06-10.md` — privacy/data-loss batches landed in 0.8.0
  (`--doctor --fix`, 7 doctor checks, `docs/privacy.md`, `docs/trust-boundaries.md`,
  R1–R8). Later interleaved batches not separately tracked.

Shipped in the yaams repo (verified 2026-06-17 against `~/code/yaams` history):
- ✅ `36-yaams-tier2-a-rejection-log.md` — cogled `ledger inbox rejected` CLI +
  `list_rejections`/`clear_rejections` (`ledger/inbox.py`); yaams consumes it
  (`60eeab9` skip candidates cogled already rejected).
- ✅ `38-yaams-tier2-c-semantic-dedup.md` — `yaams/promote/dedup.py` (calls
  `ledger embed search … --json`, exposes `DedupVerdict`/`DedupConfig`).
- ✅ `39-yaams-tier2-d-cross-tier-query.md` — `2b0846e` query `--tier`,
  `--json`/`--pretty` aliases + reserved-key check.
- ✅ `40-yaams-tier2-e-conflict-detection.md` — `ce733ed` ConflictChecker /
  `promote.conflict_detection` (yaams CHANGELOG). `35-…-roadmap.md` was just
  their index page.

Open (no shipping evidence — re-author the plan if resuming):
- ❓ `41-web-interface-v1.md` — Phase 4 graph view + Phase 5 polish. Last web
  entry is 0.3.1 (Phase 3 backlinks); no graph view shipped. Includes the
  `Corpus._rebuild_link_maps()` private-link-stripping fix.

## Memanto-inspired plans — DONE (2026-06-17, archived to `done/`)

All six plans (42–47) shipped on `memanto-inspired-plans` (commits
05dda15..e6819b8), merged to main in `1575e45` and released as v0.9.0.
Plan files + `memanto-inspired-index.md` and `memanto-inspired-performance.md`
moved to `.plans/done/`. Summary: `ledger mcp`, `ledger answer`, `ledger
changed` / `query --changed-since`, trust verdict in `--view`, provenance-
weighted confidence (42) and type-dependent decay (43) — 42/43 ship OFF by
default (behaviour-neutral, A/B quality-tied).

All deferred follow-ups also done in `bd70d3c` + `9e5bcce`:
- 42#8: `ledger sleep provenance --check/--apply` stamps `provenance:corrected`.
- 47C: `ledger briefing` "Recent Changes" window since last briefing run.
- 45: `answer`/`AnswerResult` exported from `ledger/__init__.py`.
- README/CHANGELOG updated for the new verbs (answer, changed, mcp).

## Cross-cutting checks before executing any plan

- Use fixture corpora in tests; do not depend on Kim's live notes.
- Strip private fenced content from every index, prompt, JSON output, and web/search snippet.
- Update README/CHANGELOG only for shipped user-facing behavior, not for plan edits.
- Preserve existing uncommitted work unless the active task explicitly owns it.
- [ ] Klassifisereren i claude_memory.classify er first-match-wins, og concept-in-title er hoyeste presedens. Det er nettopp derfor EN los markor (`convention`) kunne overstyre alle andre signaler og gjore en disambigueringsfakta til concept. Vurder a score signaler mot hverandre i stedet for a returnere pa forste treff, sa ingen enkelt markor kan kuppe klassifiseringen.
- [ ] Sterkere form av den lukkede embed-todoen: query-varselet er passivt. Vurder a la `inbox triage` og `import-claude-memory --apply` selv koe/utfore en embed-rebuild nar de har promotert noter, sa vinduet lukkes automatisk i stedet for a kreve at brukeren leser stderr. `sleep index` gjor alt riktig alt - det mangler bare a bli kalt.
- [ ] `updated:`-feltet er ubrukelig som ferskhetssignal i praksis: 34 loops delte tidsstempelet 2026-07-06T00:00:00Z og 16 delte 2026-06-02 - altsa 50 av 58 maskinsatt av bulk-operasjoner. briefing.py beregner staleness fra nettopp dette feltet (linjer ~176), sa briefingen bommer systematisk pa hva som faktisk er ferskt. Bestem: slutt a bulk-touche `updated`, eller regn staleness fra timeline.jsonl i stedet.
- [ ] Na som `forward_complete` finnes, revurder default `things3_orphan_action: flag`. Flagg-banen prefikser Things-tittelen med `[orphan]` og har ingen un-flag - en engangs, ikke-reversibel tittelmutasjon i brukerens personlige tasksystem. De aller fleste tidligere orphans var egentlig lukkede loops (8 av 8 i den forste ekte kjoringen), og de handteres na korrekt. Vurder `ignore` som default og la `flag` vaere opt-in.
