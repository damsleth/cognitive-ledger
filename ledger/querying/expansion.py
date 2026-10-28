"""Expansion operations for query."""

from __future__ import annotations




def prf_expand_query_vector(
    query_vec: "np.ndarray",
    candidate_vectors: dict[str, "np.ndarray"],
    top_rel_paths: list[str],
    bottom_rel_paths: list[str],
    alpha: float = 1.0,
    beta: float = 0.75,
    gamma: float = 0.15,
) -> "np.ndarray":
    """Expand a query vector via Rocchio Pseudo-Relevance Feedback (PRF).

    Implements the Rocchio formula:
      q2 = alpha * q + beta * mean(pseudo_positives) - gamma * mean(pseudo_negatives)

    The result is L2-normalised to unit length so it can be used directly with
    cosine-similarity scoring.  If no pseudo-positive or pseudo-negative vectors
    are available (e.g. too few candidates), the original query vector is returned
    unchanged.

    Parameters
    ----------
    query_vec:
        Original query embedding, shape (1, dims) or (dims,), float32, L2-normalised.
    candidate_vectors:
        Mapping from rel_path -> embedding vector (shape (dims,) or (1, dims)).
    top_rel_paths:
        Paths of pseudo-positive candidates (top-m ranked results).
    bottom_rel_paths:
        Paths of pseudo-negative candidates (bottom-n from a wider pool).
    alpha, beta, gamma:
        Rocchio weighting coefficients.

    Returns
    -------
    np.ndarray of shape (1, dims), float32, L2-normalised expanded query vector.
    """
    import numpy as np

    q = np.asarray(query_vec, dtype=np.float32).reshape(-1)

    pos_vecs = [
        np.asarray(candidate_vectors[p], dtype=np.float32).reshape(-1)
        for p in top_rel_paths
        if p in candidate_vectors
    ]
    neg_vecs = [
        np.asarray(candidate_vectors[p], dtype=np.float32).reshape(-1)
        for p in bottom_rel_paths
        if p in candidate_vectors
    ]

    q2 = alpha * q

    if pos_vecs:
        pos_centroid = np.mean(np.stack(pos_vecs, axis=0), axis=0)
        q2 = q2 + beta * pos_centroid

    if neg_vecs:
        neg_centroid = np.mean(np.stack(neg_vecs, axis=0), axis=0)
        q2 = q2 - gamma * neg_centroid

    # L2 normalise
    norm = float(np.linalg.norm(q2))
    if norm > 1e-9:
        q2 = q2 / norm

    return q2.reshape(1, -1).astype(np.float32)

