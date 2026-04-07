# Phase 1: Voice DNA Integration + Content Index

## Problem

The ledger has no concept of the user's writing voice, so agent-written notes
sound generic. The boot context (`context.md`) is a summary, not a browseable
catalog - agents can't scan what the ledger contains without loading every note.

## Plan

### 1a. Voice DNA as Identity Note

Store the output of the `voice-dna-creator` skill as a ledger identity note
so every agent can read and apply it when writing.

1. Create `templates/voice_dna_template.md` - wraps voice-dna JSON in
   frontmatter + fenced code block
2. Create `ledger/voice.py` (~100 lines):
   - `import_voice_dna(json_path)` - validate JSON, write to
     `notes/01_identity/id__voice_dna.md`
   - `export_voice_dna()` - extract and return the JSON
   - `get_voice_profile()` - return parsed profile or None
3. Add `voice-dna` subcommand to `scripts/ledger`:
   - `ledger voice-dna import <json-path>`
   - `ledger voice-dna show`
4. Update `schema.yaml`:
   - Add `voice` to `identity_type` enum (6th value)
   - Bump `max_identity_notes` from 5 to 6
   - Update `ledger/notes/__init__.py` if it hardcodes the cap
5. Update `skills/notes/SKILL.md` - add instruction:
   "Before writing any note longer than 2 sentences, read
   `notes/01_identity/id__voice_dna.md` if it exists. Apply the voice
   profile to tone, sentence structure, and vocabulary."
6. Update `AGENTS.md` Boot section to mention voice identity note

### 1b. Content-Oriented Index (Karpathy's index.md)

Generate a browseable, content-oriented catalog as part of `sheep index`.

1. Add `_generate_content_index(notes_dir, indices_dir)` to
   `ledger/maintenance.py`:
   - Group by note type
   - Each entry: title, one-line summary (first sentence of body),
     tags, confidence, updated, relative path
   - Output `notes/08_indices/index.md` (human-readable) +
     `notes/08_indices/index.json` (machine-consumable)
2. Call from existing `cmd_index()` in the sheep pipeline
3. Update SKILL.md Boot Sequence: read `context.md` first (compact
   summary, stays small), then use `index.md` as a lookup table for
   deeper searches. **Do not** load the full index into context at boot -
   on a mature ledger it would become the context bottleneck. Instead:
   - Boot reads `context.md` (existing behavior, unchanged)
   - When the agent needs to find notes by topic, it reads `index.md`
     or `index.json` as a lightweight lookup before `rg`
   - The index replaces blind `rg` searches, not the boot summary
4. Update AGENTS.md Boot section to document the two-tier strategy:
   `context.md` for boot, `index.md` for lookup

### 1c. Obsidian-Friendly Retrieval API

The content index and retrieval module should be queryable by external tools -
specifically the `ledger-obsidian` CLI and a future Obsidian plugin. This
means the lookup path must work with arbitrary text input, not just ledger
note paths.

1. Ensure `index.json` is stable and documented:
   - Each entry includes: `path`, `title`, `summary`, `tags`, `note_type`,
     `confidence`, `updated`
   - Format is a JSON array (not JSONL) so external tools can parse it
     without streaming
2. Add a text-query entry point to `ledger/retrieval.py`:
   - `related_to_text(text, top_k=5)` - tokenize arbitrary text (e.g. an
     Obsidian note's contents), run it through the existing
     `retrieve_candidates_from_index()` pipeline, return ranked results
   - This is a thin wrapper - the retrieval machinery already exists
3. Expose via `ledger-obsidian`:
   - `ledger-obsidian related <path-to-obsidian-note>` - reads the file,
     calls `related_to_text()`, prints matching ledger artifacts
   - `ledger-obsidian related --query "deployment pipeline"` - free-text
     variant
4. Design for future plugin use: the same `related_to_text()` function
   can back a local HTTP endpoint or be called directly by a Python-based
   Obsidian bridge. Keep it dependency-free (no web framework required).

This keeps the ledger notes in their own repo while letting Obsidian
surface related artifacts on demand - tight integration without merging.

## Key Files

- `templates/voice_dna_template.md` (new)
- `ledger/voice.py` (new)
- `ledger/maintenance.py` (extend `cmd_index`)
- `scripts/ledger` (add subcommands)
- `schema.yaml`
- `skills/notes/SKILL.md`
- `ledger/retrieval.py` (add `related_to_text()`)
- `ledger/obsidian/cli.py` (extend with `related` subcommand)
- `AGENTS.md`

## Reuse

- `ledger/parsing/frontmatter.py` for all frontmatter parsing
- `ledger/io/safe_write.py` for atomic writes
- `ledger/maintenance.py:_iter_note_files()` for index generation
- `ledger/retrieval.py:retrieve_candidates_from_index()` for the Obsidian lookup
- `ledger/io/safe_write.py:append_timeline_entry()` for logging
- `ledger/timeline.py:append_timeline_jsonl()` for direct JSONL timeline writes

## Verification

- `pytest -q --tb=short` passes
- `sheep lint` - no new errors
- `sheep index` generates `index.md` and `index.json`
- `ledger voice-dna import` + `ledger voice-dna show` round-trips correctly
- Manual: invoke `/notes` skill, confirm it reads voice profile
