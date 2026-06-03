"""LLM-judged signal seeding backend for Cognitive Ledger.

Provides two backends for judging (query, note) relevance pairs:

- ``dummy``: deterministic lexical-overlap heuristic. Stable, offline,
  and used in all tests. Produces reproducible verdicts proportional to
  token overlap between query and note body/title.
- ``subprocess``: configurable external command (e.g. ``claude -p``).
  Receives a JSON prompt on stdin and returns a JSON verdict on stdout.

The seeding workflow is exposed via ``ledger signal seed --from-history``
(or ``--queries-file``).  All seeded events are written with
``synthetic: true`` and ``source: "llm_judge"`` so they can be
down-weighted by ``summarize_signals`` and rolled back via
``ledger signal purge --synthetic``.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Verdict type
# ---------------------------------------------------------------------------

class Verdict:
    """Result of judging a (query, note_path, note_body) triple."""

    __slots__ = ("relevant", "rating", "reason")

    def __init__(self, relevant: bool, rating: int | None, reason: str = "") -> None:
        self.relevant = relevant
        self.rating: int | None = rating  # 1–10 or None
        self.reason = reason

    def __repr__(self) -> str:
        return (
            f"Verdict(relevant={self.relevant!r}, "
            f"rating={self.rating!r}, reason={self.reason!r})"
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ALPHANUMERIC = re.compile(r"[a-zA-ZÀ-ɏА-я\d]+")


def _tokenize(text: str) -> set[str]:
    """Return a lowercase token set, ignoring short words."""
    return {t.lower() for t in _ALPHANUMERIC.findall(text) if len(t) > 2}


# ---------------------------------------------------------------------------
# Dummy backend (lexical overlap heuristic)
# ---------------------------------------------------------------------------

def _dummy_judge(query: str, note_body: str) -> Verdict:
    """Deterministic relevance verdict based on token overlap.

    Algorithm:
    - Compute Jaccard-like overlap: |query_tokens ∩ note_tokens| / |query_tokens|
    - Relevant if overlap >= 0.15 (at least 15% of query terms appear in note)
    - Rating is overlap scaled to 1–10

    This is intentionally simple and stable so tests are deterministic.
    """
    q_tokens = _tokenize(query)
    n_tokens = _tokenize(note_body)

    if not q_tokens:
        # No query tokens → default non-relevant verdict
        return Verdict(relevant=False, rating=None, reason="empty_query")

    overlap = len(q_tokens & n_tokens) / len(q_tokens)
    relevant = overlap >= 0.15
    # Scale 0.0–1.0 to 1–10
    rating = max(1, min(10, round(1 + overlap * 9)))
    return Verdict(
        relevant=relevant,
        rating=rating if relevant else None,
        reason=f"lexical_overlap={overlap:.3f}",
    )


# ---------------------------------------------------------------------------
# Subprocess backend
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a relevance judge for a personal knowledge base.

Query: {query}

Note path: {note_path}
Note content (first 800 chars):
{note_excerpt}

Decide whether this note is relevant to the query.
Return ONLY a JSON object with these keys:
  "relevant": true or false
  "rating": integer 1-10 (omit if not relevant)
  "reason": one-line explanation

JSON response:"""


def _subprocess_judge(
    query: str,
    note_path: str,
    note_body: str,
    command_template: str,
) -> Verdict:
    """Call an external command to judge relevance.

    The command receives a JSON object on stdin:
      {"prompt": "<formatted prompt string>", "query": "...", "note_path": "..."}

    The command must return a JSON object on stdout:
      {"relevant": bool, "rating": int | null, "reason": str}

    ``command_template`` may include ``{prompt}``, ``{query}``, ``{note_path}``
    shell placeholders (not used for security — the template is expanded
    in Python then the whole command is run via ``shell=True``).

    Example templates:
      ``"claude -p"``
      ``"/usr/local/bin/ollama run llama3 --format json"``
    """
    excerpt = note_body[:800]
    prompt_text = _PROMPT_TEMPLATE.format(
        query=query,
        note_path=note_path,
        note_excerpt=excerpt,
    )
    stdin_payload = json.dumps(
        {"prompt": prompt_text, "query": query, "note_path": note_path},
        ensure_ascii=False,
    )

    try:
        result = subprocess.run(
            command_template,
            shell=True,
            input=stdin_payload,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Judge subprocess timed out after 60s: {command_template!r}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Judge subprocess failed to start: {command_template!r}: {exc}"
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"Judge subprocess returned {result.returncode}: {result.stderr[:200]}"
        )

    # Parse the verdict from stdout — be lenient about surrounding whitespace
    raw = result.stdout.strip()
    # If the output contains a JSON block, extract it
    json_match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    try:
        verdict_dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Judge subprocess returned non-JSON output: {result.stdout[:200]!r}"
        ) from exc

    relevant = bool(verdict_dict.get("relevant", False))
    rating_raw = verdict_dict.get("rating")
    rating: int | None = None
    if rating_raw is not None and relevant:
        try:
            rating = max(1, min(10, int(rating_raw)))
        except (TypeError, ValueError):
            rating = None
    reason = str(verdict_dict.get("reason", ""))
    return Verdict(relevant=relevant, rating=rating, reason=reason)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def judge(
    query: str,
    note_path: str,
    note_body: str,
    backend: str = "dummy",
    subprocess_command: str = "",
) -> Verdict:
    """Judge whether a note is relevant to a query.

    Args:
        query: The search query string.
        note_path: Relative path of the note (used in the prompt).
        note_body: Full text content of the note.
        backend: ``"dummy"`` (lexical heuristic) or ``"subprocess"``.
        subprocess_command: Shell command template for the subprocess backend.

    Returns:
        A :class:`Verdict` with ``relevant``, ``rating``, and ``reason``.

    Raises:
        ValueError: If ``backend`` is not recognised.
        RuntimeError: If the subprocess backend fails or times out.
    """
    if backend == "dummy":
        return _dummy_judge(query, note_body)
    if backend == "subprocess":
        if not subprocess_command:
            raise ValueError(
                "subprocess_command must be set when backend='subprocess'"
            )
        return _subprocess_judge(query, note_path, note_body, subprocess_command)
    raise ValueError(
        f"Unknown judge backend: {backend!r}. Valid backends: 'dummy', 'subprocess'"
    )


def seed_from_queries(
    queries: list[str],
    notes_dir: Path,
    top_k: int = 5,
    backend: str = "dummy",
    subprocess_command: str = "",
    retrieve_fn: Any = None,
) -> list[dict[str, Any]]:
    """Judge top-k notes per query and return synthetic signal dicts.

    This is the core seeding logic.  The caller is responsible for writing
    the returned dicts to ``signals.jsonl`` via :func:`ledger.signals.append_signal_raw`.

    Args:
        queries: List of query strings to evaluate.
        notes_dir: Absolute path to the notes directory.
        top_k: Number of top notes to retrieve and judge per query.
        backend: Judge backend (``"dummy"`` or ``"subprocess"``).
        subprocess_command: Shell command for subprocess backend.
        retrieve_fn: Callable ``(query, notes_dir, top_k) -> list[str]`` returning
            relative note paths.  When ``None``, falls back to a simple filename
            glob scan (sufficient for offline/test use).

    Returns:
        List of signal dicts suitable for writing to ``signals.jsonl``.
        Each dict has ``synthetic: True``, ``source: "llm_judge"``.
    """
    import datetime as _dt

    if retrieve_fn is None:
        retrieve_fn = _simple_retrieve

    events: list[dict[str, Any]] = []
    for query in queries:
        candidates = retrieve_fn(query, notes_dir, top_k)
        for rel_path in candidates:
            abs_path = notes_dir / rel_path
            try:
                body = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            verdict = judge(
                query=query,
                note_path=rel_path,
                note_body=body,
                backend=backend,
                subprocess_command=subprocess_command,
            )
            ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            sig_type = "retrieval_hit" if verdict.relevant else "retrieval_miss"
            event: dict[str, Any] = {
                "ts": ts,
                "type": sig_type,
                "query": query,
                "note": rel_path,
                "synthetic": True,
                "source": "llm_judge",
            }
            if verdict.rating is not None:
                event["rating_hint"] = verdict.rating
            if verdict.reason:
                event["detail"] = verdict.reason
            events.append(event)

    return events


def _simple_retrieve(query: str, notes_dir: Path, top_k: int) -> list[str]:
    """Fallback retrieval: find top_k .md notes by filename token overlap.

    This is a very coarse heuristic used only when no retrieve_fn is
    provided — primarily for offline tests where the embedding index is
    not available.
    """
    q_tokens = _tokenize(query)
    scored: list[tuple[float, str]] = []
    for md_file in sorted(notes_dir.rglob("*.md")):
        rel = str(md_file.relative_to(notes_dir))
        # Score by filename token overlap
        name_tokens = _tokenize(md_file.stem)
        overlap = len(q_tokens & name_tokens) / max(len(q_tokens), 1)
        scored.append((overlap, rel))
    # Sort by score descending, then by path for stability
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [rel for _, rel in scored[:top_k]]
