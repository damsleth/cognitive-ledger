# Phase 4: Ingest Pipeline + Knowledge Compounding

## Problem

The ledger lacks Karpathy's key insight: knowledge should compound. Currently
there's no way to feed raw sources and have the LLM distill them into atomic
notes. Good query answers vanish into chat history instead of being filed back.
Cross-references aren't maintained, and contradictions go undetected.

## Plan

### 4a. Source Ingest Pipeline

**Key design decision**: The LLM does the distillation, not code. Code provides
scaffolding (what to ingest, provenance tracking). The agent reads sources and
creates notes through the normal skill workflow.

1. Create `ledger/ingest.py` (~250 lines):
   - `scan_sources(source_root)` - list source files with metadata
     (path, SHA256, modified timestamp, size)
   - `diff_manifest(manifest, scan)` - identify new/modified/deleted
     sources since last ingest
   - `prepare_ingest_context(source_path)` - read source, return
     structured context for the LLM including:
     - Source content
     - Existing related notes (from `rg` search)
     - Ingest prompt template
   - `record_ingest(source_path, derived_notes)` - update manifest,
     append timeline entries
2. Create `notes/08_indices/source_manifest.json`:
   ```json
   [{"path": "rel/path.md", "sha256": "...", "ingested_at": "ISO",
     "derived_notes": ["notes/02_facts/fact__x.md"]}]
   ```
3. Add `ingest` subcommand to `scripts/ledger`:
   - `ledger ingest scan` - show new/changed sources
   - `ledger ingest diff` - detailed diff against manifest
   - `ledger ingest record <source> <note1> [note2...]` - record provenance
4. Create `templates/ingest_prompt_template.md` - the prompt agents use
   to distill a source into atomic notes
5. Update SKILL.md with Ingest section:
   - "If the user says 'ingest this', 'process this article/meeting/doc':
     run `ledger ingest scan`, read the source, create 3-8 atomic notes,
     run `ledger ingest record`"

### 4b. Answer Filing (Knowledge Compounding)

Good query answers should persist as notes.

1. Add to SKILL.md "Answer Filing" policy:
   - After synthesizing a query that drew from 2+ notes AND produced
     new insight not in any single source:
   - Create a `concept__` or `fact__` note capturing the synthesis
   - Tag it `synthesized`, link to all source notes
   - Set `source: assistant`, `confidence: 0.8`
2. Add lint check in `ledger/maintenance.py`:
   - Notes tagged `synthesized` must have at least 1 outgoing link
3. Update `schema.yaml` - add `synthesized` to recommended tags list

### 4c. Cross-Reference Maintenance

Detect orphans, broken links, and contradictions.

1. Extend `ledger/maintenance.py`:
   - `_generate_links_index()` - build `notes/08_indices/links.json`
     mapping each note to its outgoing/incoming links
   - Lint: orphan notes (0 total links) - warning, not error
   - Lint: broken links (reference non-existent notes) - error
   - Lint: potential contradictions (notes with overlapping tags but
     conflicting confidence values) - flag as new open loop
2. Add `links` subcommand to `scripts/ledger`:
   - `ledger links` - show full link graph summary
   - `ledger links <note-path>` - show links for specific note
3. Call `_generate_links_index()` from `cmd_index()`

## Key Files

- `ledger/ingest.py` (new)
- `templates/ingest_prompt_template.md` (new)
- `notes/08_indices/source_manifest.json` (new, generated)
- `ledger/maintenance.py` (extend lint + index)
- `scripts/ledger` (add `ingest`, `links` subcommands)
- `skills/notes/SKILL.md` (add Ingest + Answer Filing sections)
- `schema.yaml` (add `synthesized` tag, update recommended tags)

## Reuse

- `ledger/parsing/links.py` for extracting wiki/markdown links
- `ledger/parsing/frontmatter.py` for reading note metadata
- `ledger/maintenance.py:_iter_note_files()` for traversal
- `ledger/retrieval.py` for finding related notes during ingest
- `ledger/timeline.py` for all provenance logging
- `ledger/io/safe_write.py` for atomic writes

## Verification

- `pytest -q --tb=short` passes
- Create test source files, run `ledger ingest scan` - verify detection
- After manual ingest, run `ledger ingest record` - verify manifest
- Create a synthesized note without links, run `sheep lint` - verify warning
- Run `sheep index` - verify `links.json` generated
- Run `ledger links` - verify graph output
