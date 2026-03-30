"""Retrieval evaluation helpers for Cognitive Ledger.

This module owns eval-case parsing/validation and benchmark metrics.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from ledger.config import get_config
from ledger.errors import EvalCaseValidationError
from ledger.retrieval import rank_lexical, resolve_retrieval_mode
from ledger.retrieval_types import RetrievalResult, ScoredResult


def _root_dir() -> Path:
    return get_config().root_dir.resolve()


def _payload_results(payload: RetrievalResult | dict[str, Any]) -> list[ScoredResult | dict[str, Any]]:
    if isinstance(payload, RetrievalResult):
        return payload.results
    return payload["results"]


def _result_path(result: ScoredResult | dict[str, Any]) -> tuple[str, str]:
    if isinstance(result, ScoredResult):
        return result.path, result.rel_path
    return result["path"], result["rel_path"]


def _result_rel_path(result: ScoredResult | dict[str, Any]) -> str:
    if isinstance(result, ScoredResult):
        return result.rel_path
    return result["rel_path"]



def parse_yaml_scalar(value: str) -> str:
    """Parse a lightweight YAML scalar used in eval-cases files."""
    cleaned = value.strip()
    if cleaned.startswith('"') and cleaned.endswith('"'):
        return cleaned[1:-1]
    if cleaned.startswith("'") and cleaned.endswith("'"):
        return cleaned[1:-1]
    return cleaned


def parse_eval_cases(path: str | Path) -> list[dict[str, Any]]:
    """Parse retrieval eval cases from lightweight YAML."""
    cases = []
    current = None
    in_expected = False

    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- query:") or stripped.startswith("- id:"):
            if current:
                cases.append(current)
            current = {
                "id": "",
                "query": "",
                "scope": "all",
                "expected_any": [],
            }
            key, value = stripped[2:].split(":", 1)
            key = key.strip()
            if key in {"id", "query", "scope"}:
                current[key] = parse_yaml_scalar(value)
            in_expected = key == "expected_any"
            continue

        if current is None:
            continue

        field_match = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", stripped)
        if field_match:
            key = field_match.group(1)
            value = field_match.group(2)
            if key in {"id", "query", "scope"}:
                current[key] = parse_yaml_scalar(value)
                in_expected = False
                continue

        if re.match(r"^scope:\s*", stripped):
            current["scope"] = parse_yaml_scalar(stripped.split(":", 1)[1]) or "all"
            in_expected = False
            continue

        if re.match(r"^expected_any:\s*$", stripped):
            in_expected = True
            continue

        if in_expected and re.match(r"^-\s+", stripped):
            current["expected_any"].append(parse_yaml_scalar(stripped[1:].strip()))
            continue

    if current:
        cases.append(current)
    return cases


def extract_notes_relative_path(raw_value: str) -> str:
    """Extract notes/... relative path from absolute or mixed input."""
    normalized = str(raw_value).strip().replace("\\", "/")
    if not normalized:
        return ""
    if normalized.startswith("notes/"):
        return normalized

    marker = "/notes/"
    if marker in normalized:
        suffix = normalized.split(marker, 1)[1].lstrip("/")
        if suffix:
            return f"notes/{suffix}"
    return ""


def path_candidates_from_expected(value: str | Path) -> set[str]:
    """Generate equivalent path forms for expected path matching."""
    raw = str(value).strip()
    if not raw:
        return set()

    path = Path(raw)
    candidates = {raw, raw.replace("\\", "/")}

    if path.is_absolute():
        candidates.add(path.as_posix())
        try:
            rel = path.resolve().relative_to(_root_dir())
            candidates.add(rel.as_posix())
        except ValueError:
            notes_rel = extract_notes_relative_path(raw)
            if notes_rel:
                candidates.add(notes_rel)
    else:
        candidates.add(path.as_posix())
        candidates.add(str((_root_dir() / path).resolve()))
        notes_rel = extract_notes_relative_path(raw)
        if notes_rel:
            candidates.add(notes_rel)

    return {candidate for candidate in candidates if candidate}


def normalize_expected_path(value: str | Path) -> set[str]:
    """Backward-compatible alias."""
    return path_candidates_from_expected(value)


def validate_eval_cases(cases: list[dict[str, Any]], strict_cases: bool = False) -> list[str]:
    """Validate retrieval eval cases and return errors."""
    errors: list[str] = []
    seen_ids = set()
    root_resolved = _root_dir().resolve()

    for idx, case in enumerate(cases, start=1):
        case_id = str(case.get("id", "")).strip()
        case_label = case_id or f"case_{idx}"
        query = str(case.get("query", "")).strip()
        scope = str(case.get("scope", "all")).strip().lower() or "all"
        expected = case.get("expected_any", [])

        if case_id:
            if case_id in seen_ids:
                errors.append(f"{case_label}: duplicate id '{case_id}'")
            seen_ids.add(case_id)
        elif strict_cases:
            errors.append(f"{case_label}: missing required id")

        if not query:
            errors.append(f"{case_label}: missing query")

        if scope not in get_config().query_scopes:
            errors.append(f"{case_label}: invalid scope '{scope}'")

        if not isinstance(expected, list) or not expected:
            errors.append(f"{case_label}: expected_any must contain at least one path")
            continue

        if not strict_cases:
            continue

        for expected_path in expected:
            raw = str(expected_path).strip()
            if not raw:
                errors.append(f"{case_label}: expected_any contains an empty path")
                continue
            candidate_path = Path(raw)
            if candidate_path.is_absolute():
                errors.append(
                    f"{case_label}: expected_any path must be repo-relative notes/... (got absolute: {raw})"
                )
                continue

            rel_path = candidate_path.as_posix()
            if not rel_path.startswith("notes/"):
                errors.append(
                    f"{case_label}: expected_any path must start with 'notes/' (got: {raw})"
                )
                continue

            resolved = (_root_dir() / rel_path).resolve()
            try:
                resolved.relative_to(root_resolved)
            except ValueError:
                errors.append(f"{case_label}: expected_any path escapes repo root (got: {raw})")
                continue

            if not resolved.is_file():
                errors.append(f"{case_label}: expected_any path not found (got: {raw})")

    return errors


def _invoke_rank_query(
    rank_query_fn: Callable[..., RetrievalResult | dict[str, Any]],
    query: str,
    scope: str,
    limit: int,
    retrieval_mode: str,
    embed_backend: str,
    embed_model: str | None,
) -> RetrievalResult | dict[str, Any]:
    """Call rank_query function with best-effort compatibility across signatures."""
    try:
        return rank_query_fn(
            query,
            scope=scope,
            limit=limit,
            retrieval_mode=retrieval_mode,
            embed_backend=embed_backend,
            embed_model=embed_model,
        )
    except TypeError:
        try:
            return rank_query_fn(
                query,
                scope=scope,
                limit=limit,
                retrieval_mode=retrieval_mode,
            )
        except TypeError:
            if retrieval_mode != "legacy":
                raise
            return rank_query_fn(
                query,
                scope=scope,
                limit=limit,
            )


def run_eval(
    cases_path: str | Path,
    k: int,
    strict_cases: bool = False,
    retrieval_mode: str = "legacy",
    embed_backend: str = "local",
    embed_model: str | None = None,
    rank_query_fn: Callable[..., RetrievalResult | dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run retrieval benchmark and compute hit@1/hit@k/MRR."""
    cases = parse_eval_cases(cases_path)
    validation_errors = validate_eval_cases(cases, strict_cases=strict_cases)
    if validation_errors:
        raise EvalCaseValidationError(validation_errors)

    if not cases:
        return {
            "cases": 0,
            "hit1": 0.0,
            "hitk": 0.0,
            "mrr": 0.0,
            "failed": [],
            "k": k,
        }

    ranker = rank_query_fn or rank_lexical
    total = len(cases)
    hit1_count = 0
    hitk_count = 0
    rr_total = 0.0
    failed = []

    for idx, case in enumerate(cases, start=1):
        case_id = str(case.get("id", "")).strip() or f"case_{idx}"
        payload = _invoke_rank_query(
            ranker,
            case["query"],
            scope=case.get("scope", "all"),
            limit=50,
            retrieval_mode=retrieval_mode,
            embed_backend=embed_backend,
            embed_model=embed_model,
        )

        results = _payload_results(payload)
        expected_sets = [path_candidates_from_expected(path) for path in case.get("expected_any", [])]

        best_rank = None
        for result_idx, result in enumerate(results, start=1):
            result_paths = set(_result_path(result))
            matched = any(result_paths & expected for expected in expected_sets)
            if matched:
                best_rank = result_idx
                break

        if best_rank == 1:
            hit1_count += 1
        if best_rank is not None and best_rank <= k:
            hitk_count += 1
        rr_total += (1.0 / best_rank) if best_rank else 0.0

        if best_rank is None or best_rank > k:
            failed.append(
                {
                    "id": case_id,
                    "query": case["query"],
                    "scope": case.get("scope", "all"),
                    "expected_any": case.get("expected_any", []),
                    "top_results": [_result_rel_path(item) for item in results[: min(k, 5)]],
                }
            )

    return {
        "retrieval_mode": resolve_retrieval_mode(retrieval_mode),
        "cases": total,
        "hit1": hit1_count / total,
        "hitk": hitk_count / total,
        "mrr": rr_total / total,
        "failed": failed,
        "k": k,
        "hit1_count": hit1_count,
        "hitk_count": hitk_count,
    }


def print_eval_result(result: dict[str, Any]) -> None:
    """Print eval metrics in CLI-friendly format."""
    cases = result["cases"]
    k = result["k"]
    print(f"cases: {cases}")
    print(f"hit@1: {result['hit1']:.3f} ({result.get('hit1_count', 0)}/{cases})")
    print(f"hit@{k}: {result['hitk']:.3f} ({result.get('hitk_count', 0)}/{cases})")
    print(f"mrr: {result['mrr']:.3f}")

    hitk_ok = result["hitk"] >= 0.80
    mrr_ok = result["mrr"] >= 0.65
    status = "PASS" if hitk_ok and mrr_ok else "FAIL"
    print(f"thresholds: hit@{k}>=0.80, mrr>=0.65 -> {status}")

    if result["failed"]:
        print("failed_cases:")
        for case in result["failed"]:
            print(f"- id: {case.get('id', '')}")
            print(f"  query: {case['query']}")
            print(f"  scope: {case['scope']}")
            print(f"  expected_any: {', '.join(case['expected_any'])}")
            print(f"  top_results: {', '.join(case['top_results'])}")


def baseline_metrics(result: dict[str, Any]) -> dict[str, Any]:
    """Extract compact metric snapshot used as baseline."""
    return {
        "hit1": round(result.get("hit1", 0.0), 6),
        "hitk": round(result.get("hitk", 0.0), 6),
        "mrr": round(result.get("mrr", 0.0), 6),
        "k": int(result.get("k", 3)),
        "cases": int(result.get("cases", 0)),
    }


def build_baseline_snapshot(
    result: dict[str, Any],
    *,
    cases_path: str | Path,
    generated_at: str,
) -> dict[str, Any]:
    """Build a persisted baseline snapshot payload."""
    snapshot = baseline_metrics(result)
    snapshot["generated_at"] = generated_at
    snapshot["cases_path"] = str(Path(cases_path).resolve())
    return snapshot


def write_baseline_snapshot(
    result: dict[str, Any],
    *,
    cases_path: str | Path,
    output_path: str | Path,
    generated_at: str,
) -> dict[str, Any]:
    """Write a baseline snapshot to disk and return the written payload."""
    snapshot = build_baseline_snapshot(
        result,
        cases_path=cases_path,
        generated_at=generated_at,
    )
    Path(output_path).write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return snapshot


def compare_with_baseline(result: dict[str, Any], baseline_path: str | Path) -> dict[str, Any]:
    """Compare current metrics with a baseline snapshot."""
    try:
        baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"available": False, "reason": "baseline file not found"}
    except json.JSONDecodeError:
        return {"available": False, "reason": "baseline file is invalid JSON"}

    current = baseline_metrics(result)
    baseline_hitk = float(baseline.get("hitk", 0.0))
    baseline_mrr = float(baseline.get("mrr", 0.0))
    hitk_delta = current["hitk"] - baseline_hitk
    mrr_delta = current["mrr"] - baseline_mrr
    regressed = (hitk_delta < 0) or (mrr_delta < 0)
    return {
        "available": True,
        "regressed": regressed,
        "baseline": baseline,
        "current": current,
        "hitk_delta": hitk_delta,
        "mrr_delta": mrr_delta,
    }


def format_baseline_comparison(comparison: dict[str, Any], *, k: int) -> str:
    """Format baseline comparison for CLI output."""
    if not comparison.get("available"):
        return f"baseline: unavailable ({comparison.get('reason', 'unknown')})"
    return (
        "baseline_comparison: "
        f"hit@{k} delta={comparison.get('hitk_delta', 0.0):+.3f}, "
        f"mrr delta={comparison.get('mrr_delta', 0.0):+.3f}, "
        f"regressed={'yes' if comparison.get('regressed') else 'no'}"
    )


def eval_result_to_json(
    result: dict[str, Any],
    *,
    default_k: int,
    default_retrieval_mode: str,
    embed_backend: str,
    embed_model: str,
    baseline_path: str | Path | None = None,
    baseline_written: str | Path | None = None,
) -> dict[str, Any]:
    """Build the JSON-friendly eval command payload."""
    payload = {
        "cases": int(result.get("cases", 0)),
        "k": int(result.get("k", default_k)),
        "retrieval_mode": result.get("retrieval_mode", default_retrieval_mode),
        "embed_backend": embed_backend,
        "embed_model": embed_model,
        "hit1": float(result.get("hit1", 0.0)),
        "hitk": float(result.get("hitk", 0.0)),
        "mrr": float(result.get("mrr", 0.0)),
        "hit1_count": int(result.get("hit1_count", 0)),
        "hitk_count": int(result.get("hitk_count", 0)),
        "failed": result.get("failed", []),
    }
    if baseline_path:
        payload["baseline_comparison"] = compare_with_baseline(result, baseline_path)
    if baseline_written:
        payload["baseline_written"] = str(baseline_written)
    return payload


__all__ = [
    "EvalCaseValidationError",
    "parse_yaml_scalar",
    "parse_eval_cases",
    "extract_notes_relative_path",
    "path_candidates_from_expected",
    "normalize_expected_path",
    "validate_eval_cases",
    "run_eval",
    "print_eval_result",
    "baseline_metrics",
    "build_baseline_snapshot",
    "write_baseline_snapshot",
    "compare_with_baseline",
    "format_baseline_comparison",
    "eval_result_to_json",
]
