# Cognitive Ledger Refactoring Implementation Plan

> Generated from adversarial code review, February 2026

## Overview

This plan addresses 8 critical issues identified in the code review:
1. **Module Decomposition** — Split `scripts/ledger` monolith into focused modules
2. **Proper Import Structure** — Replace `exec_module()` with standard imports
3. **Shared Parsing Utilities** — Consolidate 3 duplicate YAML parsers into one
4. **Error Handling Strategy** — Establish consistent error handling patterns
5. **Input Validation** — Add validation boundaries at all entry points
6. **File Safety** — Implement atomic writes and file locking
7. **TUI Test Coverage** — Add tests for all TUI components
8. **Configuration System** — Replace hardcoded paths with config

---

## Phase 1: Foundation

### Step 1.1: Configuration System

- [ ] Create `ledger/` package directory structure
- [ ] Create `ledger/__init__.py`
- [ ] Create `ledger/config.py` with `LedgerConfig` dataclass
- [ ] Document all magic numbers with their rationale
- [ ] Add environment variable overrides
- [ ] Add `reset_config()` for testing
- [ ] Update `scripts/ledger` to use `get_config()`
- [ ] Run tests to verify no regression
- [ ] Commit

### Step 1.2: Shared Parsing Utilities

- [ ] Create `ledger/parsing/__init__.py`
- [ ] Create `ledger/parsing/frontmatter.py` — canonical YAML parser
- [ ] Create `ledger/parsing/sections.py` — markdown section extraction
- [ ] Create `ledger/parsing/links.py` — wiki/markdown link extraction
- [ ] Create `ledger/parsing/tokenizer.py` — text tokenization
- [ ] Update `scripts/ledger` to import from `ledger.parsing`
- [ ] Update `scripts/ledger_embeddings.py` to import from shared module
- [ ] Update `tui/services/note_parser.py` to import from shared module
- [ ] Add tests for parsing modules
- [ ] Run all tests to verify no regression
- [ ] Commit

### Step 1.3: Proper Import Structure

- [ ] Create proper package entry points in `ledger/__init__.py`
- [ ] Update `scripts/ledger` to be thin CLI wrapper
- [ ] Remove all `exec_module()` hacks
- [ ] Update test files to use standard imports
- [ ] Add `tests/conftest.py` with shared fixtures
- [ ] Run all tests to verify no regression
- [ ] Commit

---

## Phase 2: Module Decomposition

- [ ] Create `ledger/notes/` — note type definitions and models
- [ ] Create `ledger/retrieval/` — candidate building, scoring, shortlisting
- [ ] Create `ledger/embedding/` — embedding and semantic search (from ledger_embeddings.py)
- [ ] Create `ledger/eval/` — evaluation framework
- [ ] Create `ledger/cli/` — CLI command handlers
- [ ] Update all imports throughout codebase
- [ ] Run all tests to verify no regression
- [ ] Commit

---

## Phase 3: Error Handling & Validation

### Step 3.1: Error Handling

- [ ] Create `ledger/errors.py` — custom exception hierarchy
- [ ] Create `ledger/logging.py` — structured logging
- [ ] Replace bare `except Exception: continue` patterns
- [ ] Add proper logging for parse failures
- [ ] Run all tests to verify no regression
- [ ] Commit

### Step 3.2: Input Validation

- [ ] Create `ledger/validation.py` — input validators
- [ ] Add `validate_query()` — length, encoding, null bytes
- [ ] Add `validate_scope()` — enum validation
- [ ] Add `validate_limit()` — bounds checking
- [ ] Add `validate_path_within_root()` — directory traversal prevention
- [ ] Add validation at all CLI entry points
- [ ] Add validation tests
- [ ] Run all tests to verify no regression
- [ ] Commit

---

## Phase 4: File Safety

- [ ] Create `ledger/io/__init__.py`
- [ ] Create `ledger/io/safe_write.py` — atomic writes, file locking
- [ ] Add `FileLock` context manager
- [ ] Add `atomic_write()` context manager
- [ ] Update `tui/services/note_writer.py` to use safe writes
- [ ] Add file safety tests
- [ ] Run all tests to verify no regression
- [ ] Commit

---

## Phase 5: TUI Testing

- [ ] Create `tests/tui/__init__.py`
- [ ] Create `tests/tui/conftest.py` — TUI-specific fixtures
- [ ] Create `tests/tui/test_note_parser.py`
- [ ] Create `tests/tui/test_note_store.py`
- [ ] Create `tests/tui/test_note_writer.py`
- [ ] Ensure all tests pass
- [ ] Commit

---

## Phase 6: Final Integration

- [ ] Verify all tests pass
- [ ] Check for circular import issues
- [ ] Update `README.md` with new structure
- [ ] Final commit

---

## Completion Status

| Phase | Status |
|-------|--------|
| 1.1 Configuration | ✅ Complete |
| 1.2 Parsing | ✅ Complete |
| 1.3 Imports | ✅ Complete |
| 2 Notes Module | ✅ Complete |
| 3.1 Errors | ✅ Complete |
| 3.2 Validation | ✅ Complete |
| 4 File Safety | ✅ Complete |
| 5 TUI Testing | ✅ Complete |
| 6 Integration | ✅ Complete |

---

## Final Summary (Post-Refactoring)

**Tests:** 261 passing (up from 72)

**New Files Created:**
- `ledger/__init__.py` — Package entry point
- `ledger/config.py` — Centralized configuration system
- `ledger/parsing/` — Canonical parsing utilities (frontmatter, sections, links, tokenizer)
- `ledger/notes/__init__.py` — Type-safe note dataclasses
- `ledger/errors.py` — Exception hierarchy
- `ledger/validation.py` — Input validators
- `ledger/io/safe_write.py` — Atomic writes and file locking
- `tests/conftest.py` — Shared pytest fixtures
- `tests/tui_tests/` — TUI component tests (37 tests)
- `pytest.ini` — Pytest configuration
