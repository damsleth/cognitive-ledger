# Phase 2: Passive Second-Brain Capture

## Problem

Agents only write to the ledger when the user explicitly invokes `/notes`.
Most agent interactions produce durable artifacts (decisions, patterns,
preferences) that silently vanish into chat history. The ledger should
passively capture what agents do across Claude Code, Codex, and Copilot.

## Plan

### 2a. SKILL.md Passive Capture Policy (all agents)

Add a "Session Wrap-Up" section to SKILL.md instructing agents to scan
their own actions at session end:

1. At end of any session that created/modified/deleted files:
   - Scan actions for durable artifacts (decisions, preferences, new
     concepts, unresolved items)
   - For each: search ledger first, then create/update appropriate note type
   - If multi-step task completed: file a fact note summarizing what + why
   - If work left incomplete: create/update an open loop with next action
2. This applies even when `/notes` was never explicitly invoked
3. Add "When to capture" heuristics:
   - Commit messages containing decisions or rationale
   - New architectural patterns established
   - Configuration choices made
   - Problems diagnosed and solutions applied

### 2b. Claude Code Hooks for Automatic Capture

For Claude Code, use hooks to trigger capture at session boundaries.

1. Create `scripts/hooks/session_end_capture.py` (~120 lines):
   - Read recent git log (commits since session start)
   - Extract durable artifacts from commit messages and file changes
   - Write inbox notes to `notes/00_inbox/` with `source: assistant`,
     `confidence: 0.6`
   - Append timeline entries
2. Create `scripts/hooks/session_end_capture.sh` - bash wrapper activating
   venv and calling the Python script
3. Document recommended hook config in AGENTS.md:
   ```json
   { "event": "session_end", "command": "./scripts/hooks/session_end_capture.sh" }
   ```

### 2c. Inbox Triage System

Raw passive captures need a promotion path.

1. Create `ledger/inbox.py` (~80 lines):
   - `list_inbox()` - list all `notes/00_inbox/` items
   - `triage_suggestions()` - suggest target type based on content analysis
   - `promote(path, target_type)` - move inbox item to proper folder with
     correct prefix, update timeline
2. Create `templates/inbox_template.md` - minimal frontmatter for captures
3. Add `inbox` subcommand to `scripts/ledger`:
   - `ledger inbox list`
   - `ledger inbox triage`
4. Wire into sleep: `sheep sleep` should triage non-empty inbox

## Key Files

- `skills/notes/SKILL.md` (extend)
- `scripts/hooks/session_end_capture.py` (new)
- `scripts/hooks/session_end_capture.sh` (new)
- `ledger/inbox.py` (new)
- `templates/inbox_template.md` (new)
- `scripts/ledger` (add `inbox` subcommand)
- `AGENTS.md` (hook documentation)

## Reuse

- `ledger/parsing/frontmatter.py` for parsing inbox notes
- `ledger/timeline.py` for logging promotions
- `ledger/io/safe_write.py` for atomic writes
- Existing `notes/00_inbox/` folder (already in scaffold)

## Verification

- `pytest -q --tb=short` passes
- Create test inbox items manually, run `ledger inbox triage`
- Simulate session_end hook: `echo '{}' | scripts/hooks/session_end_capture.sh`
- Verify promoted notes have correct frontmatter and appear in timeline
