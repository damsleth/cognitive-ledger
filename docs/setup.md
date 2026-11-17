# Setup: hooks, scheduling, Obsidian drop-in

## Recommended setup

**Claude Code hooks** (`.claude/settings.json`):
```json
{
  "hooks": {
    "SessionStart": [
      {"type": "command", "command": "bash scripts/hooks/session_start.sh"}
    ],
    "Notification": [
      {"type": "command", "command": "bash scripts/hooks/session_end_capture.sh"}
    ]
  }
}
```

**Codex** (`AGENTS.md` in repo root already covers this).

**Automated maintenance:** use the owner's configured scheduler and current
scheduling preferences (for example, `takt`). Schedule the following command
daily; inspect the scheduler's help for its registration syntax:
```
/path/to/cognitive-ledger/scripts/sheep-auto.sh
```

**Troubleshooting:**
- If hooks don't fire: check that scripts are executable (`chmod +x scripts/hooks/*.sh`)
- If venv is missing: run `./scripts/setup-venv.sh`
- If session_start is slow: ensure `.venv` exists (avoids pip install on each run)
- If capture creates too many inbox items: review and triage with `ledger inbox triage`

## Session Lifecycle Hooks

Hook scripts under `scripts/hooks/` automate common session patterns:

| Hook               | Script                          | Purpose                                      |
| ------------------ | ------------------------------- | -------------------------------------------- |
| **session-start**  | `scripts/hooks/session_start.sh` | Load boot context: identity + loops + status |
| **post-write**     | `scripts/hooks/post_write.sh`   | Append timeline entry after note operations  |
| **session-end**    | `scripts/hooks/session_end.sh`  | Flush signals, report session activity       |

Manual invocation: `bash scripts/hooks/session_start.sh`

For Claude Code integration, configure in `.claude/settings.json`:
```jsonc
{
  "hooks": {
    "SessionStart": [{"type": "command", "command": "bash scripts/hooks/session_start.sh"}]
  }
}
```

## Obsidian Drop-In

```bash
pipx install cognitive-ledger
ledger import obsidian init --vault /path/to/obsidian-vault
ledger import obsidian import --vault /path/to/obsidian-vault
ledger import obsidian bootstrap --vault /path/to/obsidian-vault
ledger import obsidian watch --vault /path/to/obsidian-vault
ledger import obsidian queue sync --vault /path/to/obsidian-vault
ledger import obsidian doctor --vault /path/to/obsidian-vault
ledger import obsidian daemon start|status|stop --vault /path/to/vault   # macOS
```

The drop-in writes a **standalone ledger inside the vault**
(`<vault>/cognitive-ledger/notes/`), not into `ledger_notes_dir`. It is for
vaults with no separate ledger. Where a ledger already exists and YAAMS indexes
the vault, do not run it against that vault — it would start a second,
parallel ledger.
