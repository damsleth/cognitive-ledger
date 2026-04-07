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
     4. Install skill symlinks (see safety note below)
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
4. **Fix `install-skill.sh` safety** (prerequisite): The current installer
   unconditionally `rm -rf`s existing skill directories before recreating
   symlinks (line 24-25). This destroys user customizations. Fix:
   - If target is already a symlink pointing to the correct source: skip
   - If target is a symlink pointing elsewhere: warn and ask
   - If target is a real directory (user customizations): warn and skip,
     suggest `--force` flag
   - Only `rm -rf` when `--force` is passed or target doesn't exist
5. **Add `ledger` console script to `pyproject.toml`**: Currently only
   `ledger-obsidian` is exposed. Add:
   ```toml
   [project.scripts]
   ledger = "ledger.cli:main"
   ledger-obsidian = "ledger.obsidian.cli:main"
   ```
   This requires extracting the CLI logic from `scripts/ledger` into a
   proper `ledger/cli.py` module. Until then, `ledger init` only works
   from the repo root via `./scripts/ledger init`.
6. **Ship templates and skill assets as package data**: The wheel currently
   only packages the `ledger` Python package (`pyproject.toml` line 29).
   A pip-installed `ledger init` would not find `templates/` or
   `skills/install-skill.sh`. Fix with one of:
   - **(a) Package data**: Add `templates/` under `ledger/` (e.g.
     `ledger/templates/`) and declare it in `pyproject.toml`:
     ```toml
     [tool.hatch.build.targets.wheel]
     packages = ["ledger"]
     [tool.hatch.build.targets.wheel.force-include]
     "templates" = "ledger/templates"
     ```
     Then `init.py` uses `importlib.resources` to locate them.
   - **(b) Code-generated defaults**: `init.py` generates minimal template
     content inline (frontmatter stubs) so no external files are needed.
   Option (b) is simpler and avoids shipping files that users will
   customize anyway. The generated defaults should match the current
   templates but can omit the verbose examples. `install-skill.sh` is
   only needed for repo-local setups and should not be in the wheel.

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
   - Install: `pip install cognitive-ledger`
   - Init from repo root: `./scripts/ledger init`
     (or `ledger init` once the console script is wired up)
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
