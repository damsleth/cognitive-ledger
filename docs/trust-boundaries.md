# Trust Boundaries

This document defines the **agent trust model** for the Cognitive Ledger:
which agents can do what, and where the walls are.

---

## Principals

| Principal | Description |
|-----------|-------------|
| **Owner** | The human user who owns the ledger. Highest trust. |
| **Local agent** | Claude Code, Codex, or any LLM running locally with access to the notes directory. High trust within defined write policy. |
| **Remote agent** | Future: an API consumer, a scheduled cloud agent, or a third-party integration. Low trust; read-only by default. |
| **CI / automation** | Eval harnesses, `ledger ab run`, GitHub Actions. No write access to live notes. |

---

## Write policy

The default policy is **Auto-write + Silent write** (see `AGENTS.md`).
A local agent:

- **May write** high-confidence, durable artifacts (facts, preferences, goals, concepts, open loops) without asking.
- **Must ask** for genuinely ambiguous, sensitive, or identity-layer changes.
- **Must not** write raw chat transcripts, invented facts, or bulk dumps.

Write modes:

| Mode | Behaviour |
|------|-----------|
| `auto-write` | Persist high-confidence artifacts without confirmation |
| `silent-write` | Do not show diffs; rely on git for reversibility |
| `ask-to-write` | Ask before any write (useful for sensitive topics) |

The active mode can be overridden per-session by the user.

---

## Read policy

| Scope tag | Who may read |
|-----------|--------------|
| `meta` | Any agent |
| `dev` | Any agent |
| `work` | Local agent (owner-context sessions) |
| `personal` | Local agent only; not surfaced in cross-scope queries |
| `home` | Local agent only; not surfaced in work-context queries |
| `life` (alias for `personal`) | Same as `personal` |

Retrieval commands (`ledger query`) respect `--scope` filtering.  Cross-scope
queries require `--scope all` to be explicit.

---

## Index and embedding trust

- **note_index.json** — generated locally; not committed to git by default.
- **Embeddings** — float vectors only; no raw note text is stored in the vector index.
- **Private fences** — content inside ```` ```private ``` ```` is stripped before any index write, LLM prompt, or web snippet.

An agent must never forward raw indexed content to a remote service
without the owner's explicit consent.

---

## External service boundaries

| Service | Current use | Trust level |
|---------|------------|-------------|
| `sentence-transformers` (local) | Embedding generation | Fully local; no data leaves the machine |
| LLM judge (`claude -p` or subprocess) | Signal seeding, retrieval eval | Queries + note snippets sent; no full note bodies without consent |
| GitHub Actions | CI, release workflow | Test corpus only (fixtures); no live notes |

---

## Audit trail

Every note write appends to `notes/08_indices/timeline.md`.
Every retrieval event can optionally log to `08_indices/query_log.jsonl`
(opt-in; default off).

The `ledger --doctor` health check verifies:

- Redaction sentinel is caught by `redact()`.
- No private-fenced content leaked into `note_index.json`.

---

## Threat model (non-goals)

The ledger does **not** protect against:

- A malicious process with filesystem access to the notes directory.
- A compromised LLM that lies about what it wrote.
- Side-channel attacks on the embedding vectors.

It is a personal knowledge tool, not a security boundary.
