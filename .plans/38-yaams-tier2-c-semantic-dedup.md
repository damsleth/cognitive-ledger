# Plan 38 — YAAMS Tier 2 integration, Phase C: semantic dedup at promotion

Status: **NOT_STARTED** (verified against code 2026-06-10).
Prerequisites already shipped:

- ✅ shipped in `f176825` — Phase 0 contract doc `docs/yaams-cogled-interface.md` (does **not** yet document `embed search`; Step 4 below adds it).
- ✅ shipped in `96e1e07` — Phase A2+B: rejection-log skipping already lives in `yaams/promote/candidates.py::generate_candidates` (`_load_rejected`, `RejectedIndex`). **Do not re-implement it**; Phase C inserts the dedup check *after* those existing rejection checks.

What does NOT exist today (verified):

- `ledger embed search` — cogled's `embed` subcommands are only `build|status|clean` (`ledger/cli.py:1508-1557`, handlers at `ledger/cli.py:312/350/374`, dispatch dict `embed_handlers` at `ledger/cli.py:~1906`).
- `ledger/semantic.py` has `semantic_search_source` (source target only) — no ledger-target search helper.
- `yaams/promote/dedup.py` does not exist; `PromotionCandidate` has no `merge_with`; no `semantic_dedup` config anywhere in yaams.

## Goal

Block YAAMS from writing inbox candidates whose statement already exists as a tier-2 cogled note. For medium-similarity matches, keep the candidate but tag it `merge_with: <rel_path>` so Phase B's triage `m` command can consume it. Coupling stays CLI-only: YAAMS shells out to `ledger embed search --json`; neither repo imports the other.

Thresholds (cosine similarity, top-1 hit):

- `>= 0.92` → **duplicate** → skip candidate entirely.
- `>= 0.80 and < 0.92` → **merge** → keep candidate, set `merge_with` + `dedup_similarity`.
- `< 0.80` → **new** → keep candidate unchanged.

Defensive failure mode everywhere: if cogled is missing, the subprocess fails/times out, the index isn't built, or JSON is malformed → verdict is `new` with a `reason`. Never block promotion on tooling.

---

## Part 1 — cogled: `ledger embed search` (repo: `/Users/damsleth/code/cognitive-ledger`)

### Step 1.1 — expose `built_at` from search payloads (`ledger/embeddings.py`)

`semantic_score_map` (around line 875) loads `index_data` via `load_semantic_index` but does not surface the index's `built_at`. Add to the **success** return dict (the one containing `"index_item_count"`):

```python
"index_built_at": str(index_data.get("built_at", "")),
```

Add `"index_built_at": ""` to the two `available: False` early returns in the same function (`missing_index`, `empty_query_vector`) so the key is always present. `semantic_search` (line ~961) passes the payload through unchanged — no edit needed there.

### Step 1.2 — `semantic_search_target` helper (`ledger/semantic.py`)

Add a module-level function alongside `semantic_search_source` (same dependency-injection style — see `semantic_search_source` and its use in `tests/test_semantic.py::test_semantic_search_source_returns_typed_result`):

```python
def semantic_search_target(
    query: str,
    *,
    target: str = "ledger",
    limit: int = 5,
    embed_backend: str = "local",
    embed_model: str | None = None,
    allow_api_on_source: bool = False,
    load_embeddings_module_fn: Callable[[], Any] | None = None,
    resolve_embed_model_fn: Callable[..., str] | None = None,
) -> dict[str, Any]:
```

Behavior:

1. Resolve embeddings module + model exactly like `semantic_search_source` does (default `load_embeddings_module_fn` to the module's cached loader, `resolve_embed_model_fn` to `resolve_embed_model`).
2. Call `embeddings.semantic_search(query=query, target=target, backend=embed_backend, model=resolved_model, limit=limit, allow_api_on_source=allow_api_on_source)`.
3. **Project** the raw payload to the stable contract shape below and return it as a plain dict (no dataclass needed). Drop `score_by_id`, `score_by_rel_path`, `abs_path`, `content_hash`, `row`, `id`, `embedding_text` from output.

Contract shape (this is what `--json` prints, verbatim, non-enveloped):

```json
{
  "target": "ledger",
  "backend": "local",
  "model": "BAAI/bge-m3",
  "available": true,
  "reason": "",
  "index_built_at": "2026-06-09T10:00:00Z",
  "index_item_count": 142,
  "results": [
    {
      "rel_path": "02_facts/fact__crayon_and_softwareone_use_viva_engage.md",
      "type": "fact",
      "scope": "work",
      "status": "active",
      "lang": "en",
      "updated": "2026-05-12T00:00:00Z",
      "cosine_similarity": 0.94
    }
  ]
}
```

**Important**: index items carry **no `title` field** (`_public_item`, `ledger/embeddings.py:458` — fields are `id, rel_path, abs_path, type, scope, status, lang, updated, content_hash, row`). The contract has no `title`; YAAMS keys on `rel_path` + `cosine_similarity` only. When `available` is false, `reason` is one of `missing_index | empty_query_vector` and `results` is `[]`.

Also add `format_embed_search_human(payload: dict[str, Any]) -> str` next to `format_source_search_human` (mirror its style: header lines `target/backend/model`, then `available: no (<reason>)` or one line per result `"- {cos:.3f} | {rel_path} | {scope}"`).

### Step 1.3 — CLI wiring (`ledger/cli.py`)

Handler, placed after `handle_embed_clean_command` (line ~374), mirroring `handle_discover_source_command` (line ~427):

```python
def handle_embed_search_command(args):
    validated_query = validate_query(args.query)
    validated_limit = validate_limit(args.limit, min_val=1, max_val=100)
    backend = resolve_embed_backend(args.embed_backend)
    payload = semantic_lib.semantic_search_target(
        validated_query,
        target=args.target,
        limit=validated_limit,
        embed_backend=backend,
        embed_model=args.embed_model,
        allow_api_on_source=args.allow_api_on_source,
        load_embeddings_module_fn=lambda: load_embeddings_module(),
        resolve_embed_model_fn=semantic_lib.resolve_embed_model,
    )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(semantic_lib.format_embed_search_human(payload))
```

`validate_query`/`validate_limit` are already imported at `ledger/cli.py:11`. Missing index is **not** an error: exit 0 with `available: false` (consumer degrades open).

Parser, added in the embed block after `embed_clean_parser` (line ~1548):

```python
embed_search_parser = embed_subparsers.add_parser("search", help="Semantic search over a built index")
embed_search_parser.add_argument("--target", choices=("ledger", "source"), default="ledger")
embed_search_parser.add_argument("--query", required=True)
embed_search_parser.add_argument("--limit", type=int, default=5)
embed_search_parser.add_argument("--embed-backend", dest="embed_backend", choices=cfg.embed_backends, default=None)
embed_search_parser.add_argument("--embed-model", dest="embed_model", default=None)
embed_search_parser.add_argument("--allow-api-on-source", action="store_true", dest="allow_api_on_source")
embed_search_parser.add_argument("--json", action="store_true", dest="json")
```

Dispatch: add `"search": handle_embed_search_command,` to the `embed_handlers` dict (line ~1906).

### Step 1.4 — contract doc (`docs/yaams-cogled-interface.md`)

Add a new artifact row to "The three seam artifacts" table (becomes four): `ledger embed search --json` | cogled → YAAMS | cogled CLI | YAAMS promote dedup | new in Phase C. Add a numbered section documenting: the exact invocation YAAMS uses (`ledger embed search --target ledger --query "<statement>" --limit 3 --json`), the JSON shape from Step 1.2 (call out: non-enveloped, no `title`, `available:false` semantics), and the threshold semantics (0.92 / 0.80) as contract v1 defaults that live in YAAMS config.

### Step 1.5 — cogled tests

`tests/test_semantic.py` (follow the existing `FakeEmbeddings` pattern at the top of the file):

- `test_semantic_search_target_projects_contract_fields` — FakeEmbeddings returns a raw payload containing `score_by_id`, `abs_path`, `embedding_text`, `index_built_at`; assert output has only contract keys, results sorted order preserved, `cosine_similarity` float.
- `test_semantic_search_target_unavailable_passthrough` — FakeEmbeddings returns `{"available": False, "reason": "missing_index", ...}`; assert `available is False`, `results == []`, `reason == "missing_index"`.
- `test_format_embed_search_human` — covers available and unavailable payloads.

`tests/test_cli.py` (handler-level, like existing handler tests using `argparse.Namespace` + monkeypatched `cli.semantic_lib`):

- `test_embed_search_json_prints_contract_payload` — monkeypatch `ledger.cli.semantic_lib.semantic_search_target` to return a canned payload; call `handle_embed_search_command(Namespace(query="x", target="ledger", limit=5, embed_backend=None, embed_model=None, allow_api_on_source=False, json=True))`; capsys-parse stdout as JSON, assert shape.
- `test_embed_search_validates_query` — empty query raises via `validate_query` (match the behavior existing discover-source tests assert).
- `test_embed_search_parser_wiring` — `cli.main(["embed", "search", "--query", "x", "--json"])` with the search function monkeypatched; assert exit code 0.

### Step 1.6 — `ledger/embeddings.py` test

In `tests/test_ledger_embeddings.py` (unittest style, real tmp index fixtures already exist there): extend an existing `semantic_score_map`/`semantic_search` test to assert `index_built_at` is present and non-empty on a freshly built index, and present-but-empty on `missing_index`.

---

## Part 2 — YAAMS (repo: `/Users/damsleth/code/yaams`)

### Step 2.1 — new module `yaams/promote/dedup.py`

```python
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class DedupVerdict:
  decision: Literal["new", "merge", "duplicate"]
  target_path: str | None
  similarity: float
  reason: str


@dataclass
class DedupConfig:
  enabled: bool = True
  duplicate_threshold: float = 0.92
  merge_threshold: float = 0.80
  embed_backend: str = "local"
  ledger_cli: str = "ledger"
  timeout_s: int = 60


def check_candidate(candidate_statement: str, config: DedupConfig) -> DedupVerdict:
  if not config.enabled:
    return DedupVerdict("new", None, 0.0, "dedup disabled")
  cmd = [
    config.ledger_cli, "embed", "search",
    "--target", "ledger",
    "--query", candidate_statement,
    "--limit", "3",
    "--json",
  ]
  if config.embed_backend:
    cmd += ["--embed-backend", config.embed_backend]
  try:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.timeout_s)
  except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
    return DedupVerdict("new", None, 0.0, f"dedup unavailable: {exc}")
  if result.returncode != 0:
    return DedupVerdict("new", None, 0.0, f"dedup failed: {result.stderr.strip()[:200]}")
  try:
    payload = json.loads(result.stdout)
  except json.JSONDecodeError as exc:
    return DedupVerdict("new", None, 0.0, f"dedup bad json: {exc}")
  if not payload.get("available") or not payload.get("results"):
    return DedupVerdict("new", None, 0.0, payload.get("reason") or "no index or no matches")
  top = payload["results"][0]
  sim = float(top.get("cosine_similarity", 0.0))
  rel_path = str(top.get("rel_path", ""))
  if sim >= config.duplicate_threshold:
    return DedupVerdict("duplicate", rel_path, sim, f"sim={sim:.2f}")
  if sim >= config.merge_threshold:
    return DedupVerdict("merge", rel_path, sim, f"sim={sim:.2f}")
  return DedupVerdict("new", None, sim, f"sim={sim:.2f}")
```

Add a per-run cache wrapper (YAAMS clusters produce near-identical statements):

```python
class DedupChecker:
  """Caches verdicts per normalized statement for one promote run."""
  def __init__(self, config: DedupConfig):
    self.config = config
    self._cache: dict[str, DedupVerdict] = {}

  def check(self, statement: str) -> DedupVerdict:
    key = " ".join(statement.lower().split())
    if key not in self._cache:
      self._cache[key] = check_candidate(statement, self.config)
    return self._cache[key]
```

### Step 2.2 — `yaams/promote/candidates.py`

- `PromotionCandidate` (line ~46): add fields `merge_with: str | None = None` and `dedup_similarity: float | None = None`.
- `PromoteConfig` (line ~80): add `dedup: DedupConfig = field(default_factory=DedupConfig)` (import from `yaams.promote.dedup`).
- `generate_candidates` (line ~88): after the post-draft entity-title rejection check (the `_rejected_by_entity_title` block) and before `candidates.append(candidate)`, insert (create `checker = DedupChecker(config.dedup)` once before the loop):

```python
verdict = checker.check(candidate.draft_statement)
if verdict.decision == "duplicate":
  if on_progress:
    on_progress(f"  skipped (duplicate of {verdict.target_path}, {verdict.reason})")
  continue
if verdict.decision == "merge":
  candidate.merge_with = verdict.target_path
  candidate.dedup_similarity = verdict.similarity
  if on_progress:
    on_progress(f"  marked merge → {verdict.target_path} ({verdict.reason})")
candidates.append(candidate)
```

Every dedup decision must reach `on_progress` (similarity + target path) — this is the audit trail for retuning thresholds.

- `store_candidates` (line ~144): extend the INSERT column list with `merge_with, dedup_similarity` and the VALUES tuple with `c.merge_with, c.dedup_similarity`.

### Step 2.3 — schema migration (`yaams/schema.py`)

In `_migrate_promotion_candidates` (line ~299), after the CREATE TABLE, add guarded ALTERs following the existing pattern used for `items.promoted_to` (check `PRAGMA table_info(promotion_candidates)` for column presence first):

```sql
ALTER TABLE promotion_candidates ADD COLUMN merge_with TEXT
ALTER TABLE promotion_candidates ADD COLUMN dedup_similarity REAL
```

### Step 2.4 — `yaams/promote/review.py`

`format_note` (line 28): when `candidate.get("merge_with")` is truthy, emit two extra frontmatter lines after the `yaams_source_item_ids` block:

```
merge_with: <rel_path>
dedup_similarity: <float, 2 decimals>
```

Phase B's triage `m` command consumes `merge_with:`; review.py only writes the field. `render_candidate` (line 99): show `merge → <rel_path> (sim 0.84)` when present, so the human reviewer sees it.

### Step 2.5 — config plumbing (`yaams/cli/promote.py`, `config.yaml.example`, `yaams/_default_config.yaml`)

In `promote_generate` (line ~107) where `pcfg = PromoteConfig(...)` is built (line ~140), read `promote_cfg_raw.get("semantic_dedup") or {}` and construct:

```python
dedup=DedupConfig(
  enabled=bool(sd.get("enabled", True)),
  duplicate_threshold=float(sd.get("duplicate_threshold", 0.92)),
  merge_threshold=float(sd.get("merge_threshold", 0.80)),
  embed_backend=str(sd.get("embed_backend", "local")),
  ledger_cli=str(sd.get("ledger_cli", "ledger")),
  timeout_s=int(sd.get("timeout_s", 60)),
)
```

Add the `promote.semantic_dedup` block with these defaults + comments to `config.yaml.example` and `yaams/_default_config.yaml`.

Staleness warning: in `promote_generate`, before generation, if dedup enabled and the resolved `note_index_path` exists, compare its mtime against now; if older than 7 days, `click.echo` a warning naming `ledger sleep index` (and `ledger embed build --target ledger --backend local`) as the fix. Warning only — never an error.

### Step 2.6 — YAAMS tests (`tests/test_promote_dedup.py`, new file)

Mock `subprocess.run` (monkeypatch `yaams.promote.dedup.subprocess.run`) — no real cogled needed:

- `test_check_candidate_returns_new_below_merge_threshold` (sim 0.55)
- `test_check_candidate_returns_merge_in_band` (sim 0.84 → decision merge, target_path set)
- `test_check_candidate_returns_duplicate_above_threshold` (sim 0.94)
- `test_check_candidate_handles_ledger_missing` (`FileNotFoundError` → new, reason startswith "dedup unavailable")
- `test_check_candidate_handles_index_missing` (`available: false, reason: missing_index` → new)
- `test_check_candidate_handles_nonzero_exit` (returncode 2 → new, reason has stderr)
- `test_check_candidate_handles_timeout` (`subprocess.TimeoutExpired` → new)
- `test_check_candidate_handles_bad_json` (stdout `"not json"` → new)
- `test_check_candidate_disabled_returns_new` (enabled=False, subprocess.run must NOT be called)
- `test_dedup_checker_caches_by_normalized_statement` (two whitespace-variant statements → one subprocess call)
- `test_generate_candidates_skips_duplicates` and `test_generate_candidates_marks_merges` — follow the fixture/mocking style of `tests/test_promote_rejection.py` (fake adapter, in-memory sqlite via `init_schema`), monkeypatching `check_candidate`/`DedupChecker.check`.
- `test_format_note_writes_merge_with_field` — in `tests/test_promote_contract.py` or the new file: `format_note({... "merge_with": "02_facts/x.md", "dedup_similarity": 0.84})` contains `merge_with: 02_facts/x.md`; absent when key missing.

---

## Acceptance criteria

1. Cogled: `cd /Users/damsleth/code/cognitive-ledger && python -m pytest tests/test_semantic.py tests/test_cli.py tests/test_ledger_embeddings.py` passes.
2. `ledger embed search --target ledger --query "test" --json` on a machine with a built ledger index prints the contract JSON (keys: `target, backend, model, available, reason, index_built_at, index_item_count, results`); with no index, prints `available: false`, `reason: missing_index`, exits 0.
3. `ledger embed --help` lists `search` next to `build|status|clean`; `ledger embed search` without `--query` exits non-zero with argparse error.
4. `docs/yaams-cogled-interface.md` documents the `embed search` artifact and JSON shape (no `title` field).
5. YAAMS: `cd /Users/damsleth/code/yaams && python -m pytest tests/test_promote_dedup.py tests/test_promote_rejection.py tests/test_promote_contract.py` passes.
6. With cogled uninstalled/missing from PATH, `yaams promote generate --dry-run` still completes; progress log shows `dedup unavailable` reasons, no candidates blocked.
7. Dry-run output logs every dedup decision with similarity, target path, and action (`new|merge|duplicate`).
8. Manual end-to-end (optional, real corpus): build ledger index, add a tier-2 note restating a known tier-1 cluster, run `yaams promote generate` — that cluster's candidate is skipped (duplicate) or carries `merge_with`.

## Rollback

Set `promote.semantic_dedup.enabled: false` in yaams config → reverts to today's title/index-text dedup (`_is_covered`) plus rejection-log skipping. Cogled's `embed search` is additive and needs no rollback.

## Effort

- Cogled `embed search` + helper + tests + contract doc: ~1 day
- YAAMS `dedup.py` + candidates/schema/review/config wiring + tests: ~1–1.5 days

## Risks

- Thresholds wrong → legitimate candidates suppressed. Mitigation: every decision logged with score + path (Step 2.2); retune in config without code changes.
- Stale ledger index → dedup misses recent notes. Mitigation: staleness warning (Step 2.5) naming the rebuild commands.
- Subprocess overhead per candidate. Mitigation: `DedupChecker` cache; typical promote runs draft <20 candidates.
