# Agent Instructions (Cognitive Ledger)

A file-based memory system: atomic Markdown notes with YAML frontmatter, plus
the `ledger` CLI that retrieves, lints and consolidates them. Goal: **turn
verified context into curated, durable memory that agents can trust across
sessions**, readable by any agent (Claude Code, Codex, ...) with no chat context.

Detail lives behind these pointers; read one when its trigger applies:

| Read | When |
|---|---|
| `schema.yaml`, `templates/` | writing a note type you have not written before; adding a field or type |
| `docs/retrieval.md` | changing or tuning retrieval, eval cases, signals, bitemporal `--as-of`, the stale-index warning, the usage log |
| `docs/maintenance.md` | running or changing sleep, lint, lock reaping, link proposals, the contradiction scan |
| `docs/setup.md` | wiring hooks or the scheduler; the Obsidian drop-in |
| `docs/privacy.md`, `docs/trust-boundaries.md` | anything that sends note text out of the corpus (embeddings API, LLM prompts, web, JSON output) |
| `memcore/README.md` | touching `memcore/` or a JSON seam yaams consumes |

## Boot

```bash
LEDGER_NOTES_DIR="$(ledger paths --field ledger_notes_dir)"   # physical corpus; logical refs stay notes/...
ledger context --format boot                                  # identity + open loops + status + write policy
tail -20 "$LEDGER_NOTES_DIR/08_indices/timeline.md"           # recent changes
ledger query "<topic>" --scope all --limit 8                  # ranked retrieval
rg "<keyword>" "$LEDGER_NOTES_DIR" -n                         # exact search
```

`context.md` is the boot summary. `08_indices/index.md` / `index.json` are lookup
tables for deeper searches; they are too large to load whole. Identity notes
(`01_identity/`, at most 6, each with an `identity_type`) are always relevant:
load them early. If `01_identity/id__voice_dna.md` exists, write notes in that voice.

## Golden rules

1. **Summaries, never transcripts.** Raw chat logs are prohibited; distil into atomic notes.
2. **Facts come from the user or a tool.** Anything you infer is `source: inferred` with `confidence < 0.7`.
3. **One idea per file**, connected by `[[wikilinks]]`.
4. **Search before you write** (`rg "<topic>" "$LEDGER_NOTES_DIR" -l`). Update or link an existing note over creating a near-duplicate.
5. **Append before overwrite.** Bump `updated` on every content edit. A replaced fact goes through `supersede()` (`ledger/bitemporal.py`), which closes `valid_to`, sets `superseded_by` and moves the old note to `09_archive/`.
6. **The timeline is appended through `append_timeline_entry`.** `timeline.jsonl` is the source of truth; `timeline.md` is generated.
7. **Forget means hard delete.** When the user asks to delete or forget a note: remove the file, fix links to it, append a `deleted` timeline event. This is the one case that skips the archive.
8. **Respect scope and sensitivity.** Store personal detail when it is durable and useful; ask when something is unusually sensitive or ambiguous.

## When to write

Persist only what is **durable and reusable**: a decision, a stable preference,
a correction, a long-lived goal or constraint, a new concept, an open loop that
spans sessions, or an identity change (mission, belief, heuristic, narrative →
`01_identity/`). When none applies, write nothing: noise kills retrieval.

"Remember this" / "hold that thought": persist the smallest artifact that
preserves the thread. To resume later, write an open loop with a clear next
action and an exit condition, so another agent can pick it up from 1–3 notes.

Write policy comes from `ledger context --format boot` (`write_policy.mode`:
`auto-write`, `silent-write`, `ask-to-write`; default auto-write + silent,
asking only for ambiguous items). After writing, report one line per created
or updated note (path + what changed); paste note contents only on request.

## Writing a note

```
1. SEARCH    rg "<topic>" "$LEDGER_NOTES_DIR" -l
2. PATH      notes/{folder}/{prefix}{slug}.md     slug: lowercase, underscores
3. WRITE     frontmatter + body, from the matching file in templates/
4. TIMELINE  .venv/bin/python -c "from ledger.embeddings import append_timeline_entry; append_timeline_entry('created', '{path}', '{desc}')"
5. VERIFY    git diff
```

`ledger notes add --type <type> ...` does steps 2–4 for atomic notes. Timeline
actions: created, updated, archived, deleted, closed, sleep, reviewed, linked.

| Type | Prefix | Folder | Holds |
|---|---|---|---|
| capture | `note__` | `00_inbox/` | untyped capture; triaged or retired to the archive |
| identity | `id__` | `01_identity/` | mission, beliefs, models, strategies, narratives, voice |
| fact | `fact__` | `02_facts/` | stable truths and decisions |
| preference | `pref__` | `03_preferences/` | preferences, styles, policies |
| goal | `goal__` | `04_goals/` | long-lived objectives |
| open loop | `loop__` | `05_open_loops/` | unresolved items with a `status` and a `## Next action` checkbox |
| concept | `concept__` | `06_concepts/` | definitions, frameworks, mental models |
| project | `project__` | `07_projects/` | hub: Goal, State, Decisions, append-only Log |
| (generated) | | `08_indices/` | timeline, indexes, signals, eval cases, import state |
| (any) | | `09_archive/` | superseded notes; kept forever |
| person | `person__` | `10_people/` | hub: Who, Working with them, append-only Log |

Frontmatter (full spec in `schema.yaml`):

| Field | Format | Notes |
|---|---|---|
| created, updated | ISO 8601 UTC | `2026-01-20T12:00:00Z`; bump `updated` on every content edit |
| tags | list | lowercase, no spaces, at most 10 |
| confidence | 0.0–1.0 | below 0.7 = hypothesis |
| source | enum | user, tool, assistant, inferred |
| scope | enum | home, work, dev, personal, life (alias), meta |
| lang | enum | en, no, mixed |
| status | enum | **loops only**: open, closed, blocked, snoozed |
| identity_type | enum | **identity only**: mission, beliefs, models, strategies, narratives, voice |
| valid_from, valid_to | ISO 8601 UTC | *optional* valid-time; `valid_to: null` = still true |
| superseded_by, supersedes | path / list | *optional*; written by `supersede()` |
| via, origin, external_id | enum / string | *optional* ingest channel (claude-memory, obsidian, yaams, manual), upstream locator, idempotency key |
| provenance | enum | *optional*: explicit_statement, validated, corrected, observed, imported, inferred |
| attribute | slug | *optional* state slot (`employer`, `kim.residence`); two live notes on one slot are filed as a conflict |

### Note types live in one registry

`ledger/layout.py` `NOTE_LAYOUTS` is the single source for folders, prefixes,
labels, kinds, bitemporal/status/hub behaviour. Every other module derives
from it (`tests/test_layout_registry.py` enforces this). Adding a type = one
`NOTE_LAYOUTS` entry + its `schema.yaml` `types:` entry (a test checks they
agree) + a template. `CONTENT_NOTE_LAYOUTS` (everything but identity) drives
embeddings and the A/B corpus; `ATOMIC_NOTE_LAYOUTS` (no hubs) drives
`ledger notes add` and the importers.

### Hubs and unreviewed agent content

Hubs (`ledger/hubs.py`) are living documents: people and projects. Write them
through `ledger people|projects add|log|set` only: mutations take the file lock
and patch single frontmatter lines so the rest stays byte-identical. Other
notes point at a hub with `people: [slug]` / `projects: [slug]` or a wikilink;
`ledger people show` computes the Connected list at read time.

Agent-written content is marked, and agents always pass `--agent`:
`reviewed: false` frontmatter, `- YYYY-MM-DD (agent) …` log lines, and any
`source: assistant` note without `reviewed: true`. `ledger unreviewed` lists
them; `ledger unreviewed --accept <path>` sets `reviewed: true`, keeps `source`
and `updated` (a review is not a content edit) and logs timeline action
`reviewed`, which briefings and the sleep-due count ignore. An unreviewed note
has its provenance capped at `inferred`, and yaams classes it `agent` (weight
0.75) instead of `curated`.

`scripts/projects_refresh.py` (takt job `ledger-projects`) refreshes project
hubs: fixed read-only evidence commands, then codex in its read-only sandbox
returns a JSON proposal, which is validated and applied with `agent=True`. No
model gets a shell while reading ledger or yaams text.

## Signals

Log feedback with `ledger signal add --type <type> [--query <q>] [--note <path>]`
when it actually happened: a retrieved note was used in the answer
(`retrieval_hit`), a search found nothing useful (`retrieval_miss`), the user
corrected your use of a note (`correction`), confirmed it (`affirmation`), or a
note you used was outdated (`stale_flag`). Log on clear user feedback or
deliberate note use; skip trivial queries.

## Retrieval rules

Measured verdicts; the numbers and the knobs are in `docs/retrieval.md`. Change
one only with a fresh `ledger ab run` that beats it on the live cases.

- `retrieval_mode: semantic_hybrid` with `fusion: weighted_sum`. RRF measured worse.
- `prf_enabled: false`. PRF measured worse.
- `score_weight_signal: 0`. Signals are collected but stay out of ranking: no weight helped signal cases without regressing the general ones.
- `contradiction_enabled: false` and `contradiction_auto_supersede: false`. Precision is below the 0.9 floor for auto-resolution, and Norwegian is unvalidated.
- `migrate bitemporal` leaves `updated` alone: a validity back-fill is metadata, and re-dating notes cost hit@1.
- A note added or edited after the last index build ranks badly in `semantic_hybrid` until `ledger sleep index` runs. `ledger query` warns on stderr when that is the case.

**Mandatory eval gate.** A change touching `ledger/retrieval.py`, `query.py`,
`scoring.py`, `signals.py` or `config.py` runs `bash scripts/ab_gate.sh` and
records the output with the change. Exit 0 = beneficial, 3 = neutral (fine for
an off-by-default or behaviour-neutral change), **2 = regression: stop, do not
merge**, 4 = invalid setup. CI runs the same gate on push and PR, pinned to
lexical mode. The live-corpus check is:

```bash
ledger eval --cases "$(ledger paths --field ledger_notes_dir)/08_indices/retrieval_eval_cases.yaml" --k 3 --strict-cases
```

## Sleep (consolidation)

`ledger sleep status` says when a pass is due (many changes, duplicates,
fragmented concepts, loops without next actions, or a week since the last
one). A pass merges duplicates (oldest note stays canonical), promotes repeated
patterns into concepts or preferences, rebuilds indices, tightens open loops,
and surfaces conflicts as inbox conflict notes for a human. Flag identity notes
older than 90 days. Commands and their safety rules: `docs/maintenance.md`.

## memcore (shared retrieval contract)

`memcore/` is a separately installable package holding the contract shared with
the sibling **yaams** repo: the `ScoredResult` schema, RRF fusion, the
cross-encoder reranker and trust verdicts. Every public symbol and dataclass
field in it is cross-repo API: additive changes only, and coordinate a yaams
update for anything else.

- **memcore is stdlib-only.** Anything needing numpy stays in `ledger/`.
  (`memcore.rerank` imports sentence-transformers lazily, at call time.)
- Inside ledger code, import through the re-export shims
  (`ledger.retrieval_types`, `ledger.rerank`,
  `ledger.query.reciprocal_rank_fusion`, `ledger.scoring.trust_verdict`,
  `ledger.retrieval.attach_trust_verdicts`); they are the compatibility seam.
- The JSON seams yaams shells out to (`ledger embed search --json`/`--batch`,
  `ledger paths --json`) are shape-locked by `tests/test_seam_golden.py`. A
  failure there means yaams breaks: fix the shape or update both repos together.
- yaams raw items are immutable and append-only. Improve retrieval and promote
  curated notes; leave the raw tier as written.

## Learned patterns

- **Tests run as `.venv/bin/python -m pytest`.** Bare `python`, `python3` and
  `pytest` all fail here. yaams has its own venv: `~/code/yaams/.venv/bin/python -m pytest`.
- The suite points `LEDGER_NOTES_DIR` and `XDG_CONFIG_HOME` at temp dirs
  (`tests/conftest.py`), so it never reads the developer's config or corpus.
  Keep it that way: tests use fixture corpora.
- Read a file before Writing it when it may already exist (`CHANGELOG.md`,
  `pyproject.toml`, test files); the Write tool refuses otherwise.
- Corpus paths: use absolute paths from `ledger paths` (`--field` accepts
  `ledger_root`, `ledger_notes_dir`, `source_notes_dir`, `timeline_path`).
  Relative `notes/...` paths fail with `fd`/`find` because the corpus lives
  outside this repo.
- `hugr stats`, not `hugr status`.
- Workflow scripts are plain JavaScript; TypeScript annotations fail at runtime.
- The repo is public. Fixtures, docs and examples use invented people, orgs
  and note names; real corpus content and machine paths stay out of tracked files.

## Keeping docs in sync

Infrastructure changes (note types, config keys, retrieval modes, CLI verbs,
schema, hooks) get a `CHANGELOG.md` bullet under `## Unreleased`
(`### Added|Changed|Fixed|Removed`), and a `README.md` edit only when a
getting-started step, CLI example or top-level feature changes. The README
describes the present; history belongs in the changelog. Durable instructions
for agents go here or in the `docs/` file the table above points at. Routine
note operations touch neither.

## Releasing

"Cut a major/minor/patch version and publish" always means this full sequence
(`vX.Y.Z`):

1. **Bump the version** in BOTH `pyproject.toml` (`version = "..."`) and
   `ledger/__init__.py` (`__version__ = "..."`). Move the `## Unreleased`
   CHANGELOG entries under a new dated heading `## <YYYY-MM-DD> (X.Y.Z)`.
2. **Commit and push** to `main` (`release: bump to X.Y.Z`), then create and
   push an annotated tag: `git tag -a vX.Y.Z -m vX.Y.Z && git push origin main vX.Y.Z`.
   The Homebrew formula sources the GitHub tag tarball, so the tag must exist
   before computing its hash. The tag push also triggers
   `.github/workflows/release.yml`, which runs the tests, rebuilds the wheel +
   sdist with `uv build`, and creates the GitHub Release with both artifacts
   attached. That workflow does **not** publish to PyPI; step 4 does that locally.
3. **Update the homebrew-tap formula** at `~/code/homebrew-tap/Formula/cognitive-ledger.rb`:
   point `url` at `.../archive/refs/tags/vX.Y.Z.tar.gz` and set `sha256` to
   `curl -sL <url> | shasum -a 256`. Commit and push the tap repo
   (`git@github.com:damsleth/homebrew-tap`).
4. **Publish to PyPI** with uv:
   ```bash
   export UV_PUBLISH_TOKEN="$(grep -E '^UV_PUBLISH_TOKEN=' ~/code/owa-piggy/.env | cut -d= -f2- | tr -d '"')"
   rm -rf dist/ && uv build && uv publish
   ```
   (`uv build` fails on tracked absolute/external symlinks: keep dev-only
   symlinks like `scripts/ledger` untracked and gitignored.)

**Brew guard.** Run `brew install` / `upgrade` / `reinstall` for this formula
only on a machine where `where ledger` resolves to the Homebrew bin. On a
machine with the dev install (`~/.local/bin/ledger` → the editable venv), brew
would shadow it and reintroduce version skew.
