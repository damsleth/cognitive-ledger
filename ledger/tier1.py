"""Tier-1 (YAAMS) result fetching and cross-tier RRF fusion.

This module is imported *lazily* from ledger/cli.py — only when the user
passes --include-tier1 to `ledger query`.  It must never be imported at
module level in cli.py so that the default query path gains zero overhead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from ledger.query import reciprocal_rank_fusion


# Subprocess timeout in seconds for the yaams call.
_YAAMS_TIMEOUT = 10


@dataclass
class Tier1Result:
    id: str
    kind: str
    source: str
    timestamp: str
    content: str
    subject: str
    sender: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def display_id(self) -> str:
        """Short display id shown in human-readable output."""
        return f"yaams:{self.id[:24]}"


def fetch_yaams_results(
    query: str,
    limit: int,
    yaams_cli: str = "yaams",
    min_score: float | None = None,
) -> tuple[list[Tier1Result], str | None]:
    """Run yaams and return parsed Tier1Result list plus optional error string.

    Returns ``(results, None)`` on success and ``([], reason_string)`` on any
    failure.  All failure modes are non-fatal — the caller degrades to
    tier-2-only and prints a stderr warning.

    Parameters
    ----------
    query:
        Verbatim query text forwarded to yaams.
    limit:
        ``--top-k`` value passed to yaams.
    yaams_cli:
        Path or name of the yaams binary (default ``"yaams"``).
    min_score:
        When set, discard results whose ``score`` is below this threshold.
    """
    # Check binary exists before spawning.
    if not shutil.which(yaams_cli):
        return [], "yaams_not_found"

    cmd = [
        yaams_cli,
        "query",
        query,
        "--top-k",
        str(limit),
        "--tier",
        "raw",
        "--no-parse",
        "--no-log",
        "--json",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_YAAMS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return [], "timeout"

    if proc.returncode != 0:
        return [], f"yaams_exit_{proc.returncode}"

    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return [], "invalid_json"

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return [], "invalid_json"

    results: list[Tier1Result] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        score = float(item.get("score", 0.0))
        if min_score is not None and score < min_score:
            continue
        results.append(
            Tier1Result(
                id=str(item.get("id", "")),
                kind=str(item.get("kind", "")),
                source=str(item.get("source", "")),
                timestamp=str(item.get("timestamp", "")),
                content=str(item.get("content_preview", item.get("content", ""))),
                subject=str(item.get("subject", "")),
                sender=str(item.get("sender", "")),
                score=score,
                metadata=dict(item.get("metadata", {})),
            )
        )

    return results, None


def fuse_results(
    t2_payload: dict[str, Any],
    tier1_results: list[Tier1Result],
    *,
    tier2_boost: float = 0.0,
    rrf_k: int = 60,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    """Fuse tier-2 payload dict with tier-1 results via Reciprocal Rank Fusion.

    Modifies and returns ``t2_payload`` with:
    - ``results`` replaced by the merged + RRF-sorted list, each entry
      annotated with ``_tier`` (1 or 2) and ``rrf`` score.
    - ``fusion`` metadata block added at the top level.

    The ``t2_payload`` must be a plain dict (as produced by
    ``query_result_to_json`` or returned from the retrieval engine).
    ``RetrievalResult`` dataclass objects should be converted first.
    """
    t2_results = t2_payload.get("results", [])

    # Build rank lists: tier-2 uses rel_path (or path), tier-1 uses id.
    t2_keys: list[str] = []
    for r in t2_results:
        if isinstance(r, dict):
            key = r.get("rel_path") or r.get("path", "")
        else:
            key = str(getattr(r, "rel_path", "") or getattr(r, "path", ""))
        t2_keys.append(key)

    t1_keys = [r.id for r in tier1_results]

    rrf_scores = reciprocal_rank_fusion([t2_keys, t1_keys], k=rrf_k)

    # Annotate tier-2 results with _tier and boosted rrf.
    annotated_t2: list[dict[str, Any]] = []
    for r in t2_results:
        if isinstance(r, dict):
            d = dict(r)
            key = d.get("rel_path") or d.get("path", "")
        else:
            from ledger.query import scored_result_to_dict
            d = scored_result_to_dict(r)
            key = d.get("rel_path") or d.get("path", "")
        d["_tier"] = 2
        d["rrf"] = rrf_scores.get(key, 0.0) + tier2_boost
        annotated_t2.append(d)

    # Build tier-1 result dicts.
    annotated_t1: list[dict[str, Any]] = []
    for r in tier1_results:
        d: dict[str, Any] = {
            "_tier": 1,
            "id": r.id,
            "kind": r.kind,
            "source": r.source,
            "timestamp": r.timestamp,
            "subject": r.subject,
            "sender": r.sender,
            "content": r.content,
            "score": r.score,
            "rrf": rrf_scores.get(r.id, 0.0),
        }
        annotated_t1.append(d)

    # Merge and sort by rrf descending.
    merged = sorted(annotated_t2 + annotated_t1, key=lambda x: x.get("rrf", 0.0), reverse=True)

    t2_payload["results"] = merged
    t2_payload["fusion"] = {
        "tier2_count": len(annotated_t2),
        "tier1_count": len(annotated_t1),
        "rrf_k": rrf_k,
        "tier2_boost": tier2_boost,
        "unavailable_reason": unavailable_reason,
    }

    return t2_payload
