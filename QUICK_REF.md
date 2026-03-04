# Agent Quick Reference

Machine-readable spec: `schema.yaml`. Full docs: `AGENTS.md`.

## Boot

```bash
tail -20 notes/08_indices/timeline.md          # recent changes
rg "<keyword>" notes -n                        # search content (show matches)
fd "pref__" notes/03_preferences && fd "concept__" notes/06_concepts  # search by type (portable)
```

## Python env

```bash
./scripts/setup-venv.sh  # default: one shared venv with base + dev + embeddings
./scripts/setup-venv.sh --python python3.12 --recreate  # if torch wheels are missing
./scripts/setup-venv.sh --minimal  # base-only fallback
```

Embedding compatibility stack is pinned in `<ledger-root>/scripts/setup-venv.sh`.

## Obsidian Drop-In (MVP)

```bash
pipx install cognitive-ledger
ledger-obsidian init --vault /path/to/obsidian-vault
ledger-obsidian import --vault /path/to/obsidian-vault
ledger-obsidian watch --vault /path/to/obsidian-vault
ledger-obsidian queue sync --vault /path/to/obsidian-vault
ledger-obsidian doctor --vault /path/to/obsidian-vault
ledger-obsidian daemon start --vault /path/to/obsidian-vault   # macOS
ledger-obsidian daemon status --vault /path/to/obsidian-vault  # macOS
ledger-obsidian daemon stop --vault /path/to/obsidian-vault    # macOS
```

Drop-in writes only under `cognitive-ledger/` in the vault and queues medium-confidence candidates in `cognitive-ledger/notes/00_inbox/` for Bases review.

## Should I write?

Persist only if it’s **durable** and **re-usable** (Decision / Preference / Correction / Goal / Concept / Open loop).
If none apply: don’t write. Noise kills retrieval.

## Create or update a note

1. **Search first**
   ```bash
   rg "<topic>" notes -l
   ```
2. **Create/update the right type** (atomic, one idea per file; use the right folder + prefix)
3. **Frontmatter required**: `created`, `updated`, `tags`, `confidence`, `source`, `scope`, `lang` (+ `status` for loops)
4. **No transcripts**: never store raw chat logs; summarize into atomic notes
5. **Append timeline**
   ```bash
   echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | <verb> | <path> | <why>" >> notes/08_indices/timeline.md
   ```

## Cross-agent handoff (“remember this”)

When the user says “remember this”, “hold that thought”, “store this”:
- Write the **smallest durable artifact(s)** that preserve the thread.
- If it needs follow-up, create/update an **open loop** with a clear next action + exit condition.
- Write so another agent can resume via `rg`/`fd` + reading 1–3 notes.

## Electric Sheep (sleep / consolidation)

Use when drift/duplicates build up or periodically:
```bash
./scripts/sheep status
./scripts/sheep sync --check
./scripts/sheep sync --apply
./scripts/sheep sleep
./scripts/sheep index   # if present
./scripts/sheep lint    # if present
```

## Retrieve (compact + interactive)

```bash
./scripts/ledger query "<topic>" --scope all --limit 8
./scripts/ledger query "<topic>" --scope all --limit 8 --retrieval-mode <mode>
./scripts/ledger query "<topic>" --scope all --limit 8 --retrieval-mode semantic_hybrid --embed-backend local
./scripts/ledger query "<topic>" --scope dev --bundle
./scripts/ledger discover-source "<topic>" --source-root <source-notes-root> --limit 20
./scripts/ledger discover-source "<topic>" --embed-backend openai --allow-api-on-source
./scripts/ledger embed build --target ledger --backend local --model TaylorAI/bge-micro-v2
./scripts/ledger embed status --target both
./scripts/ledger embed clean --target source
./scripts/ledger eval --cases notes/08_indices/retrieval_eval_cases.yaml --k 3
./scripts/ledger eval --cases notes/08_indices/retrieval_eval_cases.yaml --k 3 --strict-cases
./scripts/ledger eval --cases notes/08_indices/retrieval_eval_cases.yaml --k 3 --retrieval-mode <mode>

# A/B branch comparison harness (quality + latency)
./scripts/ledger_ab --baseline-ref main --candidate-ref HEAD
./scripts/ledger_ab --baseline-ref main --candidate-ref HEAD --runs 5
./scripts/ledger_ab --baseline-ref HEAD --candidate-ref HEAD --baseline-mode <mode> --candidate-mode <mode>
./scripts/ledger_ab --baseline-ref main --candidate-ref HEAD --eval-runs 7 --query-runs 5
./scripts/ledger_ab --baseline-ref main --candidate-ref HEAD --query-runs 5 --cold-query
./scripts/ledger_ab --baseline-ref main --candidate-ref feature/alias-tuning --out-dir /tmp/ledger-ab

# Exit codes
# 0 = beneficial
# 2 = regression (quality dropped)
# 3 = neutral (quality tie but latency tie-break lost)
# 4 = invalid setup (missing ref, corpus mismatch without override, etc.)

./scripts/ledger loops                 # compact list (default)
./scripts/ledger loops --interactive   # progressive disclosure (curses)
./scripts/ledger loops --verbose       # full detail per loop
./scripts/ledger notes --type <all|facts|preferences|goals|loops|concepts> --interactive
```

`<mode>` can be: `legacy`, `two_stage`, `compressed_attention`, `scope_type_prefilter`, `precomputed_index`, `progressive_disclosure`, `semantic_hybrid`.

Eval case rules:
- `retrieval_eval_cases.yaml` entries should include `id`, `query`, `scope`, and non-empty `expected_any`.
- `expected_any` paths should be repo-relative (`notes/...`) for portability.
- Use `--strict-cases` to fail fast on invalid case schema/path issues.
- Symptom: all-zero eval quality in a new worktree often means absolute expected paths from another repo root.
- A/B harness workflow and interpretation: `.doc/ab_testing.md`

## Folder map (type-first)

```
notes/02_facts/        stable truths / decisions
notes/03_preferences/  user preferences / policies
notes/04_goals/        long-lived objectives
notes/05_open_loops/   durable unresolved items (status lifecycle)
notes/06_concepts/     definitions / frameworks / mental models
notes/08_indices/      timeline, logs, import state, generated indexes
notes/09_archive/      superseded notes (do not delete)
```
