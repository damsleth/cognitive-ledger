# Session Lifecycle Hooks

## Problem

The ledger is purely reactive — agents decide *during* conversation whether to
read or write notes. There is no automation for common patterns like:

- Loading relevant open loops when a session starts
- Persisting unwritten artifacts when a session ends
- Triggering consolidation after a burst of note activity
- Pre-loading identity/context profiles at boot

PAI solves this with 8 lifecycle hooks. We don't need all of them, but
**session-start** and **session-end** hooks would eliminate manual boot
sequences and reduce forgotten artifacts.

Claude Code already supports a hooks system (configurable in
`~/.claude/settings.json` or `.claude/settings.json`) that fires shell commands
on events like `PreToolUse`, `PostToolUse`, `Notification`, etc. We should
build *ledger-specific hook scripts* that integrate with this existing
infrastructure.

## Design

### Hook events and their purpose

| Hook              | Trigger                        | Action                                               |
| ----------------- | ------------------------------ | ---------------------------------------------------- |
| **session-start** | Claude Code session begins     | Load context profile + open loops + identity notes   |
| **session-end**   | Claude Code session ends       | Flush pending signals, update timeline               |
| **post-write**    | After any note is created/updated | Append timeline, update indices if stale          |
| **activity-burst**| N notes changed in one session | Suggest consolidation (sheep sleep)                  |

### Implementation approach

We build thin shell scripts under `scripts/hooks/` that call into the existing
`ledger` Python library. Claude Code's hook system invokes these scripts.

```
scripts/hooks/
  session_start.sh    # boot context loader
  session_end.sh      # signal flusher + timeline updater
  post_write.sh       # timeline append + index staleness check
```

### Hook 1: session-start

**Goal**: Give the agent a compact, relevant context window on boot.

Script flow:
1. Run `scripts/ledger query --scope all --limit 5 --retrieval-mode progressive_disclosure`
   with a synthetic "session start" query (or just load the prebuilt context profile)
2. Load all identity notes (`notes/01_identity/id__*.md`) — these are small
3. Load open loops with status `open` or `blocked` (compact list via `scripts/ledger loops`)
4. Check time since last consolidation (`scripts/sheep status`); if overdue, emit a reminder
5. Output a combined boot context to stdout (Claude Code can consume this)

Integration with Claude Code hooks:

```jsonc
// .claude/settings.json (project-level)
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "scripts/hooks/session_start.sh"
      }
    ]
  }
}
```

**Alternative (AGENTS.md boot block)**: If Claude Code hooks don't support
`SessionStart` as a first-class event, we can encode this as the boot sequence
in AGENTS.md (which is already partially done). The script still helps by
generating the boot payload so the agent can `cat` it rather than running
multiple commands.

### Hook 2: session-end

**Goal**: Capture any signals and ensure timeline is current.

Script flow:
1. Check if `notes/08_indices/signals.jsonl` has unflushed entries → flush
2. Append session-end marker to timeline
3. If >5 notes were modified this session (check git diff), emit reminder
   to consider consolidation

```jsonc
{
  "hooks": {
    "Notification": [
      {
        "type": "command",
        "command": "scripts/hooks/session_end.sh",
        "when": "notification.type == 'session_end'"
      }
    ]
  }
}
```

> Note: Claude Code may not have a dedicated session-end event. Fallback:
> the agent manually runs the script, or we attach to a `Stop` hook if
> available. This hook is lower priority than session-start.

### Hook 3: post-write

**Goal**: Keep timeline and indices in sync automatically.

This is the most useful hook in practice — currently agents must remember to
append to timeline.md after every note operation. Automating this eliminates
a common source of drift.

Script flow:
1. Accept args: `action` (created|updated|archived|deleted), `path`, `description`
2. Append formatted line to `notes/08_indices/timeline.md`
3. Check if note index is stale (>10 notes changed since last rebuild)
4. If stale, queue index rebuild (non-blocking)

This could hook into `PostToolUse` for Write/Edit operations on `notes/` paths:

```jsonc
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "scripts/hooks/post_write.sh \"$TOOL_NAME\" \"$FILE_PATH\"",
        "when": "tool_name in ['Write', 'Edit'] && file_path.startsWith('notes/')"
      }
    ]
  }
}
```

### Hook 4: activity-burst (deferred)

Low priority. Could be a simple counter in post-write that triggers when
threshold is reached. Defer to v2.

## Plan

### Step 1: Create hook scripts directory and session-start script

1. Create `scripts/hooks/` directory
2. Write `scripts/hooks/session_start.sh`:
   - Source the Python venv
   - Call `scripts/ledger context --format boot` (new subcommand, see below)
   - Output compact boot payload to stdout
3. Add `context` subcommand to `scripts/ledger` that:
   - Loads identity notes (01_identity/)
   - Loads open/blocked loops (compact format)
   - Loads context profile summary
   - Loads sheep status (time since last sleep)
   - Outputs combined markdown to stdout
4. Make scripts executable

### Step 2: Create post-write hook script

1. Write `scripts/hooks/post_write.sh`:
   - Parse args (action, path, description)
   - Append to timeline.md with ISO 8601 timestamp
   - Check index staleness (compare note mtimes vs index mtime)
   - Exit 0 always (hooks should not block agent)
2. Add `--check-staleness` flag to `scripts/ledger` for index freshness check

### Step 3: Create session-end hook script

1. Write `scripts/hooks/session_end.sh`:
   - Flush any pending signals (see plan 12)
   - Append session-end marker to timeline
   - Report count of notes modified this session (git diff --name-only)
2. Keep it lightweight — this runs on exit and shouldn't delay shutdown

### Step 4: Claude Code hook configuration

1. Create `.claude/settings.json` with hook definitions
2. Document which hooks are available and what they do
3. Test that hooks fire correctly in Claude Code sessions
4. Add fallback documentation in AGENTS.md for non-Claude-Code agents
   (manual boot sequence remains, but now it's just `cat $(scripts/hooks/session_start.sh)`)

### Step 5: Update AGENTS.md and SKILL.md

1. Update boot sequence in AGENTS.md to reference the session-start hook
2. Document the hook system in a new "Hooks" section
3. Update SKILL.md to mention automatic timeline management via post-write hook
4. Remove manual timeline append instructions if post-write hook is reliable

### Step 6: Integrate with identity layer (depends on plan 10)

1. session-start hook should load identity notes once plan 10 lands
2. Context profile builder should include identity summary

## Verification

```bash
bash scripts/hooks/session_start.sh              # outputs boot context
bash scripts/hooks/post_write.sh created notes/02_facts/fact__test.md "test note"
tail -1 notes/08_indices/timeline.md              # verify append
bash scripts/hooks/session_end.sh                 # clean exit
./.venv/bin/pytest tests/ -q                      # nothing broken
```

## Effort

~2 sessions. The scripts are thin wrappers around existing functionality.
The `ledger context` subcommand is the main new code (~80 lines).

## Risks

- **Claude Code hook API stability**: The hooks system may change. Mitigation:
  keep scripts standalone so they work when called manually too.
- **Performance**: session-start must be fast (<2s). Mitigation: the context
  profile is prebuilt; we just cat files, not run retrieval queries.
- **Session-end reliability**: If the session crashes, the end hook won't fire.
  Mitigation: this is nice-to-have, not critical. Signals flush on next
  session-start as fallback.
- **Hook filtering**: Claude Code's `PostToolUse` hook may not support
  fine-grained path filtering. Mitigation: the script checks the path
  internally and exits early if it's not a notes/ file.
