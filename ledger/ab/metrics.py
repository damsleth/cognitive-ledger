from __future__ import annotations
from dataclasses import dataclass
from ledger.config import get_config
from typing import Any
import math
import statistics
from .corpus import InvalidSetupError


EXIT_BENEFICIAL = 0


EXIT_REGRESSION = 2


EXIT_NEUTRAL = 3


EXIT_INVALID_SETUP = 4


EPSILON = 1e-9


QUALITY_KEYS = ("hit1", "hitk", "mrr")


DEFAULT_OBJECTIVE_WEIGHTS: dict[str, float] = {"mrr": 0.6, "hitk": 0.4}


def compute_objective(
    quality: dict[str, float], weights: dict[str, float] | None = None
) -> float:
    """Collapse a quality dict (hit1/hitk/mrr) to one scalar to maximize.

    Weights default to the ``ab_objective_weights`` config knob, falling back
    to ``DEFAULT_OBJECTIVE_WEIGHTS`` (0.6*mrr + 0.4*hitk). Unknown weight keys
    are ignored; missing metrics count as 0.0.
    """
    if weights is None:
        try:
            weights = dict(get_config().ab_objective_weights)
        except Exception:
            weights = dict(DEFAULT_OBJECTIVE_WEIGHTS)
    return sum(
        float(w) * float(quality.get(key, 0.0))
        for key, w in weights.items()
        if key in QUALITY_KEYS
    )


QUERY_METRIC_KEYS = (
    "query_latency_ms",
    "candidate_build_ms",
    "prefilter_ms",
    "shortlist_ms",
    "scoring_ms",
    "index_rebuild_ms",
)


MAINTENANCE_METRIC_KEYS = (
    "sync_drift_count",
    "changes_since_sleep",
    "lint_errors",
    "lint_warnings",
)


PROFILE_TOKEN_SCOPES = ("personal", "work", "dev")


@dataclass
class LatencySummary:
    count: int
    min_ms: float
    max_ms: float
    median_ms: float
    mean_ms: float
    p95_ms: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "min_ms": self.min_ms,
            "max_ms": self.max_ms,
            "median_ms": self.median_ms,
            "mean_ms": self.mean_ms,
            "p95_ms": self.p95_ms,
        }


def summarize_latency(samples_ms: list[float]) -> LatencySummary:
    if not samples_ms:
        raise InvalidSetupError("No latency samples were recorded")

    ordered = sorted(samples_ms)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)

    return LatencySummary(
        count=len(ordered),
        min_ms=min(ordered),
        max_ms=max(ordered),
        median_ms=statistics.median(ordered),
        mean_ms=statistics.mean(ordered),
        p95_ms=ordered[p95_index],
    )


def summarize_distribution(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "median": 0.0,
            "mean": 0.0,
            "p95": 0.0,
        }

    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "count": len(ordered),
        "min": min(ordered),
        "max": max(ordered),
        "median": statistics.median(ordered),
        "mean": statistics.mean(ordered),
        "p95": ordered[p95_index],
    }


def decide_outcome(
    baseline_quality: dict[str, float],
    candidate_quality: dict[str, float],
    baseline_eval_p95_ms: float,
    candidate_eval_p95_ms: float,
    baseline_query_p95_ms: float,
    candidate_query_p95_ms: float,
    latency_tol_pct: float,
    latency_tol_ms: float,
) -> dict[str, Any]:
    deltas = {
        key: float(candidate_quality[key]) - float(baseline_quality[key])
        for key in QUALITY_KEYS
    }

    regressed = [key for key in QUALITY_KEYS if deltas[key] < -EPSILON]
    improved = [key for key in QUALITY_KEYS if deltas[key] > EPSILON]

    # Negative cases (expected_none) gate too, lower is better: a change that
    # starts returning confident junk for unanswerable queries is a regression
    # even when hit@k/mrr hold. Only compared when both sides measured
    # negatives (an older baseline ref may not report them).
    if int(baseline_quality.get("negative_cases", 0) or 0) and int(
        candidate_quality.get("negative_cases", 0) or 0
    ):
        fp_delta = float(candidate_quality["false_positive_rate"]) - float(
            baseline_quality["false_positive_rate"]
        )
        deltas["false_positive_rate"] = fp_delta
        if fp_delta > EPSILON:
            regressed.append("false_positive_rate")
        elif fp_delta < -EPSILON:
            improved.append("false_positive_rate")

    if regressed:
        return {
            "decision": "regression",
            "reason": f"quality regression detected in: {', '.join(regressed)}",
            "exit_code": EXIT_REGRESSION,
            "quality_deltas": deltas,
            "regressed_metrics": regressed,
            "improved_metrics": improved,
            "latency": None,
        }

    if improved:
        return {
            "decision": "beneficial",
            "reason": f"quality improved in: {', '.join(improved)}",
            "exit_code": EXIT_BENEFICIAL,
            "quality_deltas": deltas,
            "regressed_metrics": regressed,
            "improved_metrics": improved,
            "latency": None,
        }

    eval_threshold = baseline_eval_p95_ms * (1.0 + latency_tol_pct) + latency_tol_ms
    query_threshold = baseline_query_p95_ms * (1.0 + latency_tol_pct) + latency_tol_ms
    eval_ok = candidate_eval_p95_ms <= (eval_threshold + EPSILON)
    query_ok = candidate_query_p95_ms <= (query_threshold + EPSILON)

    latency_info = {
        "eval": {
            "baseline_p95_ms": baseline_eval_p95_ms,
            "candidate_p95_ms": candidate_eval_p95_ms,
            "threshold_ms": eval_threshold,
            "passed": eval_ok,
        },
        "query": {
            "baseline_p95_ms": baseline_query_p95_ms,
            "candidate_p95_ms": candidate_query_p95_ms,
            "threshold_ms": query_threshold,
            "passed": query_ok,
        },
    }

    # A quality tie is neutral, never a win: an A/A run or an off-by-default
    # change must not read as "beneficial". Latency is reported, not gated:
    # identical code failed the latency band twice in a row on the fixture
    # corpus (eval p95 33 -> 42 ms; sides run sequentially, p95 of 5 samples
    # is the max), so a slow tie names the metric but stays neutral.
    # ponytail: advisory latency; make it blocking only after ABBA
    # interleaving / min-of-N and an absolute floor (improvement plan I8).
    slow = [
        name
        for name, ok in (("latency_eval_p95", eval_ok), ("latency_query_p95", query_ok))
        if not ok
    ]
    reason = (
        f"quality tied; latency over threshold (advisory) in: {', '.join(slow)}"
        if slow
        else "quality tied and candidate stayed within latency thresholds"
    )
    return {
        "decision": "neutral",
        "reason": reason,
        "exit_code": EXIT_NEUTRAL,
        "quality_deltas": deltas,
        "regressed_metrics": regressed,
        "improved_metrics": improved,
        "latency": latency_info,
    }
