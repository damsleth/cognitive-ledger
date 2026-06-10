# Privacy Model

The Cognitive Ledger is a **personal memory system** designed to be owned
and operated by a single user.  The design choices below reflect that goal.

---

## What is stored

The ledger stores **atomic, structured notes** — facts, preferences, goals,
open loops, concepts, and identity documents — written in plain Markdown with
YAML frontmatter.  Notes are plain files in a user-controlled directory.

**What is never stored:**

- Raw chat transcripts (explicitly prohibited by the golden rules; see `AGENTS.md`).
- Credentials, tokens, API keys, or secrets.
- Content inside ```` ```private ``` ```` fences (stripped before indexing).

---

## Redaction and private fences

Any Markdown block delimited by ```` ```private ```` / ```` ``` ```` is treated
as **private content** and must not appear in:

- `08_indices/note_index.json`
- Embedding vectors (`08_indices/embeddings/`)
- LLM-judge prompts (signals, contradiction, eval)
- Web search snippets (`/search` endpoint)
- `backlinks` fields in JSON responses

The `redact()` function in `ledger/conventions.py` also strips secrets matching
common patterns (Bearer tokens, JWTs, high-entropy strings).  `ledger --doctor`
includes a smoke test that verifies `redact()` works correctly.

**Checking for leaks:**

```bash
ledger --doctor --json          # redact_sentinel_leak must not appear
ledger --doctor --json          # private_fence_in_index must not appear
```

---

## Index files

| File | Contents | Privacy note |
|------|----------|--------------|
| `08_indices/note_index.json` | Title, tags, frontmatter per note | Private-fenced body stripped |
| `08_indices/embeddings/` | Dense float vectors | No raw text stored |
| `08_indices/timeline.md` | Append-only action log | Path + verb only |
| `08_indices/signals.jsonl` | Retrieval feedback events | Query text + note path |
| `08_indices/query_log.jsonl` | Query history (opt-in) | Raw query text |

---

## Sharing and multi-device

The notes directory is intended for **local use only**.  If you sync it with
Git, a cloud drive, or any other mechanism:

1. Add a `.gitignore` to the notes root (created by `ledger init`).
2. Never commit `08_indices/.session_baseline`, `*.lock`, or
   `note_index.json` (all gitignored by default).
3. Review notes for sensitive personal data before pushing to a shared
   remote.

---

## Agent access

Agents operating on this ledger should follow the read policy:

- **Read** only what is needed for the current task (no bulk context loads).
- **Never log** raw note content to external services.
- **Respect scope** — notes tagged `scope: personal` or `scope: home` should
  not be surfaced in work-context queries unless the user explicitly requests.

See `docs/trust-boundaries.md` for the full agent trust model.

---

## Deleting data

To delete a note permanently:

1. Remove the file from its directory.
2. Append a `deleted` entry to `notes/08_indices/timeline.md`.
3. Rebuild indices: `ledger sleep index`.

Do **not** move deleted notes to `09_archive/` — that folder is for
superseded-but-still-valid facts.  Delete requests are hard-deletes.

---

## Contact / audit

All writes are recorded in `notes/08_indices/timeline.md`.  Running
`ledger sleep lint` will surface any malformed or missing frontmatter that
could indicate unintended writes.
