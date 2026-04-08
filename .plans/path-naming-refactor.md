# Path Naming Refactor

## Summary
- Standardize the repo on three canonical path identifiers: `ledger_root`, `ledger_notes_dir`, and `source_notes_dir`.
- Apply the change repo-wide: core runtime, CLI/help text, `/notes` skill, TUI, generated config, tests, and docs.
- Treat this as a hard cutover: remove the old names instead of aliasing them.

## Key Changes
- Config model:
  - Replace `LedgerConfig.root_dir`, computed `notes_dir`, and `source_root` with stored `Path` fields `ledger_root`, `ledger_notes_dir`, and `source_notes_dir`.
  - Rename env vars to `LEDGER_ROOT`, `LEDGER_NOTES_DIR`, and `LEDGER_SOURCE_NOTES_DIR`.
  - Keep defaults: `ledger_notes_dir = ${ledger_root}/notes`, `source_notes_dir = ~/notes`.
  - Make config loading fail fast if YAML support is unavailable or if removed keys/env vars are present; no more silent no-op behavior.
- Shared path/layout API:
  - Introduce one shared note-layout registry and path helper layer used by config, retrieval, browse, maintenance, inbox, voice, context, ingest, embeddings, and TUI.
  - Remove duplicated note-folder definitions and all ad hoc `root_dir / "notes"` logic or `"notes/"` string stripping.
  - Standardize logical note identifiers as `notes/...` everywhere persisted or displayed, even when `ledger_notes_dir` is physically outside `ledger_root`.
- Public interfaces:
  - Rename CLI flags that mean ledger corpus paths to `--ledger-notes-dir`.
  - Rename CLI flags that mean external note sources to `--source-notes-dir`.
  - Update the `/notes` skill to use the same runtime contract: `LEDGER_ROOT`, `LEDGER_NOTES_DIR`, and `LEDGER_SOURCE_NOTES_DIR`.
  - Update `config.sample.yaml`, checked-in `config.yaml`, README, AGENTS docs, and skill docs/examples to the new names only.
  - Keep Obsidian-specific `vault_root` naming in its separate config surface; only shared ledger config/runtime adopts the new canonical names.
- Internal correctness fixes that must land with the rename:
  - `ledger_notes_dir` must be loadable from `config.yaml` as a real field, not a read-only property.
  - Timeline, note index, retrieval `rel_path`, and links index must never emit absolute paths for ledger notes.
  - Inbox promotion, voice import, and TUI read/write paths must resolve through `ledger_notes_dir`, not `ledger_root/notes`.

## Test Plan
- Config tests:
  - `config.yaml` with `ledger_root`, `ledger_notes_dir`, and `source_notes_dir` loads correctly.
  - Removed keys/env vars fail with explicit migration errors.
  - Missing `yaml` support fails loudly with an actionable error.
- Cross-root integration tests:
  - Add a fixture where `ledger_root` and `ledger_notes_dir` are different locations.
  - Verify query, browse, maintenance/index rebuild, context generation, inbox promotion, voice import, and timeline writing in that layout.
  - Assert persisted note references are logical `notes/...` paths.
- CLI/TUI/public-surface tests:
  - Parser coverage for renamed flags and rejection of removed flag names.
  - TUI store/writer tests using external `ledger_notes_dir`.
  - Doc/skill smoke checks that old names (`LEDGER_DIR`, `NOTES_DIR`, `root_dir`, `notes_dir`, `source_root`, `LEDGER_ROOT_DIR`, `LEDGER_SOURCE_ROOT`) no longer appear in active guidance.

## Assumptions
- `ledger_root` remains the repo/config/scripts/templates/semantic-artifacts root.
- `ledger_notes_dir` is the physical atomic-note corpus location.
- `source_notes_dir` is the external human-facing notes/Obsidian tree.
- No compatibility layer will be kept after the refactor; migration is explicit and breaking.
