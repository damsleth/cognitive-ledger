"""Shared helpers for the ledger A/B harness."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import math
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_BENEFICIAL = 0
EXIT_REGRESSION = 2
EXIT_NEUTRAL = 3
EXIT_INVALID_SETUP = 4

EPSILON = 1e-9
QUALITY_KEYS = ("hit1", "hitk", "mrr")
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
EMBED_BACKENDS = ("local", "openai")


class InvalidSetupError(Exception):
    """Raised when the A/B harness cannot produce a valid comparison."""


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


def normalize_cases_path(repo_root: Path, cases_arg: str) -> Path:
    candidate = Path(cases_arg)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(repo_root.resolve())
        except ValueError as exc:
            raise InvalidSetupError(
                "--cases absolute path must point inside the ledger repository"
            ) from exc
    return candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_corpus_paths(worktree_root: Path, cases_rel_path: Path) -> list[Path]:
    patterns = [
        "notes/02_facts/**/*.md",
        "notes/03_preferences/**/*.md",
        "notes/04_goals/**/*.md",
        "notes/05_open_loops/**/*.md",
        "notes/06_concepts/**/*.md",
        "notes/08_indices/aliases.json",
    ]

    files: set[Path] = set()
    for pattern in patterns:
        for path in sorted(worktree_root.glob(pattern)):
            if path.is_file():
                files.add(path.resolve())

    cases_path = (worktree_root / cases_rel_path).resolve()
    if not cases_path.is_file():
        raise InvalidSetupError(
            f"Cases file not found in worktree: {cases_rel_path.as_posix()}"
        )
    files.add(cases_path)

    return sorted(files, key=lambda item: item.as_posix())


def build_corpus_file_map(worktree_root: Path, cases_rel_path: Path) -> dict[str, str]:
    file_map: dict[str, str] = {}
    for path in collect_corpus_paths(worktree_root, cases_rel_path):
        rel = path.relative_to(worktree_root.resolve()).as_posix()
        file_map[rel] = sha256_file(path)
    return file_map


def compute_fingerprint(file_map: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for rel_path in sorted(file_map):
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_map[rel_path].encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def compute_corpus_fingerprint(
    worktree_root: Path, cases_rel_path: Path
) -> dict[str, Any]:
    file_map = build_corpus_file_map(worktree_root, cases_rel_path)
    return {
        "fingerprint": compute_fingerprint(file_map),
        "file_count": len(file_map),
        "file_map": file_map,
    }


def diff_file_maps(
    baseline_map: dict[str, str], candidate_map: dict[str, str]
) -> dict[str, Any]:
    baseline_keys = set(baseline_map.keys())
    candidate_keys = set(candidate_map.keys())

    added = sorted(candidate_keys - baseline_keys)
    removed = sorted(baseline_keys - candidate_keys)
    changed = sorted(
        key
        for key in (baseline_keys & candidate_keys)
        if baseline_map[key] != candidate_map[key]
    )

    changed_details = [
        {
            "path": key,
            "baseline_hash": baseline_map[key],
            "candidate_hash": candidate_map[key],
        }
        for key in changed
    ]

    return {
        "added": added,
        "removed": removed,
        "changed": changed_details,
        "is_equal": not (added or removed or changed),
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


def resolve_embed_backend(embed_backend: str | None) -> str:
    backend = str(embed_backend or "").strip().lower() or "local"
    if backend not in EMBED_BACKENDS:
        raise InvalidSetupError(
            f"Unsupported embedding backend: {backend} (expected one of {', '.join(EMBED_BACKENDS)})"
        )
    return backend


def load_module_from_script(script_path: Path, module_name: str) -> Any:
    """Load an extensionless Python script as a module."""
    loader = importlib.machinery.SourceFileLoader(module_name, str(script_path))
    spec = importlib.util.spec_from_file_location(module_name, str(script_path), loader=loader)
    if spec is None or spec.loader is None:
        raise InvalidSetupError(f"Could not load module spec from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repo_python(repo_root: Path) -> str:
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    return sys.executable


def reset_module_cache(module: Any) -> None:
    clear_fn = getattr(module, "clear_candidate_cache", None)
    if callable(clear_fn):
        clear_fn()
        return
    if hasattr(module, "_CANDIDATE_CACHE"):
        setattr(module, "_CANDIDATE_CACHE", None)


def invoke_run_eval(
    module: Any,
    cases_path: str,
    k: int,
    retrieval_mode: str,
    embed_backend: str = "local",
    embed_model: str | None = None,
) -> Any:
    try:
        return module.run_eval(
            cases_path,
            k=k,
            retrieval_mode=retrieval_mode,
            embed_backend=embed_backend,
            embed_model=embed_model,
        )
    except TypeError as exc:
        message = str(exc)
        if ("embed_backend" in message) or ("embed_model" in message):
            try:
                return module.run_eval(cases_path, k=k, retrieval_mode=retrieval_mode)
            except TypeError as legacy_exc:
                if "retrieval_mode" not in str(legacy_exc):
                    raise
                return module.run_eval(cases_path, k=k)
        if "retrieval_mode" in message:
            return module.run_eval(cases_path, k=k)
        raise


def invoke_rank_query(
    module: Any,
    query: str,
    scope: str,
    limit: int,
    retrieval_mode: str,
    embed_backend: str = "local",
    embed_model: str | None = None,
) -> Any:
    try:
        return module.rank_query(
            query,
            scope=scope,
            limit=limit,
            retrieval_mode=retrieval_mode,
            embed_backend=embed_backend,
            embed_model=embed_model,
        )
    except TypeError as exc:
        message = str(exc)
        if ("embed_backend" in message) or ("embed_model" in message):
            try:
                return module.rank_query(
                    query,
                    scope=scope,
                    limit=limit,
                    retrieval_mode=retrieval_mode,
                )
            except TypeError as legacy_exc:
                if "retrieval_mode" not in str(legacy_exc):
                    raise
                return module.rank_query(query, scope=scope, limit=limit)
        if "retrieval_mode" in message:
            return module.rank_query(query, scope=scope, limit=limit)
        raise


def run_quality_eval(
    module: Any,
    cases_path: Path,
    k: int,
    retrieval_mode: str,
    embed_backend: str = "local",
    embed_model: str | None = None,
) -> dict[str, float | int]:
    result = invoke_run_eval(
        module,
        str(cases_path),
        k,
        retrieval_mode,
        embed_backend=embed_backend,
        embed_model=embed_model,
    )
    return {
        "hit1": float(result.get("hit1", 0.0)),
        "hitk": float(result.get("hitk", 0.0)),
        "mrr": float(result.get("mrr", 0.0)),
        "cases": int(result.get("cases", 0)),
        "k": int(result.get("k", k)),
    }


def probe_eval_latency(
    module: Any,
    cases_path: Path,
    k: int,
    runs: int,
    retrieval_mode: str,
    embed_backend: str = "local",
    embed_model: str | None = None,
) -> dict[str, Any]:
    samples: list[float] = []
    for _ in range(runs):
        reset_module_cache(module)
        start = time.perf_counter_ns()
        invoke_run_eval(
            module,
            str(cases_path),
            k,
            retrieval_mode,
            embed_backend=embed_backend,
            embed_model=embed_model,
        )
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000.0
        samples.append(elapsed_ms)

    summary = summarize_latency(samples)
    return {"summary": summary.to_dict(), "samples_ms": samples}


def probe_query_latency(
    module: Any,
    cases_path: Path,
    runs: int,
    retrieval_mode: str,
    cold_query: bool = False,
    embed_backend: str = "local",
    embed_model: str | None = None,
) -> dict[str, Any]:
    cases = module.parse_eval_cases(str(cases_path))
    if not cases:
        raise InvalidSetupError("No eval cases found for query latency probe")

    samples: list[float] = []
    for _ in range(runs):
        if not cold_query:
            reset_module_cache(module)
        for case in cases:
            query = case.get("query", "")
            scope = case.get("scope", "all")
            if cold_query:
                reset_module_cache(module)
            start = time.perf_counter_ns()
            invoke_rank_query(
                module,
                query,
                scope,
                50,
                retrieval_mode,
                embed_backend=embed_backend,
                embed_model=embed_model,
            )
            elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000.0
            samples.append(elapsed_ms)

    summary = summarize_latency(samples)
    return {
        "summary": summary.to_dict(),
        "samples_ms": samples,
        "case_count": len(cases),
        "runs": runs,
        "cold_query": bool(cold_query),
    }


def maybe_build_semantic_index(
    module: Any,
    retrieval_mode: str,
    embed_backend: str,
    embed_model: str | None,
    side_label: str,
) -> dict[str, Any]:
    if retrieval_mode != "semantic_hybrid":
        return {
            "enabled": False,
            "reason": "retrieval_mode_not_semantic_hybrid",
        }

    backend = resolve_embed_backend(embed_backend)
    embeddings = None
    load_fn = getattr(module, "load_embeddings_module", None)
    if callable(load_fn):
        try:
            embeddings = load_fn()
        except Exception as exc:
            raise InvalidSetupError(
                f"{side_label}: failed to load embeddings module for semantic_hybrid: {exc}"
            ) from exc
    else:
        module_file = Path(getattr(module, "__file__", "")).resolve()
        embeddings_path = module_file.parent / "ledger_embeddings.py"
        if not embeddings_path.is_file():
            raise InvalidSetupError(
                f"{side_label}: semantic_hybrid requires {embeddings_path} in tested ref"
            )
        embeddings = load_module_from_script(
            embeddings_path, f"ledger_embeddings_{side_label}_module"
        )

    resolved_model = str(embed_model).strip() if embed_model else embeddings.default_model_for_backend(backend)
    try:
        payload = embeddings.build_indices(
            target="ledger",
            backend=backend,
            model=resolved_model,
            write_manifest=False,
            append_timeline=False,
        )
    except Exception as exc:
        raise InvalidSetupError(
            f"{side_label}: failed to build semantic index ({backend}/{resolved_model}): {exc}"
        ) from exc

    result = (payload.get("results") or [{}])[0]
    return {
        "enabled": True,
        "backend": backend,
        "model": str(result.get("model", resolved_model)),
        "item_count": int(result.get("item_count", 0)),
        "dims": int(result.get("dims", 0)),
        "embedded_count": int(result.get("embedded_count", 0)),
        "reused_count": int(result.get("reused_count", 0)),
        "removed_count": int(result.get("removed_count", 0)),
        "built_at": str(result.get("built_at", "")),
    }


def run_probe_for_side(
    repo_root: Path,
    worktree_root: Path,
    *,
    cases_rel: Path,
    k: int,
    eval_runs: int,
    query_runs: int,
    retrieval_mode: str,
    cold_query: bool,
    embed_backend: str,
    embed_model: str | None,
    side_label: str,
    corpus_dir: "Path | None" = None,
) -> dict[str, Any]:
    probe_payload = {
        "worktree": str(worktree_root),
        "cases_rel": cases_rel.as_posix(),
        "k": k,
        "eval_runs": eval_runs,
        "query_runs": query_runs,
        "retrieval_mode": retrieval_mode,
        "cold_query": cold_query,
        "embed_backend": embed_backend,
        "embed_model": embed_model,
    }
    if corpus_dir is not None:
        probe_payload["corpus_dir"] = str(corpus_dir)
    process = subprocess.run(
        [repo_python(repo_root), "-m", "ledger.ab_probe", json.dumps(probe_payload)],
        cwd=str(worktree_root),
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "probe failed"
        raise InvalidSetupError(f"{side_label}: {detail}")
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise InvalidSetupError(
            f"{side_label}: invalid JSON from isolated probe"
        ) from exc


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

    if eval_ok and query_ok:
        return {
            "decision": "beneficial",
            "reason": "quality tied and candidate met latency tie-break thresholds",
            "exit_code": EXIT_BENEFICIAL,
            "quality_deltas": deltas,
            "regressed_metrics": regressed,
            "improved_metrics": improved,
            "latency": latency_info,
        }

    return {
        "decision": "neutral",
        "reason": "quality tied but candidate failed latency tie-break thresholds",
        "exit_code": EXIT_NEUTRAL,
        "quality_deltas": deltas,
        "regressed_metrics": regressed,
        "improved_metrics": improved,
        "latency": latency_info,
    }


def apply_probe_results(
    report: dict[str, Any],
    *,
    baseline_probe: dict[str, Any],
    candidate_probe: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    report["baseline"]["semantic_index"] = baseline_probe.get("semantic_index", {})
    report["candidate"]["semantic_index"] = candidate_probe.get("semantic_index", {})
    report["baseline"]["quality"] = baseline_probe["quality"]
    report["candidate"]["quality"] = candidate_probe["quality"]
    report["baseline"]["latency"] = baseline_probe["latency"]
    report["candidate"]["latency"] = candidate_probe["latency"]
    report["baseline"]["query_metrics"] = baseline_probe["query_metrics"]
    report["candidate"]["query_metrics"] = candidate_probe["query_metrics"]
    report["baseline"]["context_metrics"] = baseline_probe["context_metrics"]
    report["candidate"]["context_metrics"] = candidate_probe["context_metrics"]
    report["baseline"]["maintenance_metrics"] = baseline_probe["maintenance_metrics"]
    report["candidate"]["maintenance_metrics"] = candidate_probe["maintenance_metrics"]
    report["decision"] = decision
    report["deltas"] = {
        "quality": decision.get("quality_deltas", {}),
        "latency_p95_ms": {
            "eval": candidate_probe["latency"]["eval"]["summary"]["p95_ms"]
            - baseline_probe["latency"]["eval"]["summary"]["p95_ms"],
            "query": candidate_probe["latency"]["query"]["summary"]["p95_ms"]
            - baseline_probe["latency"]["query"]["summary"]["p95_ms"],
        },
        "query_metrics": {
            metric: round(
                float(candidate_probe["query_metrics"][metric]["summary"].get("p95_ms", 0.0))
                - float(baseline_probe["query_metrics"][metric]["summary"].get("p95_ms", 0.0)),
                6,
            )
            for metric in QUERY_METRIC_KEYS
        },
        "context_metrics": {
            "boot_context_tokens": int(candidate_probe["context_metrics"].get("boot_context_tokens", 0))
            - int(baseline_probe["context_metrics"].get("boot_context_tokens", 0)),
            "boot_context_bytes": int(candidate_probe["context_metrics"].get("boot_context_bytes", 0))
            - int(baseline_probe["context_metrics"].get("boot_context_bytes", 0)),
            "bundle_tokens_p95": round(
                float(candidate_probe["context_metrics"]["bundle_tokens"].get("p95", 0.0))
                - float(baseline_probe["context_metrics"]["bundle_tokens"].get("p95", 0.0)),
                6,
            ),
            "notes_total_tokens": int(candidate_probe["context_metrics"].get("notes_total_tokens", 0))
            - int(baseline_probe["context_metrics"].get("notes_total_tokens", 0)),
            "avg_note_words": round(
                float(candidate_probe["context_metrics"].get("avg_note_words", 0.0))
                - float(baseline_probe["context_metrics"].get("avg_note_words", 0.0)),
                6,
            ),
            "p95_note_words": round(
                float(candidate_probe["context_metrics"].get("p95_note_words", 0.0))
                - float(baseline_probe["context_metrics"].get("p95_note_words", 0.0)),
                6,
            ),
        },
        "maintenance_metrics": {
            metric: int(candidate_probe["maintenance_metrics"].get(metric, 0))
            - int(baseline_probe["maintenance_metrics"].get(metric, 0))
            for metric in MAINTENANCE_METRIC_KEYS
        },
    }
    return report


def finalize_direct_probe_report(
    report: dict[str, Any],
    *,
    repo_root: Path,
    cases_rel: Path,
    baseline_ref: str,
    candidate_ref: str,
    baseline_probe: dict[str, Any],
    candidate_probe: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    corpus = compute_corpus_fingerprint(repo_root, cases_rel)
    report["baseline"]["commit"] = baseline_ref
    report["candidate"]["commit"] = candidate_ref
    report["baseline"]["corpus"] = {
        "fingerprint": corpus["fingerprint"],
        "file_count": corpus["file_count"],
    }
    report["candidate"]["corpus"] = {
        "fingerprint": corpus["fingerprint"],
        "file_count": corpus["file_count"],
    }
    report["corpus_diff"] = {"added": [], "removed": [], "changed": [], "is_equal": True}
    return apply_probe_results(
        report,
        baseline_probe=baseline_probe,
        candidate_probe=candidate_probe,
        decision=decision,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def format_ms(value: float) -> str:
    return f"{value:.3f}"


def build_markdown_report(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Ledger A/B Evaluation")
    lines.append("")
    lines.append(f"Generated: `{payload['generated_at']}`")
    lines.append("")

    decision = payload.get("decision", {})
    lines.append(f"**Decision:** `{decision.get('decision', 'invalid_setup')}`")
    lines.append(f"**Reason:** {decision.get('reason', 'n/a')}")
    lines.append("")

    baseline = payload.get("baseline", {})
    candidate = payload.get("candidate", {})

    lines.append("## Refs")
    lines.append("")
    lines.append("| Side | Ref | Commit | Mode | Embed |")
    lines.append("| --- | --- | --- | --- | --- |")
    baseline_embed = (
        f"{baseline.get('embed_backend', 'local')}/{baseline.get('embed_model') or '(default)'}"
    )
    candidate_embed = (
        f"{candidate.get('embed_backend', 'local')}/{candidate.get('embed_model') or '(default)'}"
    )
    lines.append(
        f"| Baseline | `{baseline.get('ref', '')}` | `{baseline.get('commit', '')}` | `{baseline.get('retrieval_mode', 'legacy')}` | `{baseline_embed}` |"
    )
    lines.append(
        f"| Candidate | `{candidate.get('ref', '')}` | `{candidate.get('commit', '')}` | `{candidate.get('retrieval_mode', 'legacy')}` | `{candidate_embed}` |"
    )
    lines.append("")

    if baseline.get("quality") and candidate.get("quality"):
        lines.append("## Quality")
        lines.append("")
        lines.append("| Metric | Baseline | Candidate | Delta |")
        lines.append("| --- | ---: | ---: | ---: |")
        quality_deltas = decision.get("quality_deltas", {})
        for metric in QUALITY_KEYS:
            b_val = float(baseline["quality"].get(metric, 0.0))
            c_val = float(candidate["quality"].get(metric, 0.0))
            delta = float(quality_deltas.get(metric, c_val - b_val))
            lines.append(
                f"| {metric} | {b_val:.6f} | {c_val:.6f} | {delta:+.6f} |"
            )
        lines.append("")

    if baseline.get("latency") and candidate.get("latency"):
        lines.append("## Latency")
        lines.append("")
        lines.append("| Probe | Baseline p95 (ms) | Candidate p95 (ms) | Threshold (ms) | Pass |")
        lines.append("| --- | ---: | ---: | ---: | --- |")

        tie = decision.get("latency") or {}
        eval_tie = tie.get("eval", {})
        query_tie = tie.get("query", {})

        base_eval_p95 = float(baseline["latency"]["eval"]["summary"]["p95_ms"])
        cand_eval_p95 = float(candidate["latency"]["eval"]["summary"]["p95_ms"])
        base_query_p95 = float(baseline["latency"]["query"]["summary"]["p95_ms"])
        cand_query_p95 = float(candidate["latency"]["query"]["summary"]["p95_ms"])

        eval_threshold = float(eval_tie.get("threshold_ms", 0.0))
        query_threshold = float(query_tie.get("threshold_ms", 0.0))

        eval_pass = "yes" if eval_tie.get("passed") else "no"
        query_pass = "yes" if query_tie.get("passed") else "no"

        lines.append(
            "| eval run | "
            f"{format_ms(base_eval_p95)} | {format_ms(cand_eval_p95)} | "
            f"{format_ms(eval_threshold)} | {eval_pass} |"
        )
        lines.append(
            "| query run | "
            f"{format_ms(base_query_p95)} | {format_ms(cand_query_p95)} | "
            f"{format_ms(query_threshold)} | {query_pass} |"
        )
        lines.append("")

    if baseline.get("query_metrics") and candidate.get("query_metrics"):
        lines.append("## Query Breakdown")
        lines.append("")
        lines.append("| Metric | Baseline p95 | Candidate p95 | Delta |")
        lines.append("| --- | ---: | ---: | ---: |")
        for metric in QUERY_METRIC_KEYS:
            base_p95 = float(baseline["query_metrics"][metric]["summary"].get("p95_ms", 0.0))
            cand_p95 = float(candidate["query_metrics"][metric]["summary"].get("p95_ms", 0.0))
            lines.append(
                f"| {metric} | {base_p95:.3f} | {cand_p95:.3f} | {cand_p95 - base_p95:+.3f} |"
            )
        lines.append("")

    if baseline.get("context_metrics") and candidate.get("context_metrics"):
        lines.append("## Context")
        lines.append("")
        lines.append("| Metric | Baseline | Candidate | Delta |")
        lines.append("| --- | ---: | ---: | ---: |")
        lines.append(
            f"| boot_context_tokens | {int(baseline['context_metrics'].get('boot_context_tokens', 0))} | {int(candidate['context_metrics'].get('boot_context_tokens', 0))} | {int(candidate['context_metrics'].get('boot_context_tokens', 0)) - int(baseline['context_metrics'].get('boot_context_tokens', 0)):+d} |"
        )
        lines.append(
            f"| boot_context_bytes | {int(baseline['context_metrics'].get('boot_context_bytes', 0))} | {int(candidate['context_metrics'].get('boot_context_bytes', 0))} | {int(candidate['context_metrics'].get('boot_context_bytes', 0)) - int(baseline['context_metrics'].get('boot_context_bytes', 0)):+d} |"
        )
        lines.append(
            f"| bundle_tokens_p95 | {float(baseline['context_metrics']['bundle_tokens'].get('p95', 0.0)):.3f} | {float(candidate['context_metrics']['bundle_tokens'].get('p95', 0.0)):.3f} | {float(candidate['context_metrics']['bundle_tokens'].get('p95', 0.0)) - float(baseline['context_metrics']['bundle_tokens'].get('p95', 0.0)):+.3f} |"
        )
        lines.append(
            f"| notes_total_tokens | {int(baseline['context_metrics'].get('notes_total_tokens', 0))} | {int(candidate['context_metrics'].get('notes_total_tokens', 0))} | {int(candidate['context_metrics'].get('notes_total_tokens', 0)) - int(baseline['context_metrics'].get('notes_total_tokens', 0)):+d} |"
        )
        lines.append(
            f"| avg_note_words | {float(baseline['context_metrics'].get('avg_note_words', 0.0)):.3f} | {float(candidate['context_metrics'].get('avg_note_words', 0.0)):.3f} | {float(candidate['context_metrics'].get('avg_note_words', 0.0)) - float(baseline['context_metrics'].get('avg_note_words', 0.0)):+.3f} |"
        )
        lines.append(
            f"| p95_note_words | {float(baseline['context_metrics'].get('p95_note_words', 0.0)):.3f} | {float(candidate['context_metrics'].get('p95_note_words', 0.0)):.3f} | {float(candidate['context_metrics'].get('p95_note_words', 0.0)) - float(baseline['context_metrics'].get('p95_note_words', 0.0)):+.3f} |"
        )
        lines.append("")
        lines.append("Profile token counts:")
        for scope_name in PROFILE_TOKEN_SCOPES:
            base_value = int(baseline["context_metrics"].get("profile_tokens", {}).get(scope_name, 0))
            cand_value = int(candidate["context_metrics"].get("profile_tokens", {}).get(scope_name, 0))
            lines.append(
                f"- {scope_name}: baseline={base_value}, candidate={cand_value}, delta={cand_value - base_value:+d}"
            )
        lines.append("")

    if baseline.get("maintenance_metrics") and candidate.get("maintenance_metrics"):
        lines.append("## Maintenance")
        lines.append("")
        lines.append("| Metric | Baseline | Candidate | Delta |")
        lines.append("| --- | ---: | ---: | ---: |")
        for metric in MAINTENANCE_METRIC_KEYS:
            base_value = int(baseline["maintenance_metrics"].get(metric, 0))
            cand_value = int(candidate["maintenance_metrics"].get(metric, 0))
            lines.append(f"| {metric} | {base_value} | {cand_value} | {cand_value - base_value:+d} |")
        lines.append("")
        lines.append(
            f"Days since sleep: baseline={baseline['maintenance_metrics'].get('days_since_sleep', 'n/a')}, candidate={candidate['maintenance_metrics'].get('days_since_sleep', 'n/a')}"
        )
        lines.append("")

    if payload.get("corpus_diff"):
        corpus_diff = payload["corpus_diff"]
        lines.append("## Corpus")
        lines.append("")
        lines.append(
            f"Fingerprints equal: **{'yes' if corpus_diff.get('is_equal') else 'no'}**"
        )

        added = corpus_diff.get("added", [])
        removed = corpus_diff.get("removed", [])
        changed = corpus_diff.get("changed", [])

        if added:
            lines.append("")
            lines.append("Added paths in candidate:")
            for item in added:
                lines.append(f"- `{item}`")

        if removed:
            lines.append("")
            lines.append("Removed paths from baseline:")
            for item in removed:
                lines.append(f"- `{item}`")

        if changed:
            lines.append("")
            lines.append("Changed paths:")
            for item in changed:
                lines.append(f"- `{item['path']}`")

        lines.append("")

    lines.append("## Gate")
    lines.append("")
    lines.append(
        "- Fail as `regression` if any of `hit1`, `hitk`, `mrr` decreases by more than 1e-9."
    )
    lines.append(
        "- If quality improves on any metric (with no regressions), mark `beneficial`."
    )
    lines.append(
        "- If quality ties, require candidate p95 for both eval/query to stay within threshold."
    )

    return "\n".join(lines) + "\n"


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(build_markdown_report(payload), encoding="utf-8")
