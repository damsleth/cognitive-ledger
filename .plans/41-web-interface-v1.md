# Plan 41 - Web interface v1: read-only browser

**Status: PARTIAL — Phases 1-3 shipped; Phase 4 (graph) and Phase 5 (polish) remain.**

Goal: local, read-only web UI for browsing the corpus, replacing the deleted
Textual TUI. Zero parallel logic — every read goes through `ledger.browse`,
`ledger.query`/`ledger.cli.rank_query`, and `ledger.parsing`. Writes stay in
the `$EDITOR` CLI flow (out of scope for v1). No auth; loopback-only by default.

---

## Shipped (do not redo — verify with `git show <commit>` if unsure)

- **Phase 1 — app skeleton, browse, note detail**: ✅ shipped in `7e77a1b`
  (`feat(web): add v1 web UI (Phase 1) + uxl iteration loop`).
  `ledger/web/{server.py, routes/{browse,note}.py, services/{corpus,render}.py,
  templates/{base,browse,note}.html, static/{style.css,htmx.min.js}}`,
  `ledger web` CLI subcommand (`ledger/cli.py:1294` handler, `:1867` parser).
- **Phase 2 — search (lexical + semantic_hybrid)**: ✅ shipped in `2b085fe`.
  `routes/search.py`, `services/search.py` (`Searcher` with 60s TTL cache keyed
  `(q, mode, scope, limit)`), `templates/{search,_search_results}.html`.
- **Phase 3 — backlinks + `/healthz` + `/admin/reload` + search cache busting**:
  ✅ shipped in `5a91793`. `routes/admin.py`; `Corpus` link maps
  (`outgoing_stems`/`incoming_stems`/`broken_outgoing`); backlinks panel in
  `note.html`; reload clears `Searcher` cache and `ledger.retrieval`
  candidate cache.
- **Wikilink title rendering**: ✅ shipped in `98a309b` (bare `[[stem]]`
  renders target note's title). Released as 0.3.1 in `7634b72`.
- **Static asset provenance**: ✅ `ledger/web/static/README.md` records
  htmx 2.0.4 version/source/license.
- **Bonus (not in original plan): `/signals` dashboard** shipped in `f0e61f0`
  (`routes/signals.py`, `templates/signals.html`) and use-time signal capture
  in `c7422d4` (search-miss + note-open-from-search signals, gated on
  `config.signals_auto_capture`).
- **Private content stripping for rendered bodies**: ✅
  `services/render.py:103` applies `ledger.parsing.strip_private_tags` before
  markdown rendering.

## Current architecture facts (verified 2026-06-10 — rely on these, no research needed)

- App factory: `create_app(*, config=None, corpus=None)` in
  `ledger/web/server.py`. Sets `app.state.config`, `app.state.corpus`,
  `app.state.templates` (Jinja2, dir `ledger/web/templates/`), registers a
  `type_label` Jinja filter, mounts `/static`, then `include_router`s
  `admin, browse, note, search, signals` routers (all `APIRouter()` instances
  named `router` in their modules, imported locally inside `create_app`).
- `Corpus` (`ledger/web/services/corpus.py`): single source of truth.
  Constructor builds `_stem_index: dict[str, Path]` and link maps via
  `_rebuild_link_maps()` (reads every note body, `parse_frontmatter_text`,
  `ledger.parsing.links.extract_links`). Public API:
  `note_types() -> list[NoteTypeInfo]`, `note_type(key)`,
  `list_by_type(type, *, loop_status=None)`, `recent(limit)`,
  `get_by_stem(stem) -> BrowseItem | None`, `stem_exists(stem)`,
  `outgoing_stems/incoming_stems/broken_outgoing(stem) -> list[str]`,
  `link_titles(stems) -> list[tuple[stem, title]]`, `reload()`.
  `_infer_type(path) -> str` maps a path to a note-type key (plural, e.g.
  `"facts"`, `"loops"`).
- `BrowseItem` (`ledger/browse.py:23`): `path, frontmatter (dict), body, type,
  title, statement, question, why, next_action, context, implications, links`.
  Loop status lives in `item.frontmatter.get("status")` — there is no top-level
  status field.
- `note_index.json` (built by `ledger.retrieval.build_note_index`) has keys
  `{version, built, entries, inverted, build_ms}`. **It does NOT contain
  `outgoing_links`** — the original plan was wrong about this. Graph data must
  come from `Corpus`'s link maps, which is also what backlinks use.
- Privacy rule: `ledger/parsing/privacy.py:strip_private_tags(text) -> str`
  strips `<private>...</private>` fences (multiline, nested, case-insensitive).
  Anything served by the web layer must pass through it.
- Templates extend `base.html`, which provides blocks `title`, `head_extra`,
  `crumbs`, `content`; sidebar expects context vars `types`
  (list[NoteTypeInfo]), `active_type` (key or `None`/`"all"`/`"search"`/
  `"signals"`), `active_label`. Footer is currently a single hardcoded
  `<footer class="statusbar">` line.
- Tests: `tests/web/` with `conftest.py` fixtures `web_ledger_root` (tmp ledger
  with `fact__sample.md` → links to `fact__other_one.md` + broken
  `[[fact__does_not_exist]]`, `loop__open_test.md`, `loop__closed_test.md`) and
  `client` (FastAPI `TestClient`). `pytest.importorskip("fastapi")` guards the
  package. Run with `pytest tests/web/ -q`.
- `/healthz` returns `{ok, notes_loaded, embeddings_enabled, index_built_at}`.
  `/admin/reload` (POST, loopback-only) returns
  `{ok, notes_loaded, search_cache_cleared}` and calls `corpus.reload()` +
  `searcher.invalidate()` + `clear_candidate_cache()`.

---

## Phase 4 — Graph view (NEXT SLICE)

Force-directed graph of the whole corpus. Nodes = notes; edges = resolved
wikilinks (from `Corpus._outgoing`). Client-side rendering on `<canvas>` with
vendored d3; data served as one JSON blob.

### 4.1 Extend `Corpus` with graph metadata

File: `ledger/web/services/corpus.py`.

1. In `_rebuild_link_maps()`, before calling `extract_links(body)`, apply
   `strip_private_tags(body)` (import from `ledger.parsing`). This enforces the
   privacy rule for both the graph AND the existing backlinks panel: wikilinks
   inside `<private>` fences must not be served. (Frontmatter is already split
   off via `parse_frontmatter_text` and is not private-fenced.)
2. While walking notes in `_rebuild_link_maps()`, also populate two new
   instance dicts (initialise empty in `__init__` alongside the others):
   - `self._titles: dict[str, str]` — first `# ` H1 of the stripped body,
     regex `re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)`, fallback to the stem.
   - `self._meta: dict[str, dict[str, str]]` — per stem:
     `{"scope": str(frontmatter.get("scope", "") or ""), "status": str(frontmatter.get("status", "") or "")}`.
3. Add a public method:

   ```python
   def graph(self) -> dict[str, Any]:
       """Nodes + links payload for /graph/data.json.

       Excludes broken links. Edge endpoints are stems; node ids are stems.
       """
       incoming_counts = {s: len(v) for s, v in self._incoming.items()}
       nodes = [
           {
               "id": stem,
               "title": self._titles.get(stem, stem),
               "type": self._infer_type(path),          # plural key, e.g. "facts"
               "scope": self._meta.get(stem, {}).get("scope", ""),
               "status": self._meta.get(stem, {}).get("status", ""),
               "incoming": incoming_counts.get(stem, 0),
           }
           for stem, path in sorted(self._stem_index.items())
       ]
       links = [
           {"source": src, "target": dst}
           for src in sorted(self._outgoing)
           for dst in self._outgoing[src]
       ]
       return {"nodes": nodes, "links": links}
   ```

   `reload()` already calls `_rebuild_link_maps()`, so the graph payload is
   automatically fresh after `POST /admin/reload`. No extra caching needed
   (payload is built from in-memory maps; serialization of ~1-2k nodes is fast).

### 4.2 Routes

New file: `ledger/web/routes/graph.py` (mirror the structure of
`routes/browse.py`):

```python
"""Graph view routes: HTML shell + corpus graph data."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()


@router.get("/graph", response_class=HTMLResponse)
async def graph_page(request: Request) -> HTMLResponse:
    corpus = request.app.state.corpus
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "graph.html",
        {
            "types": corpus.note_types(),
            "active_type": "graph",
            "active_label": "Graph",
        },
    )


@router.get("/graph/data.json")
async def graph_data(request: Request) -> JSONResponse:
    return JSONResponse(request.app.state.corpus.graph())
```

Register in `ledger/web/server.py` inside `create_app` (keep local-import
pattern): add `from ledger.web.routes import graph as graph_routes` next to the
other route imports and `app.include_router(graph_routes.router)` after
`browse_routes` (alphabetical with the rest).

### 4.3 Sidebar entry

In `templates/base.html`, add a sidebar item between the `search` and
`signals` items, same markup pattern:

```html
<li>
  <a href="/graph"
     class="type-link{% if active_type == 'graph' %} active{% endif %}">
    <span class="type-label">graph</span>
  </a>
</li>
```

### 4.4 Template

New file: `ledger/web/templates/graph.html`:

```html
{% extends "base.html" %}
{% block title %}Graph - cogled{% endblock %}
{% block head_extra %}
<script src="/static/d3.v7.min.js" defer></script>
<script src="/static/graph.js" defer></script>
{% endblock %}
{% block content %}
<div class="graph-toolbar" id="graph-filters">
  {% for type in types %}
  <button class="chip chip-active" data-type="{{ type.key }}">{{ type.label }}</button>
  {% endfor %}
  <button class="chip" id="chip-open-loops">open loops only</button>
</div>
<canvas id="graph-canvas" width="1200" height="800"
        data-source="/graph/data.json"></canvas>
<p class="graph-hint">Click a node to open the note. Drag to pan, scroll to zoom.</p>
{% endblock %}
```

### 4.5 Vendored assets

- `ledger/web/static/d3.v7.min.js`: download
  `https://unpkg.com/d3@7.9.0/dist/d3.min.js` (~280 KB, ISC license) and check
  it in. Append a section to `ledger/web/static/README.md` recording
  version (7.9.0), source URL, upstream (https://github.com/d3/d3), license
  (ISC) — same format as the htmx entry. No npm, no CDN at runtime.
- `ledger/web/static/graph.js`: new, project-original, ~100-140 lines, plain
  ES (no modules, no build step). Behaviour:
  1. `fetch(canvas.dataset.source)` → `{nodes, links}`.
  2. `d3.forceSimulation(nodes)` with `forceLink(links).id(d => d.id)
     .distance(60)`, `forceManyBody().strength(-80)`, `forceCenter`, and
     `forceCollide(8)`.
  3. Render to canvas 2D context on each tick (canvas, not SVG — must stay
     smooth at 1-2k nodes). `d3.zoom()` on the canvas for pan/zoom transform.
  4. Node radius: `4 + 2 * Math.sqrt(node.incoming)`. Node colour: fixed map
     by `node.type` key — `facts:#4c9be8, preferences:#8a63d2, goals:#3eb489,
     loops:#e8a13c, concepts:#d65d7a, projects:#5fb3b3, identity:#999` (define
     as a JS const; unknown types fall back to `#777`).
  5. Hover: tooltip (a positioned `<div>` appended to body) showing
     `node.title`. Click: `window.location = "/note/" + node.id`
     (hit-test nearest node within 10px of the inverse-transformed pointer).
  6. Filter chips: clicking a `[data-type]` chip toggles its `chip-active`
     class and hides/shows nodes of that type (filter the node/link arrays and
     `simulation.nodes(...)` / `force("link").links(...)` + `alpha(0.3).restart()`;
     a link is hidden if either endpoint is hidden). `#chip-open-loops`
     toggles a mode that keeps only nodes with `type === "loops" &&
     status === "open"` plus their direct neighbours. Filtering is pure
     client-side JS — no HTMX round-trips (data is already loaded).
- `ledger/web/static/style.css`: add `.graph-toolbar`, `.chip`,
  `.chip-active`, `#graph-canvas { width: 100%; height: 75vh; }`,
  `.graph-tooltip`, `.graph-hint` rules (match existing visual language —
  reuse the pill/chip styling already present for search scope chips).

### 4.6 Tests

New file: `tests/web/test_graph.py` (uses existing `client` fixture from
`tests/web/conftest.py`):

- `test_graph_page_renders(client)`: `GET /graph` → 200; body contains
  `id="graph-canvas"`, `/static/d3.v7.min.js`, `/static/graph.js`, and a chip
  for the `fact` label.
- `test_graph_data_shape(client)`: `GET /graph/data.json` → 200 JSON with
  `nodes` and `links` keys. Node ids include `fact__sample`, `fact__other_one`,
  `loop__open_test`. The `fact__sample` node has `type == "facts"`,
  `title == "Sample Fact"`, `incoming == 0`; `fact__other_one` has
  `incoming == 1`. `links` contains
  `{"source": "fact__sample", "target": "fact__other_one"}`. The broken target
  `fact__does_not_exist` appears in **no** node id and **no** link endpoint.
- `test_graph_data_excludes_private_links(client, web_ledger_root)`: write a
  new note `02_facts/fact__private_edge.md` whose body contains
  `<private>see [[fact__other_one]]</private>` and no other links; `POST
  /admin/reload`; `GET /graph/data.json` must contain the
  `fact__private_edge` node but **no** link from it. Also assert the existing
  backlinks behaviour stays consistent: `GET /note/fact__other_one` does not
  list `fact__private_edge` under "Linked from".
- `test_graph_data_reload_picks_up_new_notes(client, web_ledger_root)`: write
  `02_facts/fact__late_arrival.md` (minimal frontmatter + H1, link to
  `[[fact__sample]]`), `POST /admin/reload`, assert the new node and the new
  link appear in `/graph/data.json`.

Also update `tests/web/test_backlinks.py` only if the private-stripping change
breaks an existing assertion (it should not — fixture notes contain no
`<private>` fences).

### 4.7 Acceptance (run all; expected results inline)

```bash
pytest tests/web/ -q                          # all green, incl. test_graph.py
ledger web &                                  # serve real corpus
curl -s http://127.0.0.1:8765/graph | grep -c graph-canvas         # >= 1
curl -s http://127.0.0.1:8765/graph/data.json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(len(d['nodes']), len(d['links']))"
                                              # node count ≈ ledger note count, links > 0
curl -s -X POST http://127.0.0.1:8765/admin/reload | grep '"ok": *true'
```

Manual: open http://127.0.0.1:8765/graph — graph settles in <3s for ~1.2k
notes, stays interactive (~30fps+); clicking a node opens `/note/<stem>`;
type chips hide/show node classes; "open loops only" isolates open loops and
neighbours. If perf is unacceptable at this corpus size, reduce
`forceManyBody` strength / cap tick rendering before considering sigma.js.

---

## Phase 5 — Polish (after Phase 4)

### 5.1 Status bar

Replace the hardcoded `<footer class="statusbar">` in `templates/base.html`
with data-driven cells. Add a tiny helper in `ledger/web/server.py` (or a
Jinja global) exposing per-request status — simplest: compute in `create_app`
a Jinja global function `statusbar()` that returns
`{"notes_loaded": sum(t.count for t in corpus.note_types()), "index_built_at": load_note_index().get("built", ""), "embeddings": <reuse _embeddings_enabled from routes/admin.py — move it to services/corpus.py or a shared helper so both import it>}`.
Cells: `cogled <version> · {{ notes_loaded }} notes · index {{ index_built_at or "never" }} · embeddings {{ "on" if embeddings else "off" }}`
plus a reload button:

```html
<button class="status-reload" hx-post="/admin/reload" hx-swap="none"
        title="Re-scan corpus after ledger sleep index">reload</button>
```

(htmx is already loaded in base.html; `hx-swap="none"` is enough — a follow-up
full refresh is acceptable, or add `onclick="setTimeout(()=>location.reload(),300)"`.)

### 5.2 Keyboard shortcuts

New file `ledger/web/static/shortcuts.js` (~30 lines, plain JS), loaded from
`base.html` with `defer`:
- `/` → focus the topbar search input (`.topbar-search input`), prevent default.
- `g` → navigate to `/graph`.
- `j` / `k` → move a `.kbd-focus` class down/up across `.note-row` (browse
  list) or `.result-card` (search results) elements; `Enter` opens the focused
  item's first `<a>`.
- `Esc` → blur active element.
- All handlers no-op when the event target is an `input`/`textarea`.

### 5.3 Print stylesheet

Append to `static/style.css`:

```css
@media print {
  .topbar, .sidebar, .statusbar, .graph-toolbar { display: none; }
  .layout { display: block; }
  .content { max-width: none; }
}
```

### 5.4 Tests + acceptance

- `tests/web/test_routes.py`: add `test_statusbar_shows_corpus_size(client)` —
  `GET /browse` body contains `notes` count cell and the `hx-post="/admin/reload"`
  button.
- Acceptance: `pytest tests/web/ -q` green; manual: `/` focuses search, `g`
  opens graph, `j/k` walk the recent list, print preview of a note shows body
  only.

### 5.5 Docs

- `README.md` "Web UI" section (exists at line ~304): add one paragraph on
  `/graph` and the keyboard shortcuts.
- `CHANGELOG.md`: new entry under the next unreleased version:
  `feat(web): Phase 4 graph view (+ Phase 5 polish)` listing `/graph`,
  `/graph/data.json`, private-fence link stripping, status bar, shortcuts.

---

## Invariants (apply to all remaining work)

- **No parallel logic.** Graph/status read only via `Corpus`; do not re-read
  `note_index.json` or parse notes in route handlers. If `Corpus` is
  insufficient, extend `Corpus` first.
- **Privacy.** Everything served — HTML, JSON, graph edges — must exclude
  `<private>...</private>` content (`ledger.parsing.strip_private_tags`).
- **No writes** from any route except `POST /admin/reload` (loopback-only).
- **No CDN/npm at runtime.** New JS is vendored or project-original; record
  third-party files in `ledger/web/static/README.md` with version + license.
- **Tests use the fixture corpus** (`tests/web/conftest.py`), never the live
  user ledger.
- Web deps stay under the `cognitive-ledger[web]` extra; core `ledger` CLI must
  keep working without fastapi installed (`pytest.importorskip` guard stays).

## Definition of done for v1

`ledger web` serves browse, note detail with backlinks, search (both modes),
signals dashboard, `/healthz`, `/admin/reload`, **graph view with filters**,
status bar, and keyboard shortcuts — with `pytest tests/web/ -q` green and the
manual acceptance checks above passing against the real corpus.

---

## Workflow decomposition (Sonnet subagents)

**Single repo:** cogled = `/Users/damsleth/code/cognitive-ledger`
(`.venv/bin/pytest tests/web/ -q`). Web deps live under the
`cognitive-ledger[web]` extra. Every subagent: read this plan + `AGENTS.md`
first; edit only its listed files; never share a file with a parallel sibling.
All tasks here mutate one working tree, so **any two tasks run concurrently must
use `isolation: worktree`** — otherwise run them serially.

### Task graph — Phase 4 (graph view)

| ID | Steps | Files (exclusive) | Depends on | Verify |
|----|-------|-------------------|-----------|--------|
| P4-1 | 4.1 (`Corpus.graph()` + `_titles`/`_meta` + private-strip in `_rebuild_link_maps`) | `ledger/web/services/corpus.py` | — | `.venv/bin/pytest tests/web/test_backlinks.py -q` (no regress) |
| P4-2 | 4.2 routes + server registration | `ledger/web/routes/graph.py` (new), `ledger/web/server.py` | P4-1 | `GET /graph/data.json` → 200 |
| P4-3 | 4.3, 4.4 (sidebar + template) | `ledger/web/templates/base.html`, `ledger/web/templates/graph.html` (new) | P4-2 | `GET /graph` → 200, has `graph-canvas` |
| P4-4 | 4.5 vendored d3 + `graph.js` + CSS + README provenance | `ledger/web/static/d3.v7.min.js` (new), `ledger/web/static/graph.js` (new), `ledger/web/static/style.css`, `ledger/web/static/README.md` | — (parallel with P4-1/2) | files present; README records d3 7.9.0 ISC |
| P4-5 | 4.6 tests | `tests/web/test_graph.py` (new) | P4-1..P4-4 | `.venv/bin/pytest tests/web/test_graph.py -q` |

> d3 fetch (4.5): download `https://unpkg.com/d3@7.9.0/dist/d3.min.js` and check
> it in — this is a one-time network step the asset agent performs, not a
> runtime dependency. Record version/license in `static/README.md`.

### Task graph — Phase 5 (polish; after Phase 4)

| ID | Steps | Files (exclusive) | Depends on | Verify |
|----|-------|-------------------|-----------|--------|
| P5-1 | 5.1 status bar (Jinja global + footer) | `ledger/web/server.py`, `ledger/web/templates/base.html` | P4 done | `tests/web/test_routes.py -q` |
| P5-2 | 5.2 keyboard shortcuts | `ledger/web/static/shortcuts.js` (new), `ledger/web/templates/base.html` | P4 done | manual: `/`, `g`, `j/k`, `Esc` |
| P5-3 | 5.3 print stylesheet | `ledger/web/static/style.css` | — | print preview = body only |
| P5-4 | 5.4 statusbar test | `tests/web/test_routes.py` | P5-1 | `tests/web/test_routes.py -q` |
| P5-5 | 5.5 docs | `README.md`, `CHANGELOG.md` | P4 + P5 code done | manual review |

> P5-1 and P5-2 both edit `base.html` → **sequential, not parallel.** Same for
> P4-3 and P5-1/P5-2 (all touch `base.html`). P4-4, P5-3 both edit `style.css` →
> sequential.

### Parallelism

- **Round 1:** P4-1 ∥ P4-4 (disjoint: corpus service vs static assets).
- **Round 2:** P4-2 (after P4-1).
- **Round 3:** P4-3 (after P4-2 + P4-4).
- **Round 4:** P4-5 tests (after all P4 code). → run 4.7 acceptance.
- **Phase 5** starts only after Phase 4 acceptance green. Within it, the
  base.html / style.css sharing forces: P5-3 ∥ (P5-1 → P5-2 chain) → P5-4 →
  P5-5.

### Gate discipline

Invariants in §Invariants are non-negotiable per task: no parallel logic (read
via `Corpus` only), privacy strip on everything served, no writes outside
`/admin/reload`, no runtime CDN/npm, fixture corpus in tests, `[web]` extra +
`pytest.importorskip` guard intact. A task that violates an invariant fails
review regardless of its Verify cell.
