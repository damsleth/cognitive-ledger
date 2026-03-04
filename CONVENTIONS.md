# Cognitive Ledger Conventions

This file is intentionally minimal to avoid drift.

Canonical references:
- Machine-readable schema: `schema.yaml`
- Agent behavior and policy: `AGENTS.md`
- Command quickstart: `QUICK_REF.md`

Precedence on conflicts:
1. `schema.yaml`
2. `AGENTS.md`
3. `QUICK_REF.md`
4. `CONVENTIONS.md`

## Naming

Filename pattern:
`{prefix}{slug}.md` where `slug` is lowercase with underscores.

| Type | Prefix | Folder |
|------|--------|--------|
| fact | `fact__` | `notes/02_facts/` |
| pref | `pref__` | `notes/03_preferences/` |
| goal | `goal__` | `notes/04_goals/` |
| loop | `loop__` | `notes/05_open_loops/` |
| concept | `concept__` | `notes/06_concepts/` |

## Timeline

Timeline file:
`notes/08_indices/timeline.md`

Line format (UTC):
`{ISO timestamp} | {action} | {path} | {description}`

Actions:
`created`, `updated`, `archived`, `deleted`, `closed`, `sleep`
