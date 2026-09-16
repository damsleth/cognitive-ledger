# ledger close memory and obsidian ingestion gaps

_Created 2026-06-19_

## Problem
Ledger retrieval is strong (clean 0–1 scores, right note #1 on most probes) — but it can only
rank what's been promoted into it. Two intake gaps confirmed 2026-06-19:

1. **Memory→ledger gap.** "Jan vs Jan Karl" — neither system disambiguates. The naming
   convention (Jan = Jan Guttormsen, Currents/ekstern; Jan Karl = Jan Karl Andersen, SWON/intern)
   lives only in Claude auto-memory `nocos-jan-naming-convention.md`. Zero dedicated fact in
   `02_facts/` (confirmed by grep).

2. **Obsidian retrieval gap.** "BRKH økonomiutvalg" — appears in 2 Obsidian styremøte notes
   (`notes/03-brkh/lokalforening BRK/2026-0{5-12,6-09} styremøte BRK.md`) but the content isn't
   surfacing for retrieval.

   **Decision (Kim, 2026-06-19): BRKH meeting minutes stay Obsidian-only.** Do NOT carve meeting
   notes into a separate "content type" source — that multiplies sources, then forces dedup
   against Obsidian, and the details are a swamp. The fix lives at the agent layer: the agent
   ingesting/retrieving should detect metadata/tags/context from the Obsidian note itself
   (path = `03-brkh/...`, "styremøte", "økonomiutvalg") rather than relying on a pre-extracted
   typed source.

## Goal
Durable facts captured in Claude auto-memory and Obsidian get promoted into retrievable ledger
notes, so "what's true" queries don't fall through to the raw firehose.

## Progress (2026-06-19)
- ✅ Ran `ledger import-claude-memory --apply` → **40 notes staged to `00_inbox/`** (incl. the Jan
  disambiguation). Gap #1 was "never run," not "no pipeline." Pipeline works end-to-end.
- ✅ `ledger inbox triage` run by Kim → notes promoted to typed folders. Jan note landed as
  `03_preferences/pref__nocos_jan_naming_convention.md` (triage corrected the importer's `concept`
  proposal — triage IS fixing classification, good).
- ✅ **Quality verified after rebuild** — see Finding C below; Jan probe now returns the note **#1 @0.672**.
- 🐞 Bug A: importer **mis-typed** `nocos_jan_naming_convention` as `concept` (title-heuristic on
  "naming convention"); it's really a fact/pref. Triage caught it this time, but the heuristic is wrong.
- 🐞 Bug B: importer **leaks a 0-byte `<note>.md.lock`** per note, and `ledger inbox cleanup` does
  NOT reap them ("nothing to clean up"). Removed the 40 manually. Fix: importer should clean its own
  locks, and/or `inbox cleanup` should match fresh import locks.

## Finding C — semantic index staleness window (the important one)
Adding/promoting notes does **not** improve retrieval until the embedding index is rebuilt. In
`semantic_hybrid` mode candidates are drawn from the embedding index first, so an un-embedded note
**never enters the candidate pool** — lexical overlap can't rescue it. Quality delta is *bimodal*,
not gradual: nothing retrievable until rebuild, then a perfect hit.

Proof (same query "difference between Jan and Jan Karl…", 2026-06-19):
1. Pre-import: note didn't exist → miss.
2. Post-triage, note promoted, **stale index** (387 items, built 2026-06-18) → still absent from
   top-5 *even when querying the note's literal words*.
3. After `ledger embed build --target ledger --backend local` (387→425, +38 embedded) → note **#1 @0.672** (semantic 0.657).

Risk: anyone who imports → triages → immediately queries concludes "the import didn't help" and is
wrong. There's a silent gap between triage and the next `embed build` (or `sleep`).
Tracked as a 3rd todo in `cognitive-ledger/.plans/`.

## Steps

- [x] Memory sync: ran `import-claude-memory --apply`; 40 notes incl. Jan note staged to inbox
- [x] `ledger inbox triage` → promoted; Jan note now `03_preferences/`; re-probe confirms **#1 @0.672** (after embed rebuild)
- [x] Rebuild embed index after import (`embed build --target ledger --backend local`) — REQUIRED, see Finding C
- [x] Fix importer lockfile leak (Bug B) + concept/fact title-heuristic (Bug A) + auto-rebuild-on-triage (Finding C) — **done 2026-08-21**, branch `fix/brain-ledger-cohesion` in cognitive-ledger:
  - Bug A: `convention` dropped from `_CONCEPT_MARKERS` (it made the title-first branch file naming *facts* as concepts). Jan note now classifies `facts`.
  - Bug B: `FileLock` NOT changed — its no-unlink is deliberate (unlink-after-unlock race, documented in-source). Fixed the reaping side instead: `reap_unheld_locks()` sweeps the notes tree and removes a lock only when a non-blocking flock proves nobody holds it. 42 + 57 reaped; steady state is 4 `08_indices` machine-state locks held by the running process.
  - Finding C: `ledger query` now warns on stderr when notes changed since the index build, and names `ledger sleep index`. Put at query time, not after each write, so one check covers import/triage/notes-add/hand-edits.
  - **New (Bug D), worse than A/B/C:** the importer had no idea what triage had promoted, so a memory file whose note was already typed got re-imported. Live damage: `yaams-owa-ingestion-roadmap` existed as BOTH fact and loop, and `three-gaps-branches-rollout` came back as an *open* loop after the hand-written original was closed with every step ticked. `existing_external_ids()` now indexes `external_id` across typed folders and `build_plan` skips collisions — reported in its own bucket so a dropped *update* is visible.
- [x] Confirm `notes/03-brkh/**` is in the Obsidian ingest scope at all — **yes in scope, but the importer is dead.** Checked 2026-09-16:
  - Scope is fine: `DEFAULT_EXCLUDE_DIRS` (`importers/backends/obsidian/config.py:19`) excludes only `.obsidian/.git/.trash/.smart-env/.smartchats/cognitive-ledger/attachments`. Nothing filters `03-brkh`.
  - **The importer last ran 2026-01-21** and its state points at `vault_root: /Users/damsleth/Code/notes`, a path that no longer exists (live vault is `~/brain/notes`). 13 of the 50 processed files were `03-brkh/**`, so the pipeline did reach BRKH — the two styremøte notes named in this plan (2026-05-12, 2026-06-09) simply postdate the last run.
  - **Second break:** that state sits at the legacy path `08_indices/obsidian_import_state.json`, while the current code reads `08_indices/importers/obsidian/state.json` (`models.py:84` → `backend_state_dir`). Only `importers/claude_memory/` exists. So the old state is invisible and a fresh run re-processes from zero.
  - **But that importer is not what this step was about.** Probed yaams directly 2026-09-16 (`yaams_query "økonomiutvalg" --source notes`): both plan-named styremøte notes **are indexed** — `03-brkh/lokalforening BRK`, 2026-05-12 and 2026-06-09, plus the 2026-08-20 one. Obsidian *indexing* scope is fine.
  - **The actual gap is ranking, not scope.** In the `notes` source those hits score **0.0063–0.0065** — indistinguishable from a 2024 journal note about consultancy finances that has nothing to do with BRKH. "økonomiutvalg" is not matching lexically at all. Run mixed-tier (`"BRKH økonomiutvalg"`) and the notes hits vanish entirely: the top 8 are all `tier2_ledger` facts at 0.03–0.05, matching on "BRKH" alone.
  - So the next two steps stand as written (agent-side context detection at the retrieval layer), but the target is the flat scoring of the `notes` source, not ingest scope. The dead ledger-side Obsidian importer above is a separate, real finding — worth its own todo, not a blocker here. — if the styremøte notes aren't indexed, even smart retrieval can't reach them
- [ ] Agent-side context detection: derive tags/metadata from the note (path segment `03-brkh`, "styremøte", entity "økonomiutvalg") at ingest OR retrieval time — single Obsidian source, no per-content-type sources, no dedup
- [ ] Re-probe "BRKH økonomiutvalg" — the styremøte content should surface with BRKH/økonomi context attached
- [ ] Add both probes ("Jan vs Jan Karl", "BRKH økonomiutvalg") to `ledger eval` cases with expected target notes; run `ledger eval --cases ...` to lock in

## Notes
- ponytail — prefer fixing the existing `import-claude-memory` / ingest filters over a new pipeline.
- Coordinate with yaams plan: once these facts exist as tier2_ledger, the yaams tier-boost makes them surface in the firehose too. Both probes are the shared benchmark.
- Resolved: BRKH minutes stay Obsidian-only; intelligence at the agent (ingest/retrieve) layer, not new sources. General principle — push content-type awareness into detection, not into the source taxonomy.

## Fase 2 — utført 2026-08-21

Kjørt som del av brain↔ledger↔Things-koherensrunden. Se
`loops-todos-gjennomgang-2026-08-21.md` for gjennomgangen som utløste den.

- ✅ 8 loops lukket (3 på Kims svar, 5 beviselig ferdige), 1 ny loop opprettet
  via `ledger notes add` (troika-vaktansvar), 2 fikk reelle next actions.
- ✅ `ledger sleep lint`: 6 errors → **0**; `open_loop_missing_next_action_section` 2 → **0**.
- ✅ `ledger sleep index` kjørt; ny troika-loop rangerer **#1 @0.683** på
  «hvem har vaktansvar i BRKH nå».
- ✅ `brain loops` kaller nå `ledger loops --status open` i stedet for å
  re-implementere den feil. Dashboard: 58 → **30** åpne loops.
- ✅ **Things-speilet lukkes nå forover.** Ny `forward_complete`-handling:
  syncen hadde `reverse_complete` (Things→ledger) men ingen vei tilbake, så en
  lukket loop lignet en *slettet* og fikk `[orphan]`-flagg. 8 orphan-flagg →
  8 fullførte tasks. Bekreftet i Things at alle 8 står `completed`.
- ✅ Bonusfunn: `_confirm_by_uuid` bekreftet complete/cancel ved å *lese tasken
  igjen* i den aktive lista — som per definisjon ikke kan lykkes. Alle 8
  fullføringer rapporterte falsk feil. Ville også skjult en ekte feil.

### Gjenstår (Obsidian-halvdelen, urørt denne runden)
- [ ] Confirm `notes/03-brkh/**` is in the Obsidian ingest scope at all
- [ ] Agent-side context detection (path segment `03-brkh`, "styremøte", entity "økonomiutvalg")
- [ ] Re-probe "BRKH økonomiutvalg"
- [ ] Add both probes to `ledger eval` cases
