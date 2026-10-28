"""Telemetry operations for retrieval."""

from __future__ import annotations

from ledger.retrieval_types import RetrievalResult
import datetime as dt
from ledger.layout import indices_dir
import json
import os
from ledger.ranking.settings import _cfg


def _maybe_log_query(result: "RetrievalResult") -> None:
    """Append a JSONL telemetry line if LEDGER_QUERY_LOG=1."""
    if os.environ.get("LEDGER_QUERY_LOG") != "1":
        return
    try:
        log_path = indices_dir(_cfg().ledger_notes_dir) / "query_log.jsonl"
        entry = json.dumps(
            {
                "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "query": result.query,
                "scope": result.scope,
                "mode": result.retrieval_mode,
                "top_3": [r.rel_path for r in result.results[:3]],
                "latency_ms": round(result.timing.total_ms, 1),
                "candidates": result.candidate_pool_size,
            },
            ensure_ascii=False,
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass

