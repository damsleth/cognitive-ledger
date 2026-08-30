"""memcore — the shared retrieval contract for cognitive-ledger and yaams.

Extracted from the ledger so that yaams can depend on one canonical copy of
the retrieval seam instead of carrying ported duplicates:

- ``memcore.schema``: ``ScoredResult`` and the dataclasses it builds on
- ``memcore.rrf``: reciprocal rank fusion over ranked key lists
- ``memcore.rerank``: the cross-encoder reranker helpers
- ``memcore.trust``: trust verdicts and ``attach_trust_verdicts``

Hard rule: memcore is **stdlib-only**. ``pip install memcore`` must never
pull another package. Anything that needs numpy/sklearn stays in ``ledger``;
``memcore.rerank`` needs sentence-transformers only at *call* time (lazy
import), never at import or install time.
"""

from memcore.schema import RetrievalCandidate, ScoreComponents, ScoredResult
from memcore.rrf import fuse_ranked_lists, reciprocal_rank_fusion
from memcore.trust import TrustVerdict, attach_trust_verdicts, trust_verdict

__version__ = "0.1.0"

__all__ = [
    "RetrievalCandidate",
    "ScoreComponents",
    "ScoredResult",
    "TrustVerdict",
    "attach_trust_verdicts",
    "fuse_ranked_lists",
    "reciprocal_rank_fusion",
    "trust_verdict",
    "__version__",
]
