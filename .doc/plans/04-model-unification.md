# Unify TUI and Library Note Models

## Problem

Two parallel type hierarchies:
- `ledger/notes/`: `BaseNote` with raw dict frontmatter
- `tui/models/note.py`: `Note` with structured `Frontmatter` dataclass, enums, `incoming_links`, `lint_warnings`

The TUI models are stricter and more feature-rich. Duplicated definitions drift over time.

## Plan

### Step 1: Promote TUI's structured Frontmatter to the library

1. Move the `Frontmatter` dataclass (or equivalent) from `tui/models/note.py` into `ledger/notes/__init__.py`
2. Add typed fields: `confidence`, `source`, `scope`, `status`, `tags`, `lang`, `created`, `updated`
3. Keep `BaseNote.frontmatter` as the structured type instead of raw dict
4. Add a `from_dict()` classmethod for backwards-compatible construction from parsed YAML

### Step 2: Add TUI-needed fields to BaseNote

1. Add `sections: dict[str, str]` (already present)
2. Add `outgoing_links` (already present)
3. These are already on BaseNote, so verify parity

### Step 3: Make TUI wrap via composition

```python
@dataclass
class TUINote:
    base: BaseNote
    incoming_links: list[Path]        # computed by NoteStore
    lint_warnings: list[LintWarning]  # computed by SheepRunner
```

### Step 4: Update TUI code

1. Replace direct `Note` construction with `TUINote(base=read_note(...), ...)`
2. Update all TUI widget code that accesses `note.frontmatter.confidence` etc. to go through `note.base.frontmatter.confidence`
3. Or use `@property` forwarding on TUINote to keep access patterns unchanged

### Step 5: Delete tui/models/note.py parallel definitions

1. Remove `Frontmatter`, `NoteType`, `LoopStatus` from TUI models
2. Import from `ledger.notes` instead
3. Keep only `TUINote` wrapper in TUI models

## Verification

```bash
./.venv/bin/pytest tests/tui/ -q
./.venv/bin/python -m tui  # manual smoke test: browse, filter, edit
```

## Effort

~2 sessions. The property forwarding and widget updates are the bulk of the work.
