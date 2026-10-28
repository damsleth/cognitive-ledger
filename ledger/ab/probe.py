from __future__ import annotations
from ledger.eval import parse_eval_cases as _parse_eval_cases
from pathlib import Path
from typing import Any
import json
import subprocess
import sys
import time
from .corpus import InvalidSetupError
from .metrics import summarize_latency


EMBED_BACKENDS = ("local", "openai")


def resolve_embed_backend(embed_backend: str | None) -> str:
    backend = str(embed_backend or "").strip().lower() or "local"
    if backend not in EMBED_BACKENDS:
        raise InvalidSetupError(
            f"Unsupported embedding backend: {backend} (expected one of {', '.join(EMBED_BACKENDS)})"
        )
    return backend


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
        "negative_cases": int(result.get("negative_cases", 0)),
        "false_positive_rate": float(result.get("false_positive_rate", 0.0)),
        "by_lang": result.get("by_lang", {}),
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
    parse_fn = getattr(module, "parse_eval_cases", None) or _parse_eval_cases
    cases = parse_fn(str(cases_path))
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
    load_fn = getattr(module, "load_embeddings_module", None)
    if not callable(load_fn):
        raise InvalidSetupError(
            f"{side_label}: tested ref does not expose load_embeddings_module; semantic_hybrid is unavailable"
        )
    try:
        embeddings = load_fn()
    except Exception as exc:
        raise InvalidSetupError(
            f"{side_label}: failed to load embeddings module for semantic_hybrid: {exc}"
        ) from exc

    resolved_model = embeddings.configured_model_for_backend(backend, embed_model)
    try:
        payload = embeddings.build_indices(
            target="ledger",
            backend=backend,
            model=resolved_model,
            write_manifest=False,
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
    env_overrides: "dict[str, str] | None" = None,
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
    if env_overrides:
        probe_payload["env_overrides"] = env_overrides
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
