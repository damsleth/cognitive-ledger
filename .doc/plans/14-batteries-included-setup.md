# Phase 5: Batteries-Included Setup

## Problem

Setting up the cognitive ledger requires multiple manual steps: creating
directories, installing skills, configuring hooks, running initial index.
A "batteries included" system should work with one command and minimal
configuration.

## Plan

### 5a. One-Command Init

1. Create `ledger/init.py` (~120 lines):
   - `init_ledger(root, voice_dna_path=None, source_root=None)`:
     1. Create notes directory structure (all folders from schema)
     2. Copy templates to `templates/` if not present
     3. Generate initial `config.yaml` with sensible defaults
     4. Run `install-skill.sh` (symlinks for Claude Code / Codex / Copilot)
     5. If `voice_dna_path` provided: import voice DNA (Phase 1a)
     6. If `source_root` provided: set in config, run initial scan
     7. Run `sheep index` to generate initial indices
     8. Print summary: what was created, next steps
2. Add `init` subcommand to `scripts/ledger`:
   ```
   ledger init [--voice-dna <path>] [--source-root <path>] [--notes-dir <path>]
   ```
3. Handle idempotency: skip steps that are already done, report what was
   skipped vs created

### 5b. Hook Configuration Documentation

1. Add "Recommended Setup" section to AGENTS.md with copy-pasteable
   `.claude/settings.json` hook config:
   - Session start: read boot context + run mini-briefing
   - Session end: passive capture (Phase 2b)
   - Scheduled: weekly maintenance + review (Phase 3d)
2. Include setup for other agents (Codex AGENTS.md, Copilot config)
3. Add troubleshooting: common issues, how to verify hooks work

### 5c. README Quick Start

1. Update project README (if exists) or create minimal one:
   - One-liner install: `pip install cognitive-ledger && ledger init`
   - 3-step quick start: init, create voice DNA, write first note
   - Link to detailed docs in AGENTS.md and .doc/plans/

## Key Files

- `ledger/init.py` (new)
- `scripts/ledger` (add `init` subcommand)
- `AGENTS.md` (add Recommended Setup section)
- `skills/install-skill.sh` (verify it works for all three agents)

## Reuse

- `ledger/voice.py` (Phase 1a) for voice-dna import
- `ledger/maintenance.py` for initial index generation
- `skills/install-skill.sh` for skill installation
- `ledger/config.py` for config generation

## Verification

- Fresh directory: `ledger init` creates full structure
- `ledger init` on existing ledger: idempotent, no data loss
- Skill symlinks created for all three agent targets
- `sheep lint` passes on freshly initialized ledger
- `sheep index` generates valid index.md
