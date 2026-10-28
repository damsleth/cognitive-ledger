from __future__ import annotations
from pathlib import Path
from typing import Any
import json
from .corpus import compute_corpus_fingerprint
from .metrics import MAINTENANCE_METRIC_KEYS, PROFILE_TOKEN_SCOPES, QUALITY_KEYS, QUERY_METRIC_KEYS


def apply_probe_results(
    report: dict[str, Any],
    *,
    baseline_probe: dict[str, Any],
    candidate_probe: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    report["baseline"]["semantic_index"] = baseline_probe.get("semantic_index", {})
    report["candidate"]["semantic_index"] = candidate_probe.get("semantic_index", {})
    report["baseline"]["applied_env_overrides"] = baseline_probe.get(
        "applied_env_overrides", {}
    )
    report["candidate"]["applied_env_overrides"] = candidate_probe.get(
        "applied_env_overrides", {}
    )
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
    corpus_root: Path,
    cases_rel: Path,
    baseline_ref: str,
    candidate_ref: str,
    baseline_probe: dict[str, Any],
    candidate_probe: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    corpus = compute_corpus_fingerprint(corpus_root, cases_rel)
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
        f"| Baseline | `{baseline.get('ref', '')}` | `{baseline.get('commit', '')}` | `{baseline.get('retrieval_mode', '(unset)')}` | `{baseline_embed}` |"
    )
    lines.append(
        f"| Candidate | `{candidate.get('ref', '')}` | `{candidate.get('commit', '')}` | `{candidate.get('retrieval_mode', '(unset)')}` | `{candidate_embed}` |"
    )
    lines.append("")

    baseline_env = baseline.get("env_overrides", {})
    candidate_env = candidate.get("env_overrides", {})
    baseline_applied = baseline.get("applied_env_overrides", {})
    candidate_applied = candidate.get("applied_env_overrides", {})
    if baseline_env or candidate_env or baseline_applied or candidate_applied:
        lines.append("## Config Overrides")
        lines.append("")
        all_keys = sorted(
            set(baseline_env)
            | set(candidate_env)
            | set(baseline_applied)
            | set(candidate_applied)
        )
        lines.append(
            "Requested overrides (`--baseline-env` / `--candidate-env`) and the "
            "values the isolated probe actually applied:"
        )
        lines.append("")
        lines.append(
            "| Key | Baseline (requested) | Baseline (applied) | Candidate (requested) | Candidate (applied) |"
        )
        lines.append("| --- | --- | --- | --- | --- |")
        for key in all_keys:
            b_req = baseline_env.get(key, "(default)")
            b_app = baseline_applied.get(key, "(not applied)")
            c_req = candidate_env.get(key, "(default)")
            c_app = candidate_applied.get(key, "(not applied)")
            lines.append(
                f"| `{key}` | `{b_req}` | `{b_app}` | `{c_req}` | `{c_app}` |"
            )
        lines.append("")

    if baseline.get("quality") and candidate.get("quality"):
        lines.append("## Quality")
        lines.append("")
        lines.append("| Metric | Baseline | Candidate | Delta |")
        lines.append("| --- | ---: | ---: | ---: |")
        quality_deltas = decision.get("quality_deltas", {})
        report_keys = list(QUALITY_KEYS)
        if "false_positive_rate" in quality_deltas:
            report_keys.append("false_positive_rate")  # lower is better
        for metric in report_keys:
            b_val = float(baseline["quality"].get(metric, 0.0))
            c_val = float(candidate["quality"].get(metric, 0.0))
            delta = float(quality_deltas.get(metric, c_val - b_val))
            lines.append(
                f"| {metric} | {b_val:.6f} | {c_val:.6f} | {delta:+.6f} |"
            )
        lines.append("")

        # Per-language cohorts: reported, not gated. A Norwegian drop hidden by
        # an English gain shows here. Older refs may not report cohorts.
        b_lang = baseline["quality"].get("by_lang") or {}
        c_lang = candidate["quality"].get("by_lang") or {}
        if len(set(b_lang) | set(c_lang)) > 1:
            lines.append(f"| hit@k by lang | Baseline | Candidate | Delta |")
            lines.append("| --- | ---: | ---: | ---: |")
            for lang in sorted(set(b_lang) | set(c_lang)):
                b_hit = b_lang.get(lang, {}).get("hitk")
                c_hit = c_lang.get(lang, {}).get("hitk")
                if b_hit is None or c_hit is None:
                    lines.append(f"| {lang} | {b_hit if b_hit is not None else 'n/a'} | {c_hit if c_hit is not None else 'n/a'} | n/a |")
                else:
                    n = c_lang[lang].get("cases", 0)
                    lines.append(f"| {lang} ({n}) | {b_hit:.6f} | {c_hit:.6f} | {c_hit - b_hit:+.6f} |")
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
        "- If quality ties, mark `neutral`; a p95 over the latency threshold is named"
        " in the reason but is advisory, not blocking."
    )

    return "\n".join(lines) + "\n"


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(build_markdown_report(payload), encoding="utf-8")
