# Plan 18: Fix TOCTOU Race in TUI NoteWriter add_section/add_checkbox

## Problem

`NoteWriter.update_frontmatter()` correctly holds `FileLock(note.path)` across
the full read-modify-write cycle. But `add_section()` and `add_checkbox()`
read the file first without a lock, then acquire the lock only for the final
write via `safe_write_text(..., use_lock=True)`.

Any external save (editor, sync tool, another process) between the unlocked
read and the locked write will be silently overwritten.

## Priority

P2 - Silent data loss during concurrent edits.

## Plan

### 1. Fix add_section()

File: `tui/services/note_writer.py`, lines 102-122.

Current pattern (buggy):
```python
def add_section(self, note, section_name, content):
    file_content = note.path.read_text(encoding="utf-8")  # read WITHOUT lock
    # ... modify ...
    safe_write_text(note.path, file_content, use_lock=True)  # write WITH lock
```

Fix - wrap entire read+write in a single lock:
```python
def add_section(self, note, section_name, content):
    with FileLock(note.path):
        file_content = note.path.read_text(encoding="utf-8")  # read under lock
        # ... modify ...
        safe_write_text(note.path, file_content, use_lock=False)  # lock already held
```

### 2. Fix add_checkbox()

File: `tui/services/note_writer.py`, lines 124-152.

Same pattern as add_section(). Wrap the read+write in `FileLock(note.path)`
and pass `use_lock=False` to `safe_write_text`.

### 3. Add regression test

Create a test that:
1. Writes a note
2. Patches `safe_write_text` to assert `use_lock=False` when called from
   within an already-held lock context
3. Calls `add_section()` and `add_checkbox()`
4. Verifies the lock was held during the entire read+write window

Alternatively, a simpler test: use a threading approach where a second thread
modifies the file between read and write, and verify the modification is not
silently lost.

## Key Files

- `tui/services/note_writer.py` (add_section lines 102-122, add_checkbox lines 124-152)
- `tests/test_note_writer_locking.py` (new)

## Verification

- `pytest -q --tb=short` passes
- The three NoteWriter write methods (update_frontmatter, add_section,
  add_checkbox) all follow the same lock-around-read-write pattern
