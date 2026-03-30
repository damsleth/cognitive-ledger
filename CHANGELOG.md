# Changelog

## 2026-03-30

### Fixed
- Consolidated duplicate `EvalCaseValidationError` — was defined in both `ledger/eval.py` and `ledger/errors.py` with incompatible interfaces; now single definition in `errors.py` supporting both batch and single-case usage
- Semantic retrieval and embedding paths now respect `LEDGER_ROOT_DIR` instead of deriving from script location — fixes silent correctness issues for bootstrapped external ledgers
- TUI auto-discovery checks `LEDGER_ROOT_DIR` env var before falling back to cwd or `~/cognitive-ledger`
- Test suite: removed debug print statements from conftest, fixed ambiguous capsys assertion, cleared stale candidate cache in semantic hybrid test — **all 317 tests now pass** (was 316/317)

### Added
- Opt-in query telemetry log (`LEDGER_QUERY_LOG=1`) — appends JSONL to `notes/08_indices/query_log.jsonl` with query, scope, mode, top results, latency, and candidate count

### Changed
- `scripts/ledger` slimmed from 666 to 595 lines — removed redundant delegation layer and inline constant aliases, telemetry now handled by library
- Updated dependencies: textual 0.89→8.2, watchdog 4→6, sentence-transformers 2.7→5.3, removed numpy/transformers version pins
- Consolidated 7 doc files into single improvement plan, then split remaining TODO items into individual plans in `.doc/plans/`

## 2026-03-27

### Changed
- Reorganized README to focus on getting started, `/notes` skill, and plugging into existing repos
- Consolidated `.doc/` from 8 files into 1 (refactoring_2.md)
- Created `CLAUDE.md` pointing to `AGENTS.md`
