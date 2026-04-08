"""Isolated side-probe entrypoint for the A/B harness."""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ledger import ab as ab_lib
from ledger import context as context_mod
from ledger import maintenance as maintenance_mod
from ledger import retrieval as retrieval_mod
from ledger.config import get_config
from ledger.parsing import parse_timestamp


def main() -> int:
    payload = json.loads(sys.argv[1])
    worktree = Path(payload["worktree"]).resolve()
    os.environ["LEDGER_ROOT"] = str(worktree)
    if str(worktree) not in sys.path:
        sys.path.insert(0, str(worktree))

    corpus_dir = payload.get("corpus_dir")
    if corpus_dir:
        corpus_path = Path(corpus_dir).resolve()
        os.environ["LEDGER_NOTES_DIR"] = str(corpus_path / "notes")
        cases_path = str((corpus_path / payload["cases_rel"]).resolve())
    else:
        cases_path = str((worktree / payload["cases_rel"]).resolve())

    ledger_script = ab_lib.load_module_from_script(
        worktree / "scripts" / "ledger",
        "ledger_side_module",
    )
    retrieval_mode = payload["retrieval_mode"]
    embed_backend = payload["embed_backend"]
    embed_model = payload.get("embed_model") or None
    eval_runs = int(payload["eval_runs"])
    query_runs = int(payload["query_runs"])
    k = int(payload["k"])
    cold_query = bool(payload["cold_query"])

    semantic_index = ab_lib.maybe_build_semantic_index(
        ledger_script,
        retrieval_mode=retrieval_mode,
        embed_backend=embed_backend,
        embed_model=embed_model,
        side_label="probe",
    )
    if semantic_index.get("enabled"):
        embed_model = semantic_index["model"]

    ab_lib.reset_module_cache(ledger_script)
    quality = ab_lib.invoke_run_eval(
        ledger_script,
        cases_path,
        k,
        retrieval_mode,
        embed_backend=embed_backend,
        embed_model=embed_model,
    )

    eval_samples = []
    for _ in range(eval_runs):
        ab_lib.reset_module_cache(ledger_script)
        started = time.perf_counter_ns()
        ab_lib.invoke_run_eval(
            ledger_script,
            cases_path,
            k,
            retrieval_mode,
            embed_backend=embed_backend,
            embed_model=embed_model,
        )
        eval_samples.append((time.perf_counter_ns() - started) / 1_000_000.0)

    index_samples = []
    index_runs = max(1, min(3, eval_runs))
    for _ in range(index_runs):
        started = time.perf_counter_ns()
        index_payload = retrieval_mod.rebuild_note_index()
        measured_ms = (time.perf_counter_ns() - started) / 1_000_000.0
        index_samples.append(float(index_payload.get("build_ms", measured_ms) or measured_ms))

    cases = ledger_script.parse_eval_cases(cases_path)
    query_wall_samples = []
    query_total_samples = []
    candidate_build_samples = []
    prefilter_samples = []
    shortlist_samples = []
    scoring_samples = []
    bundle_token_samples = []
    for _ in range(query_runs):
        if not cold_query:
            ab_lib.reset_module_cache(ledger_script)
        for case in cases:
            if cold_query:
                ab_lib.reset_module_cache(ledger_script)
            started = time.perf_counter_ns()
            query_payload = ab_lib.invoke_rank_query(
                ledger_script,
                case.get("query", ""),
                case.get("scope", "all"),
                50,
                retrieval_mode,
                embed_backend=embed_backend,
                embed_model=embed_model,
            )
            query_wall_samples.append((time.perf_counter_ns() - started) / 1_000_000.0)
            timing = getattr(query_payload, "timing", None)
            if timing is None and isinstance(query_payload, dict):
                timing = query_payload.get("timing")
            if timing is not None:
                if isinstance(timing, dict):
                    query_total_samples.append(float(timing.get("total_ms", 0.0) or 0.0))
                    candidate_build_samples.append(float(timing.get("candidates_ms", 0.0) or 0.0))
                    prefilter_samples.append(float(timing.get("prefilter_ms", 0.0) or 0.0))
                    shortlist_samples.append(float(timing.get("shortlist_ms", 0.0) or 0.0))
                    scoring_samples.append(float(timing.get("score_ms", 0.0) or 0.0))
                else:
                    query_total_samples.append(float(getattr(timing, "total_ms", 0.0) or 0.0))
                    candidate_build_samples.append(float(getattr(timing, "candidates_ms", 0.0) or 0.0))
                    prefilter_samples.append(float(getattr(timing, "prefilter_ms", 0.0) or 0.0))
                    shortlist_samples.append(float(getattr(timing, "shortlist_ms", 0.0) or 0.0))
                    scoring_samples.append(float(getattr(timing, "score_ms", 0.0) or 0.0))
            results = getattr(query_payload, "results", None)
            if results is None and isinstance(query_payload, dict):
                results = query_payload.get("results", [])
            if results is None:
                results = []
            bundle = ledger_script.bundle_results(results, word_budget=1200)
            bundle_token_samples.append(sum(len(str(item.get("excerpt", "")).split()) for item in bundle))

    config = get_config()
    notes_dir = config.ledger_notes_dir
    context_text = context_mod.build_context(notes_dir)
    profile_items = context_mod.collect_profile_items(notes_dir)
    profile_tokens = {}
    for scope_name in context_mod.SCOPES:
        profile_markdown, _payload = context_mod.render_profile(scope_name, profile_items)
        profile_tokens[scope_name] = len(profile_markdown.split())

    note_word_counts = []
    for path in maintenance_mod._iter_note_files(include_indices=False):
        note_word_counts.append(len(path.read_text(encoding="utf-8").split()))
    note_word_counts.sort()
    note_p95 = 0.0
    if note_word_counts:
        note_p95 = note_word_counts[max(0, math.ceil(0.95 * len(note_word_counts)) - 1)]

    sync_report = maintenance_mod._compute_sync_report()
    counters = maintenance_mod.LintCounters()
    lint_output = io.StringIO()
    with contextlib.redirect_stdout(lint_output):
        for path in maintenance_mod._iter_note_files(include_indices=False):
            maintenance_mod._lint_note(path, counters)
        maintenance_mod._lint_timeline(config.timeline_path, counters)

    timeline_entries = maintenance_mod._timeline_entries(config.timeline_path)
    sleep_positions = [idx for idx, entry in enumerate(timeline_entries) if entry[2] == "sleep"]
    changes_since_sleep = 0
    days_since_sleep = None
    if sleep_positions:
        sleep_idx = sleep_positions[-1]
        changes_since_sleep = max(0, len(timeline_entries) - sleep_idx - 1)
        last_sleep_ts = timeline_entries[sleep_idx][1]
        last_dt = parse_timestamp(last_sleep_ts)
        if last_dt is not None:
            now_dt = datetime.now(timezone.utc)
            days_since_sleep = max(0, (now_dt.date() - last_dt.date()).days)

    out = {
        "quality": {
            "hit1": float(quality.get("hit1", 0.0)),
            "hitk": float(quality.get("hitk", 0.0)),
            "mrr": float(quality.get("mrr", 0.0)),
            "cases": int(quality.get("cases", 0)),
            "k": int(quality.get("k", k)),
            "failed": quality.get("failed", []),
        },
        "latency": {
            "eval": {"summary": {f"{key}_ms" if key != "count" else key: value for key, value in ab_lib.summarize_distribution(eval_samples).items()}, "samples_ms": eval_samples},
            "query": {
                "summary": {f"{key}_ms" if key != "count" else key: value for key, value in ab_lib.summarize_distribution(query_wall_samples).items()},
                "samples_ms": query_wall_samples,
                "case_count": len(cases),
                "runs": query_runs,
                "cold_query": cold_query,
            },
        },
        "query_metrics": {
            "query_latency_ms": {"summary": {f"{key}_ms" if key != "count" else key: value for key, value in ab_lib.summarize_distribution(query_total_samples).items()}, "samples_ms": query_total_samples},
            "candidate_build_ms": {"summary": {f"{key}_ms" if key != "count" else key: value for key, value in ab_lib.summarize_distribution(candidate_build_samples).items()}, "samples_ms": candidate_build_samples},
            "prefilter_ms": {"summary": {f"{key}_ms" if key != "count" else key: value for key, value in ab_lib.summarize_distribution(prefilter_samples).items()}, "samples_ms": prefilter_samples},
            "shortlist_ms": {"summary": {f"{key}_ms" if key != "count" else key: value for key, value in ab_lib.summarize_distribution(shortlist_samples).items()}, "samples_ms": shortlist_samples},
            "scoring_ms": {"summary": {f"{key}_ms" if key != "count" else key: value for key, value in ab_lib.summarize_distribution(scoring_samples).items()}, "samples_ms": scoring_samples},
            "index_rebuild_ms": {"summary": {f"{key}_ms" if key != "count" else key: value for key, value in ab_lib.summarize_distribution(index_samples).items()}, "samples_ms": index_samples},
        },
        "context_metrics": {
            "boot_context_tokens": len(context_text.split()),
            "boot_context_bytes": len(context_text.encode("utf-8")),
            "profile_tokens": profile_tokens,
            "bundle_tokens": ab_lib.summarize_distribution(bundle_token_samples),
            "notes_total_tokens": int(sum(note_word_counts)),
            "avg_note_words": float(statistics.mean(note_word_counts)) if note_word_counts else 0.0,
            "p95_note_words": float(note_p95),
            "note_count": len(note_word_counts),
        },
        "maintenance_metrics": {
            "sync_state_exists": bool(sync_report.get("state_exists", False)),
            "sync_drift_count": int(len(sync_report.get("unlogged_paths", []))),
            "days_since_sleep": days_since_sleep,
            "changes_since_sleep": int(changes_since_sleep),
            "lint_errors": int(counters.errors),
            "lint_warnings": int(counters.warnings),
        },
        "semantic_index": semantic_index,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
