"""Autoresearch retrieval harness — eval split runner.

Runs ``ledger eval`` on a named split, emits structured JSON, and (unless
``--no-write``) writes ``_STATE`` so subsequent runs can detect regressions
against this committed baseline.

Usage::

    .venv/bin/python scripts/autoresearch_retrieval.py \\
        --split dev --json --tag baseline-anchor

Splits
------
dev      retrieval_eval_screen.yaml   (31 cases, optimization set)
holdout  retrieval_eval_holdout.yaml  (14 cases, holdout gate)
all      retrieval_eval_cases.yaml    (45 cases, canonical benchmark)

Output JSON keys
----------------
quality           float  Composite: 0.6*mrr + 0.4*hitk
rank1             float  hit@1 (fraction of queries with gold at rank 1)
retrieval_p95_ms  float  p95 wall-clock latency per query (ms)
gold_queries      int    Number of eval cases in this split
hit1              float  Same as rank1 (alias)
hitk              float  hit@k
mrr               float  Mean Reciprocal Rank
cases             int    Same as gold_queries
k                 int    Cutoff used
status            str    "ok" or "regression" (vs _STATE)
tag               str    Value of --tag
split             str    Value of --split
generated_at      str    ISO-8601 UTC timestamp
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the repo root is importable when invoked as:
#   .venv/bin/python scripts/autoresearch_retrieval.py
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Ledger imports
# ---------------------------------------------------------------------------

from ledger.config import get_config  # noqa: E402
from ledger import eval as eval_lib  # noqa: E402
from ledger import semantic as semantic_lib  # noqa: E402
from ledger import query as query_lib  # noqa: E402
from ledger.retrieval import resolve_retrieval_mode, resolve_embed_backend  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPLITS: dict[str, str] = {
    "dev": "retrieval_eval_screen.yaml",
    "holdout": "retrieval_eval_holdout.yaml",
    "all": "retrieval_eval_cases.yaml",
}

STATE_FILENAME = "autoresearch_retrieval_state.json"

# Objective weights (locked — match autoresearch-ab-loop plan §Step 1)
_OBJ_MRR_W = 0.6
_OBJ_HITK_W = 0.4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _p95(samples: list[float]) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[idx]


def _objective(mrr: float, hitk: float) -> float:
    return _OBJ_MRR_W * mrr + _OBJ_HITK_W * hitk


def _load_embeddings_module():
    return semantic_lib.load_embeddings_module()


def _resolve_embed_model(backend: str, model: str | None) -> str:
    return semantic_lib.resolve_embed_model(
        backend,
        model,
        load_embeddings_module_fn=lambda: _load_embeddings_module(),
    )


def _rank_query(*args, **kwargs):
    return query_lib.rank_query(
        *args,
        load_embeddings_module=_load_embeddings_module,
        resolve_embed_model=_resolve_embed_model,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Core eval with latency measurement
# ---------------------------------------------------------------------------


def run_eval_timed(
    cases_path: Path,
    k: int,
    runs: int,
    retrieval_mode: str,
    embed_backend: str,
    embed_model: str | None,
) -> dict:
    """Run eval ``runs`` times, collect latency, return quality + latency dict."""
    cases = eval_lib.parse_eval_cases(cases_path)

    per_query_latencies: list[float] = []

    # First run: warm-up + quality scoring
    # Subsequent runs: latency only (same cases)
    quality_result: dict | None = None

    for run_idx in range(max(1, runs)):
        t0 = time.perf_counter()
        result = eval_lib.run_eval(
            cases_path=cases_path,
            k=k,
            retrieval_mode=retrieval_mode,
            embed_backend=embed_backend,
            embed_model=embed_model,
            rank_query_fn=_rank_query,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        n_cases = max(1, result.get("cases", 1))
        per_query_latencies.append(elapsed_ms / n_cases)

        if quality_result is None:
            quality_result = result

    assert quality_result is not None  # always runs at least once

    hit1 = float(quality_result.get("hit1", 0.0))
    hitk = float(quality_result.get("hitk", 0.0))
    mrr = float(quality_result.get("mrr", 0.0))
    n = int(quality_result.get("cases", len(cases)))

    return {
        "cases": n,
        "k": k,
        "hit1": hit1,
        "hitk": hitk,
        "mrr": mrr,
        "quality": _objective(mrr, hitk),
        "rank1": hit1,
        "retrieval_p95_ms": _p95(per_query_latencies),
        "gold_queries": n,
        "failed": quality_result.get("failed", []),
    }


# ---------------------------------------------------------------------------
# State persistence (_STATE — the regression reference)
# ---------------------------------------------------------------------------


def _state_path() -> Path:
    cfg = get_config()
    return Path(cfg.ledger_notes_dir) / "08_indices" / STATE_FILENAME


def write_state(metrics: dict, tag: str, split: str) -> None:
    """Persist quality metrics as the regression reference (_STATE)."""
    state = {
        "generated_at": _utc_now(),
        "tag": tag,
        "split": split,
        "quality": metrics["quality"],
        "rank1": metrics["rank1"],
        "hit1": metrics["hit1"],
        "hitk": metrics["hitk"],
        "mrr": metrics["mrr"],
        "retrieval_p95_ms": metrics["retrieval_p95_ms"],
        "gold_queries": metrics["gold_queries"],
        "cases": metrics["cases"],
        "k": metrics["k"],
    }
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[autoresearch] _STATE written → {path}", file=sys.stderr)


def load_state() -> dict | None:
    path = _state_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Autoresearch retrieval harness — eval split runner."
    )
    parser.add_argument(
        "--split",
        default="dev",
        choices=list(SPLITS.keys()),
        help="Eval split: dev (screen/31), holdout (14), all (45). Default: dev.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")
    parser.add_argument("--tag", default="", help="Label for this run (recorded in _STATE and JSON).")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Skip writing _STATE (dry-run / regression-check mode).",
    )
    parser.add_argument("--k", type=int, default=3, help="Top-k cutoff. Default: 3.")
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Eval repetitions for latency averaging. Default: 3.",
    )

    args = parser.parse_args(argv)

    cfg = get_config()
    notes_dir = Path(cfg.ledger_notes_dir)
    cases_filename = SPLITS[args.split]
    cases_path = notes_dir / "08_indices" / cases_filename

    if not cases_path.exists():
        print(f"error: cases file not found: {cases_path}", file=sys.stderr)
        return 1

    retrieval_mode = resolve_retrieval_mode(None)
    embed_backend = resolve_embed_backend(None)
    embed_model = _resolve_embed_model(embed_backend, cfg.embed_model)

    print(
        f"[autoresearch] split={args.split}  cases={cases_filename}"
        f"  k={args.k}  runs={args.runs}  mode={retrieval_mode}",
        file=sys.stderr,
    )

    metrics = run_eval_timed(
        cases_path=cases_path,
        k=args.k,
        runs=args.runs,
        retrieval_mode=retrieval_mode,
        embed_backend=embed_backend,
        embed_model=embed_model,
    )

    # Regression check against _STATE
    prior = load_state()
    regression = False
    if prior is not None:
        delta = metrics["quality"] - prior.get("quality", 0.0)
        print(
            f"[autoresearch] quality={metrics['quality']:.4f}"
            f"  prior={prior.get('quality', 0.0):.4f}"
            f"  delta={delta:+.4f}",
            file=sys.stderr,
        )
        if delta < -1e-9:
            regression = True
            print("[autoresearch] REGRESSION detected vs _STATE", file=sys.stderr)
    else:
        print(
            f"[autoresearch] quality={metrics['quality']:.4f}  (no prior _STATE — first run)",
            file=sys.stderr,
        )

    # Write _STATE (unless --no-write)
    if not args.no_write:
        write_state(metrics, tag=args.tag, split=args.split)

    if args.json:
        output = {
            "tag": args.tag,
            "split": args.split,
            "generated_at": _utc_now(),
            "quality": metrics["quality"],
            "rank1": metrics["rank1"],
            "retrieval_p95_ms": metrics["retrieval_p95_ms"],
            "gold_queries": metrics["gold_queries"],
            "hit1": metrics["hit1"],
            "hitk": metrics["hitk"],
            "mrr": metrics["mrr"],
            "cases": metrics["cases"],
            "k": metrics["k"],
            "status": "regression" if regression else "ok",
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))

    return 2 if regression else 0


if __name__ == "__main__":
    raise SystemExit(main())
