from __future__ import annotations
from ledger.config import get_config
from pathlib import Path
from typing import Any
from typing import Callable
from .apply import run_contradiction_scan


def cmd_sleep_contradictions(*, apply: bool = False) -> int:
    """CLI entry point for `ledger sleep contradictions --check | --apply`.

    When contradiction_enabled=false, prints a notice and returns 0.
    When the semantic index is unavailable, reports clearly and returns 0.
    """
    config = get_config()

    if not config.contradiction_enabled:
        print("=== ledger sleep contradictions ===")
        print("")
        print("contradiction_enabled=false — scan skipped.")
        print("Set contradiction_enabled: true in ~/.config/ledger/config.yaml")
        print("to activate NLI-based contradiction detection.")
        return 0

    mode_label = "--apply" if apply else "--check (dry run — use --apply to write)"
    print(f"=== ledger sleep contradictions {mode_label} ===")
    print("")

    result = run_contradiction_scan(apply=apply)

    if result.missing_embed_index:
        print("ERROR: semantic index unavailable.")
        print("Run `ledger embed build` first, then retry the contradiction scan.")
        return 0  # Exit 0 per spec: report cleanly, no crash

    if not result.nli_available:
        print("ERROR: NLI classifier unavailable.")
        if result.nli_unavailable_reason:
            print(result.nli_unavailable_reason)
        print("Install embeddings dependencies and download the configured model,")
        print("or set contradiction_enabled: false until the model is available.")
        return 0  # Maintenance should report cleanly, not crash.

    print(f"Candidates scanned: {result.candidates_scanned}")
    print(f"Pairs evaluated:    {result.pairs_evaluated}")
    print(f"Supersessions:      {result.supersessions} ({'written' if apply else 'would write'})")
    print(f"Conflict notes:     {result.conflict_notes} ({'written to {INBOX_SUBDIR}' if apply else 'would write'})")
    print(f"Ignored:            {result.ignored}")
    print("")

    if result.pair_results:
        print("Per-pair results:")
        for pr in result.pair_results:
            cand = Path(pr.candidate_ref).name
            neigh = Path(pr.neighbor_ref).name
            print(
                f"  {cand} vs {neigh}: "
                f"score={pr.contradiction_score:.3f} "
                f"decision={pr.decision.value} "
                f"action={pr.action_taken}"
            )

    if not apply:
        print("")
        print("Dry run — no files written. Re-run with --apply to execute.")

    return 0


def eval_t2(
    path: Path | str,
    *,
    thresholds: tuple[float, ...] = (0.60, 0.85),
    score_fn: Callable[[str, str], float] | None = None,
) -> dict[str, Any]:
    """Score the T2 fixture and report precision/recall per language and threshold.

    Split by language because the default model has no Norwegian training data:
    a pooled number would hide exactly the finding the gate exists to surface.
    """
    import yaml

    if score_fn is None:
        from ledger.nli import contradiction_score

        model = get_config().contradiction_model
        score_fn = lambda a, b: contradiction_score(a, b, model_name=model)  # noqa: E731

    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    scored = [(e, float(score_fn(e["note_a"], e["note_b"]))) for e in entries]

    report: dict[str, Any] = {}
    for lang in sorted({str(e["lang"]) for e, _ in scored}):
        rows = [(e["relation"] == "contradiction", s) for e, s in scored if str(e["lang"]) == lang]
        per_threshold = {}
        for t in thresholds:
            tp = sum(1 for pos, s in rows if pos and s >= t)
            fp = sum(1 for pos, s in rows if not pos and s >= t)
            fn = sum(1 for pos, s in rows if pos and s < t)
            per_threshold[f"{t:.2f}"] = {
                "tp": tp, "fp": fp, "fn": fn,
                "precision": tp / (tp + fp) if tp + fp else None,
                "recall": tp / (tp + fn) if tp + fn else None,
            }
        report[lang] = {"pairs": len(rows), "thresholds": per_threshold}
    return report
