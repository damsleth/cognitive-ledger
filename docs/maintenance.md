# Sleep and maintenance

Reference for consolidation ("Electric Sheep"), lock files, link proposals and the contradiction scan.

## Electric Sheep (sleep / consolidation)

```bash
ledger sleep status
ledger sleep sync --check && ledger sleep sync --apply
ledger sleep sleep
ledger sleep lint
ledger sleep index
ledger sleep links                     # propose [[links]] for notes changed since last sleep (--all, --apply)
ledger sleep contradictions --check    # dry-run NLI contradiction scan (requires contradiction_enabled=true)
ledger sleep contradictions --apply    # execute: auto-supersede or write conflict notes to 00_inbox
ledger migrate bitemporal --check   # preview valid_from / valid_to back-fill
ledger migrate bitemporal --apply   # write back-fill + append timeline entry
```

**Lock files.** `FileLock` deliberately does **not** unlink on release: unlinking
after dropping the flock lets a waiter and a newcomer both believe they hold the
lock. `*.lock` files therefore accumulate across note and importer directories.
`ledger inbox cleanup` reaps them recursively (`unheld_locks` in its result),
including nested and non-Markdown locks, removing a lock only when a
non-blocking flock succeeds, which proves nobody holds it:

```bash
ledger inbox cleanup           # dry run — lists what would go
ledger inbox cleanup --apply   # reap
```

Safe because this is an explicit maintenance sweep, not the write path. Expect
locks held by the running process to survive, including timeline, index, or
importer state locks.

**Link proposals** (`ledger sleep links`) suggest `[[links]]` between related notes from the vectors already in the semantic index (no model load): top 5 neighbours with cosine in [0.80, 0.97), both directions, minus links that exist. Above 0.97 a pair is a near-duplicate for `sleep duplicates`, not a link. Dry run by default; `--apply` appends `- [[stem]]` under `## Links`, bumps `updated`, and logs a `linked` timeline event — which the briefing does not count as activity. Links only: nothing rewrites what a note says.

**Contradiction scan** (`ledger sleep contradictions`) uses a local NLI classifier to detect pairs of notes whose content contradicts each other. Three outcomes:

- **review** (the default for every contradiction) — score ≥ `contradiction_review_threshold` (default 0.60) → writes a **conflict note** to `notes/00_inbox/` (filename `conflict__{timestamp}__{stem_a}__{stem_b}.md`, tags `[conflict, nli, review]`, checklist for human resolver). List them with `ledger inbox conflicts`.
- **auto-supersede** — opt-in only (`contradiction_auto_supersede: true`): score ≥ `contradiction_auto_threshold` (default 0.85) + candidate strictly newer + no confidence inversion + neither note `lang:no` → calls `supersede()`, moves old note to `09_archive/`. **Keep it off.** On the T2 fixture (2026-09-23, `python -m ledger.contradiction --eval tests/fixtures/contradiction_t2.yaml`) the default model scored `lang:en` precision **0.862** at both 0.60 and 0.85 (recall 1.0) — below the 0.9 floor for any auto-resolution, i.e. about one auto-archive in seven would retire a note that is still true.
- **ignore** — score below review threshold → no action.

**Attribute slots.** A note may carry `attribute: <slug>` (`employer`, `kim.residence`; qualify with a subject when the note is about someone else). Two live notes on the same slot are filed as a conflict note before the NLI pass — this catches implicit conflicts NLI misses, needs no model, and never auto-resolves.

Hard rules (not config-overridable): identity notes are never auto-superseded; duplicate conflict inbox records for the same pair are not created; re-running on an already-resolved pair is a no-op.

**Norwegian caveat.** The default model (`MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`) is trained on MNLI + XNLI (15 languages); Norwegian is not among them. NLI accuracy on `lang:no` notes is unvalidated, so a pair involving a `lang:no` note never auto-resolves, even with auto-supersede on. (The 14 Norwegian T2 pairs scored precision 1.0 / recall 0.875 — too few to trust.)

The scan is off by default (`contradiction_enabled: false`). Enable in `~/.config/ledger/config.yaml` once the NLI model is downloaded and validated.

**Config keys:**

| Key | Default | Env override |
|---|---|---|
| `contradiction_enabled` | `false` | `LEDGER_CONTRADICTION_ENABLED` |
| `contradiction_model` | `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli` | `LEDGER_CONTRADICTION_MODEL` |
| `contradiction_neighbors_k` | `8` | `LEDGER_CONTRADICTION_NEIGHBORS_K` |
| `contradiction_auto_threshold` | `0.85` | `LEDGER_CONTRADICTION_AUTO_THRESHOLD` |
| `contradiction_review_threshold` | `0.60` | `LEDGER_CONTRADICTION_REVIEW_THRESHOLD` |
| `contradiction_auto_supersede` | `false` | `LEDGER_CONTRADICTION_AUTO_SUPERSEDE` |
| `contradiction_protect_higher_confidence` | `true` | `LEDGER_CONTRADICTION_PROTECT_HIGHER_CONFIDENCE` |
