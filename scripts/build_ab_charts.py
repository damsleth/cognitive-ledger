#!/usr/bin/env python3
"""Render A/B performance charts from docs/ab/performance_series.json.

Each run is plotted as a pair of bars (baseline vs candidate) so the absolute
metric value reached per run is visible, not just the delta.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SERIES_PATH = ROOT / "docs" / "ab" / "performance_series.json"
CHARTS_DIR = ROOT / "docs" / "ab" / "charts"

METRICS = [
    {
        "filename": "mrr_over_time.png",
        "baseline_key": "baseline_mrr",
        "candidate_key": "candidate_mrr",
        "title": "MRR per A/B run (baseline vs candidate)",
        "ylabel": "MRR",
        "ylim": (0.0, 1.05),
    },
    {
        "filename": "hitk_over_time.png",
        "baseline_key": "baseline_hitk",
        "candidate_key": "candidate_hitk",
        "title": "hit@k per A/B run (baseline vs candidate)",
        "ylabel": "hit@k",
        "ylim": (0.0, 1.05),
    },
    {
        "filename": "p95_query_over_time.png",
        "baseline_key": "baseline_query_p95_ms",
        "candidate_key": "candidate_query_p95_ms",
        "title": "p95 query latency per A/B run (baseline vs candidate)",
        "ylabel": "p95 query latency (ms)",
        "ylim": None,
    },
]


def render(series: list[dict], metric: dict) -> Path:
    labels = [r["label"] for r in series]
    baseline = [r[metric["baseline_key"]] for r in series]
    candidate = [r[metric["candidate_key"]] for r in series]
    n = len(series)
    x = np.arange(n)
    width = 0.4

    fig, ax = plt.subplots(figsize=(max(10, n * 0.55), 5))
    ax.bar(x - width / 2, baseline, width, label="baseline", color="#9aa0a6")
    ax.bar(x + width / 2, candidate, width, label="candidate", color="#1a73e8")
    ax.set_title(metric["title"])
    ax.set_ylabel(metric["ylabel"])
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    if metric["ylim"]:
        ax.set_ylim(*metric["ylim"])
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend(loc="best")
    fig.tight_layout()

    out = CHARTS_DIR / metric["filename"]
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def main() -> None:
    series = json.loads(SERIES_PATH.read_text())
    series.sort(key=lambda r: r["generated_at"])
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    for metric in METRICS:
        out = render(series, metric)
        print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
