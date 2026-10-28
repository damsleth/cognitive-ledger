from __future__ import annotations
from ledger.config import get_config
from ledger.layout import INDICES_SUBDIR
from ledger.time_utils import now_iso as _utc_now_iso
from pathlib import Path
from typing import Any
import argparse
import shutil
import sys
import tempfile
from .corpus import InvalidSetupError, compute_corpus_fingerprint, diff_file_maps, is_corpus_root, normalize_cases_path, normalize_corpus_root
from .metrics import EXIT_INVALID_SETUP, decide_outcome
from .probe import EMBED_BACKENDS, resolve_embed_backend, run_probe_for_side
from .report import apply_probe_results, finalize_direct_probe_report, write_json, write_markdown
from .worktree import _create_worktree, _ensure_git_available, _remove_worktree, _resolve_head_commit, _resolve_ref, _resolve_repo_root


RETRIEVAL_MODES: list[str] = get_config().retrieval_modes


def _cli_finalize_direct_probe(
    *,
    report: dict[str, Any],
    corpus_root: Path,
    cases_rel: Path,
    args: argparse.Namespace,
    baseline_probe: dict[str, Any],
    candidate_probe: dict[str, Any],
    json_path: Path,
    markdown_path: Path,
    baseline_commit: str | None = None,
    candidate_commit: str | None = None,
) -> int:
    decision = decide_outcome(
        baseline_quality=baseline_probe["quality"],
        candidate_quality=candidate_probe["quality"],
        baseline_eval_p95_ms=baseline_probe["latency"]["eval"]["summary"]["p95_ms"],
        candidate_eval_p95_ms=candidate_probe["latency"]["eval"]["summary"]["p95_ms"],
        baseline_query_p95_ms=baseline_probe["latency"]["query"]["summary"]["p95_ms"],
        candidate_query_p95_ms=candidate_probe["latency"]["query"]["summary"]["p95_ms"],
        latency_tol_pct=args.latency_tol_pct,
        latency_tol_ms=args.latency_tol_ms,
    )
    finalize_direct_probe_report(
        report,
        corpus_root=corpus_root,
        cases_rel=cases_rel,
        baseline_ref=baseline_commit if baseline_commit is not None else args.baseline_ref,
        candidate_ref=candidate_commit if candidate_commit is not None else args.candidate_ref,
        baseline_probe=baseline_probe,
        candidate_probe=candidate_probe,
        decision=decision,
    )
    write_json(json_path, report)
    write_markdown(markdown_path, report)
    print(
        f"decision={decision['decision']} reason={decision['reason']} exit_code={decision['exit_code']}"
    )
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return int(decision["exit_code"])


def build_cli_argument_parser() -> argparse.ArgumentParser:
    from ledger.config import get_config as _get_config
    from ledger.retrieval import resolve_retrieval_mode as _resolve_retrieval_mode
    _retrieval_modes = _get_config().retrieval_modes
    # Default to the user's *configured* retrieval mode (e.g. semantic_hybrid),
    # not the historical "legacy" hard-default — an A/B without explicit mode
    # flags should benchmark the mode the ledger actually uses. Explicit
    # --baseline-mode / --candidate-mode flags still override.
    _default_mode = _resolve_retrieval_mode(None)
    parser = argparse.ArgumentParser(
        description="A/B harness for Cognitive Ledger retrieval quality and latency"
    )
    parser.add_argument("--baseline-ref", default="main")
    parser.add_argument("--candidate-ref", default="HEAD")
    parser.add_argument("--baseline-mode", choices=_retrieval_modes, default=_default_mode)
    parser.add_argument("--candidate-mode", choices=_retrieval_modes, default=_default_mode)
    parser.add_argument("--baseline-embed-backend", choices=EMBED_BACKENDS, default="local")
    parser.add_argument("--candidate-embed-backend", choices=EMBED_BACKENDS, default="local")
    parser.add_argument("--baseline-embed-model", default=None)
    parser.add_argument("--candidate-embed-model", default=None)
    parser.add_argument(
        "--cases",
        default=f"notes/{INDICES_SUBDIR}/retrieval_eval_cases.yaml",
        help="Logical or corpus-relative path to retrieval eval cases",
    )
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--runs", type=int, help="Set both --eval-runs and --query-runs")
    parser.add_argument("--eval-runs", type=int, default=5)
    parser.add_argument("--query-runs", type=int, default=3)
    parser.add_argument("--allow-corpus-diff", action="store_true")
    parser.add_argument("--cold-query", action="store_true")
    parser.add_argument("--latency-tol-pct", type=float, default=0.05)
    parser.add_argument("--latency-tol-ms", type=float, default=2.0)
    parser.add_argument("--out-dir", help="Output directory for ab_eval.json and ab_eval.md")
    parser.add_argument("--corpus", default=None, help="Path to the ledger notes corpus root")
    parser.add_argument(
        "--baseline-env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Config override for baseline arm (repeatable)",
    )
    parser.add_argument(
        "--candidate-env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Config override for candidate arm (repeatable)",
    )
    return parser


def run_cli_harness(args: argparse.Namespace) -> int:
    from ledger.config import get_config as _get_config

    _ensure_git_available()

    repo_root = _resolve_repo_root()

    eval_runs = args.eval_runs
    query_runs = args.query_runs
    if args.runs is not None:
        eval_runs = args.runs
        query_runs = args.runs

    if args.k <= 0:
        raise InvalidSetupError("--k must be > 0")
    if eval_runs <= 0:
        raise InvalidSetupError("--eval-runs must be > 0")
    if query_runs <= 0:
        raise InvalidSetupError("--query-runs must be > 0")
    if args.latency_tol_pct < 0:
        raise InvalidSetupError("--latency-tol-pct must be >= 0")
    if args.latency_tol_ms < 0:
        raise InvalidSetupError("--latency-tol-ms must be >= 0")

    baseline_embed_backend = resolve_embed_backend(args.baseline_embed_backend)
    candidate_embed_backend = resolve_embed_backend(args.candidate_embed_backend)
    baseline_embed_model = (
        str(args.baseline_embed_model).strip() if args.baseline_embed_model else None
    )
    candidate_embed_model = (
        str(args.candidate_embed_model).strip() if args.candidate_embed_model else None
    )

    configured_corpus_dir = normalize_corpus_root(_get_config().ledger_notes_dir)
    corpus_dir: Path | None = None
    if args.corpus:
        corpus_dir = normalize_corpus_root(Path(args.corpus).expanduser().resolve())
    elif is_corpus_root(configured_corpus_dir):
        corpus_dir = configured_corpus_dir

    if corpus_dir is None:
        raise InvalidSetupError(
            "A/B harness requires --corpus or a configured ledger_notes_dir with an initialized corpus"
        )
    if not is_corpus_root(corpus_dir):
        raise InvalidSetupError(
            f"Corpus path is missing the ledger note layout: {corpus_dir}"
        )

    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path(
        tempfile.mkdtemp(prefix="ledger-ab-")
    ).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "ab_eval.json"
    markdown_path = out_dir / "ab_eval.md"

    print(
        f"A/B run: baseline={args.baseline_ref} (mode={args.baseline_mode}) "
        f"vs candidate={args.candidate_ref} (mode={args.candidate_mode}) "
        f"| repo_root={repo_root} | corpus={corpus_dir}"
    )

    report: dict[str, Any] = {
        "generated_at": _utc_now_iso(),
        "repo_root": str(repo_root),
        "baseline": {
            "ref": args.baseline_ref,
            "retrieval_mode": args.baseline_mode,
            "embed_backend": baseline_embed_backend,
            "embed_model": baseline_embed_model,
        },
        "candidate": {
            "ref": args.candidate_ref,
            "retrieval_mode": args.candidate_mode,
            "embed_backend": candidate_embed_backend,
            "embed_model": candidate_embed_model,
        },
        "settings": {
            "cases": args.cases,
            "k": args.k,
            "runs": args.runs,
            "eval_runs": eval_runs,
            "query_runs": query_runs,
            "baseline_mode": args.baseline_mode,
            "candidate_mode": args.candidate_mode,
            "baseline_embed_backend": baseline_embed_backend,
            "candidate_embed_backend": candidate_embed_backend,
            "baseline_embed_model": baseline_embed_model,
            "candidate_embed_model": candidate_embed_model,
            "allow_corpus_diff": args.allow_corpus_diff,
            "cold_query": args.cold_query,
            "latency_tol_pct": args.latency_tol_pct,
            "latency_tol_ms": args.latency_tol_ms,
        },
    }
    if corpus_dir:
        report["settings"]["corpus"] = str(corpus_dir)

    def _parse_env_overrides(raw: list[str]) -> dict[str, str]:
        overrides: dict[str, str] = {}
        for item in raw:
            if "=" not in item:
                print(f"warning: ignoring malformed env override (expected KEY=VALUE): {item}", file=sys.stderr)
                continue
            key, value = item.split("=", 1)
            overrides[key.strip()] = value.strip()
        return overrides

    baseline_env_overrides = _parse_env_overrides(args.baseline_env)
    candidate_env_overrides = _parse_env_overrides(args.candidate_env)
    if baseline_env_overrides:
        report["baseline"]["env_overrides"] = baseline_env_overrides
    if candidate_env_overrides:
        report["candidate"]["env_overrides"] = candidate_env_overrides

    worktree_paths: list[Path] = []
    temp_worktree_root: Path | None = None

    try:
        # Resolve both refs up front. A ref that does not exist is a hard error
        # (exit 4) — we must NOT silently fall back to probing whatever tree
        # happens to be checked out in repo_root.
        try:
            baseline_commit = _resolve_ref(repo_root, args.baseline_ref)
        except InvalidSetupError as exc:
            raise InvalidSetupError(
                f"cannot resolve --baseline-ref {args.baseline_ref!r} in {repo_root}: {exc}"
            ) from exc
        try:
            candidate_commit = _resolve_ref(repo_root, args.candidate_ref)
        except InvalidSetupError as exc:
            raise InvalidSetupError(
                f"cannot resolve --candidate-ref {args.candidate_ref!r} in {repo_root}: {exc}"
            ) from exc

        head_commit = _resolve_head_commit(repo_root)

        report["baseline"]["commit"] = baseline_commit
        report["candidate"]["commit"] = candidate_commit

        cases_rel = normalize_cases_path(corpus_dir, args.cases)

        if baseline_commit == candidate_commit:
            # Both sides target the same commit. Only probe repo_root directly
            # when it is *already* checked out there; otherwise build a worktree
            # at the resolved commit so we run the named ref's code, not the
            # current working tree.
            if baseline_commit == head_commit:
                probe_root = repo_root
            else:
                temp_worktree_root = Path(
                    tempfile.mkdtemp(prefix="ledger-ab-worktrees-")
                )
                probe_root = temp_worktree_root / "ref"
                _create_worktree(repo_root, args.baseline_ref, probe_root)
                worktree_paths.append(probe_root)

            direct_baseline = run_probe_for_side(
                repo_root, probe_root,
                cases_rel=cases_rel, k=args.k,
                eval_runs=eval_runs, query_runs=query_runs,
                retrieval_mode=args.baseline_mode, cold_query=args.cold_query,
                embed_backend=baseline_embed_backend, embed_model=baseline_embed_model,
                side_label="baseline", corpus_dir=corpus_dir,
                env_overrides=baseline_env_overrides or None,
            )
            direct_candidate = run_probe_for_side(
                repo_root, probe_root,
                cases_rel=cases_rel, k=args.k,
                eval_runs=eval_runs, query_runs=query_runs,
                retrieval_mode=args.candidate_mode, cold_query=args.cold_query,
                embed_backend=candidate_embed_backend, embed_model=candidate_embed_model,
                side_label="candidate", corpus_dir=corpus_dir,
                env_overrides=candidate_env_overrides or None,
            )
            return _cli_finalize_direct_probe(
                report=report, corpus_root=corpus_dir, cases_rel=cases_rel,
                args=args, baseline_probe=direct_baseline, candidate_probe=direct_candidate,
                json_path=json_path, markdown_path=markdown_path,
                baseline_commit=baseline_commit, candidate_commit=candidate_commit,
            )

        temp_worktree_root = Path(tempfile.mkdtemp(prefix="ledger-ab-worktrees-"))
        baseline_wt = temp_worktree_root / "baseline"
        candidate_wt = temp_worktree_root / "candidate"

        _create_worktree(repo_root, args.baseline_ref, baseline_wt)
        worktree_paths.append(baseline_wt)

        _create_worktree(repo_root, args.candidate_ref, candidate_wt)
        worktree_paths.append(candidate_wt)

        baseline_corpus = compute_corpus_fingerprint(corpus_dir, cases_rel)
        candidate_corpus = compute_corpus_fingerprint(corpus_dir, cases_rel)

        report["baseline"]["corpus"] = {
            "fingerprint": baseline_corpus["fingerprint"],
            "file_count": baseline_corpus["file_count"],
        }
        report["candidate"]["corpus"] = {
            "fingerprint": candidate_corpus["fingerprint"],
            "file_count": candidate_corpus["file_count"],
        }

        corpus_diff = diff_file_maps(baseline_corpus["file_map"], candidate_corpus["file_map"])
        report["corpus_diff"] = corpus_diff

        if not args.allow_corpus_diff and not corpus_diff["is_equal"]:
            report["decision"] = {
                "decision": "invalid_setup",
                "reason": "corpus fingerprints differ between baseline and candidate; rerun with --allow-corpus-diff to override",
                "exit_code": EXIT_INVALID_SETUP,
            }
            write_json(json_path, report)
            write_markdown(markdown_path, report)
            print("A/B harness failed: corpus mismatch between refs")
            print(f"JSON report: {json_path}")
            print(f"Markdown report: {markdown_path}")
            return EXIT_INVALID_SETUP

        baseline_probe = run_probe_for_side(
            repo_root, baseline_wt,
            cases_rel=cases_rel, k=args.k,
            eval_runs=eval_runs, query_runs=query_runs,
            retrieval_mode=args.baseline_mode, cold_query=args.cold_query,
            embed_backend=baseline_embed_backend, embed_model=baseline_embed_model,
            side_label="baseline", corpus_dir=corpus_dir,
            env_overrides=baseline_env_overrides or None,
        )
        candidate_probe = run_probe_for_side(
            repo_root, candidate_wt,
            cases_rel=cases_rel, k=args.k,
            eval_runs=eval_runs, query_runs=query_runs,
            retrieval_mode=args.candidate_mode, cold_query=args.cold_query,
            embed_backend=candidate_embed_backend, embed_model=candidate_embed_model,
            side_label="candidate", corpus_dir=corpus_dir,
            env_overrides=candidate_env_overrides or None,
        )

        decision = decide_outcome(
            baseline_quality=baseline_probe["quality"],
            candidate_quality=candidate_probe["quality"],
            baseline_eval_p95_ms=baseline_probe["latency"]["eval"]["summary"]["p95_ms"],
            candidate_eval_p95_ms=candidate_probe["latency"]["eval"]["summary"]["p95_ms"],
            baseline_query_p95_ms=baseline_probe["latency"]["query"]["summary"]["p95_ms"],
            candidate_query_p95_ms=candidate_probe["latency"]["query"]["summary"]["p95_ms"],
            latency_tol_pct=args.latency_tol_pct,
            latency_tol_ms=args.latency_tol_ms,
        )
        apply_probe_results(
            report,
            baseline_probe=baseline_probe,
            candidate_probe=candidate_probe,
            decision=decision,
        )
        if report["baseline"]["semantic_index"].get("enabled"):
            report["baseline"]["embed_model"] = report["baseline"]["semantic_index"].get(
                "model", baseline_embed_model
            )
        if report["candidate"]["semantic_index"].get("enabled"):
            report["candidate"]["embed_model"] = report["candidate"]["semantic_index"].get(
                "model", candidate_embed_model
            )

        write_json(json_path, report)
        write_markdown(markdown_path, report)

        print(
            f"decision={decision['decision']} reason={decision['reason']} exit_code={decision['exit_code']}"
        )
        print(f"JSON report: {json_path}")
        print(f"Markdown report: {markdown_path}")

        return int(decision["exit_code"])

    except InvalidSetupError as exc:
        report["decision"] = {
            "decision": "invalid_setup",
            "reason": str(exc),
            "exit_code": EXIT_INVALID_SETUP,
        }
        write_json(json_path, report)
        write_markdown(markdown_path, report)
        print(f"A/B harness failed: {exc}")
        print(f"JSON report: {json_path}")
        print(f"Markdown report: {markdown_path}")
        return EXIT_INVALID_SETUP

    finally:
        for worktree in reversed(worktree_paths):
            _remove_worktree(repo_root, worktree)
        if temp_worktree_root is not None:
            shutil.rmtree(temp_worktree_root, ignore_errors=True)


def main_cli(argv: list[str] | None = None) -> int:
    parser = build_cli_argument_parser()
    args = parser.parse_args(argv)
    return run_cli_harness(args)
