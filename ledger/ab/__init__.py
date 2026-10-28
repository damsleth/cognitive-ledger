"""Compatibility imports used outside this package."""
from .corpus import InvalidSetupError, compute_corpus_fingerprint, is_corpus_root, normalize_cases_path, normalize_corpus_root
from .metrics import EPSILON, EXIT_BENEFICIAL, EXIT_INVALID_SETUP, EXIT_NEUTRAL, EXIT_REGRESSION, MAINTENANCE_METRIC_KEYS, QUERY_METRIC_KEYS, compute_objective, decide_outcome, summarize_distribution
from .probe import invoke_rank_query, invoke_run_eval, maybe_build_semantic_index, probe_eval_latency, probe_query_latency, reset_module_cache, run_probe_for_side, run_quality_eval
from .report import apply_probe_results, build_markdown_report
from .worktree import _is_ledger_code_repo, _resolve_repo_root
from .cli import RETRIEVAL_MODES, build_cli_argument_parser, main_cli, run_cli_harness
