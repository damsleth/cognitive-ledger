# First Run Setup

This is your first session with the Cognitive Ledger. Complete the steps below before proceeding with anything else.

## What to do

### 1. Populate the agent memory index

Your platform's built-in memory system (e.g. `~/.claude/projects/<project>/memory/MEMORY.md` in Claude Code) is loaded into every session automatically - even when hooks don't run. Use it as a stable structural index into the ledger so you can navigate without searching.

Read the contents of the following directories and write a structured MEMORY.md index - one section per domain, each entry pointing to a specific file:
- `notes/01_identity/`
- `notes/02_facts/`
- `notes/03_preferences/`
- `notes/05_open_loops/`
- `notes/06_concepts/`

Keep it under 150 lines. The goal is navigability, not exhaustiveness.

### 2. Mark first run complete

Edit `config.yaml` and change:
```yaml
first_run: true
```
to:
```yaml
first_run: false
```

This step is important - it stops these instructions from appearing in future sessions.

---

Once both steps are done, proceed normally.
