"""Reciprocal Rank Fusion over ranked key lists.

``reciprocal_rank_fusion`` moved verbatim from ``ledger/query.py``;
``fuse_ranked_lists`` is the generalized entry point (ranked lists in, fused
list out) that ``ledger/tier1.py``'s ``fuse_results`` and yaams's hybrid
retrieval both build on.
"""

from __future__ import annotations


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = 60,
) -> dict[str, float]:
    """Compute Reciprocal Rank Fusion scores for items appearing in multiple rank lists.

    Each element of ``ranked_lists`` is an ordered list of item keys (e.g.
    ``rel_path`` strings) from highest to lowest rank.  Items that appear in
    multiple lists accumulate scores; items absent from a list contribute 0.

    Formula: RRF(d) = sum_over_lists(1 / (k + rank(d)))
    where rank is 1-based and ``k`` is the smoothing constant (default 60).

    Returns a dict mapping item key -> RRF score (higher is better).
    The absolute score values are not meaningful on their own — use them
    only for relative ordering.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank_idx, item_key in enumerate(ranked):
            scores[item_key] = scores.get(item_key, 0.0) + 1.0 / (k + rank_idx + 1)
    return scores


def fuse_ranked_lists(
    ranked_lists: list[list[str]],
    k: int = 60,
) -> list[str]:
    """Fuse ranked key lists into one list ordered by descending RRF score.

    Ties break by first appearance across the input lists (scanned in order),
    so the result is deterministic for equal scores. Duplicate keys within one
    list keep their first (best) rank, matching ``reciprocal_rank_fusion``'s
    accumulation over every occurrence.
    """
    scores = reciprocal_rank_fusion(ranked_lists, k=k)
    first_seen: dict[str, int] = {}
    position = 0
    for ranked in ranked_lists:
        for item_key in ranked:
            if item_key not in first_seen:
                first_seen[item_key] = position
                position += 1
    return sorted(scores, key=lambda item_key: (-scores[item_key], first_seen[item_key]))
