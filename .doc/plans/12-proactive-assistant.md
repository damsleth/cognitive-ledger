# Phase 3: Proactive Assistant

## Problem

The ledger is passive storage - it waits for queries. Users need the system
to actively surface what needs attention: stale open loops, approaching
deadlines, accumulated inbox items, and maintenance needs. The ledger should
motivate closing open loops and help users stay on top of their commitments.

## Plan

### 3a. Briefing Command

Build `ledger briefing` as the central awareness tool.

1. Create `ledger/briefing.py` (~200 lines):
   - `daily_briefing()` returns structured markdown:
     - **Open loops** sorted by staleness (days since last update)
     - **Nudge candidates**: loops open > 14 days, blocked > 7 days
     - **Recent changes** from timeline (last 24h)
     - **Things3 tasks** due today/upcoming (if `things-cli` available)
     - **Maintenance status** (sheep status summary)
     - **Suggested actions** ("3 loops stale > 14d - close, snooze, or update?")
   - `weekly_review()` returns extended markdown:
     - Week-over-week stats (notes created, loops opened/closed)
     - Stale notes flagged for review
     - Knowledge gaps (loops without next actions, low-confidence notes)
     - Achievements ("Closed 4 loops this week")
2. Add `briefing` subcommand to `scripts/ledger`:
   - `ledger briefing` (daily, default)
   - `ledger briefing --weekly`
3. Update SKILL.md: "If the user says 'what's on my plate', 'brief me',
   'what needs doing', 'morning', or 'status', run `ledger briefing`"

### 3b. Loop Nudging and Motivation

Extend briefing with staleness-aware nudges.

1. Add to `ledger/briefing.py`:
   - Loops open > 7 days, no updates: gentle nudge
   - Loops open > 21 days: "close, delegate, or snooze?"
   - Loops blocked > 14 days: "still blocked? what's needed?"
   - Track nudge timestamps in note frontmatter (`last_nudge` field)
     to avoid re-nudging same day
2. Update `schema.yaml` - add optional `last_nudge` field to loop type
3. Motivational framing: emphasize progress, not guilt.
   "You've closed 2 loops this week. 3 more are ready to wrap up."

### 3c. Session-Start Briefing Integration

Make the agent proactively aware at session start.

1. Extend SKILL.md Boot Sequence:
   - After reading index.md and context.md, run `ledger briefing`
   - If urgent items (stale loops, overdue tasks): mention them
   - If nothing urgent: skip (don't waste context)
2. Optionally extend `scripts/hooks/session_start.sh` to cache briefing
   output for the agent to read

### 3d. Automated Maintenance

1. Create `scripts/sheep-auto.sh` (~40 lines):
   - Check `sheep status`
   - If sleep is due: run `sheep index && sheep lint`
   - Write summary to `notes/08_indices/last_auto_maintenance.md`
2. Document cron setup in AGENTS.md:
   ```
   0 6 * * * cd ~/Code/cognitive-ledger && ./scripts/sheep-auto.sh
   ```
   Or use Claude Code `/schedule` skill for managed scheduling.

## Key Files

- `ledger/briefing.py` (new)
- `scripts/ledger` (add `briefing` subcommand)
- `scripts/sheep-auto.sh` (new)
- `skills/notes/SKILL.md` (extend boot + triggers)
- `schema.yaml` (add `last_nudge` field)
- `AGENTS.md` (cron/schedule documentation)

## Reuse

- `ledger/browse.py:load_loops()` for open loop analysis
- `ledger/timeline.py` for recent changes
- `ledger/maintenance.py` for sheep status
- `ledger/notes/__init__.py:get_notes()` for type-filtered listing
- Things3 CLI integration already documented in SKILL.md

## Verification

- `pytest -q --tb=short` passes
- Create 3-5 test loops with various ages/statuses
- Run `ledger briefing` - verify output includes nudges, stats, suggestions
- Run `ledger briefing --weekly` - verify extended review
- Run `sheep-auto.sh` - verify it checks status and runs if needed
