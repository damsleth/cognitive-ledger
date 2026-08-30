"""Persistent wiki layer for the autonomous A/B loop.

Adapts the wiki layer of *WikiSkill: Compiling Agent Experience into
Persistent Knowledge for Skill Evolution* (arXiv:2608.27454) to the ledger's
retrieval-config evolution loop. The three WikiSkill layers map onto the
existing ``ledger ab loop`` artifacts like this:

  raw layer    ->  ``ab_loop_results.jsonl``  (immutable trial traces)
  wiki layer   ->  ``<out_dir>/wiki/``        (this module: impact tracker,
                                               pattern verdicts, evolution log)
  skill layer  ->  ``champion.json``          (the active retrieval config)

Two properties carried over from the paper:

* **The wiki persists across rollbacks.** A rejected candidate still updates
  the impact tracker and evolution log; only the champion (skill layer)
  reverts. Knowledge compounds even when config changes are discarded.
* **The proposer is wiki-informed.** ``proposal_order`` ranks search-space
  params by their accumulated impact record, so the loop explores directions
  with a record of improvement first and stops re-exploring directions with a
  consistent record of regression.

The wiki is always *derived* from the raw trial log: rebuilding it from
history is safe and is exactly what ``Wiki.open`` does, so a stale or deleted
``patterns.json`` never loses knowledge that the raw layer still holds.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ledger.ab import EPSILON

WIKI_DIRNAME = "wiki"
PATTERNS_FILENAME = "patterns.json"
EVOLUTION_FILENAME = "evolution.md"

# A param is marked "avoid" after this many tried values with zero accepts and
# no screen-objective improvement at all. Mirrors WikiSkill's skill-impact
# tracker letting the proposer skip previously failed modification directions.
AVOID_MIN_TRIES = 3

# Verdicts, in proposal-priority order (lower rank proposed first).
VERDICT_PRIORITIZE = "prioritize"  # at least one accepted mutation
VERDICT_EXPLORE = "explore"        # untried, or mixed/positive evidence
VERDICT_STRUGGLING = "struggling"  # only failures so far, but too few to avoid
VERDICT_AVOID = "avoid"            # consistent failure record

_VERDICT_RANK = {
    VERDICT_PRIORITIZE: 0,
    VERDICT_EXPLORE: 1,
    VERDICT_STRUGGLING: 2,
    VERDICT_AVOID: 3,
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class ParamImpact:
    """Accumulated impact record for one search-space param."""

    tries: int = 0
    accepted: int = 0
    regressions: int = 0
    overfits: int = 0
    neutral: int = 0
    delta_sum: float = 0.0
    best_delta: float | None = None
    worst_delta: float | None = None
    values_tried: list[str] = field(default_factory=list)

    @property
    def mean_delta(self) -> float:
        return self.delta_sum / self.tries if self.tries else 0.0

    def record(self, decision: str, delta: float, value: str) -> None:
        self.tries += 1
        self.delta_sum += delta
        if self.best_delta is None or delta > self.best_delta:
            self.best_delta = delta
        if self.worst_delta is None or delta < self.worst_delta:
            self.worst_delta = delta
        if decision == "beneficial":
            self.accepted += 1
        elif decision == "regression":
            self.regressions += 1
        elif decision == "overfit":
            self.overfits += 1
        else:
            self.neutral += 1
        if value not in self.values_tried:
            self.values_tried.append(value)

    @property
    def verdict(self) -> str:
        if self.tries == 0:
            return VERDICT_EXPLORE
        if self.accepted > 0:
            return VERDICT_PRIORITIZE
        if self.best_delta is not None and self.best_delta > EPSILON:
            # Won a screen at least once (e.g. an overfit) -- keep exploring.
            return VERDICT_EXPLORE
        if self.tries >= AVOID_MIN_TRIES:
            return VERDICT_AVOID
        return VERDICT_STRUGGLING

    def as_dict(self) -> dict[str, Any]:
        return {
            "tries": self.tries,
            "accepted": self.accepted,
            "regressions": self.regressions,
            "overfits": self.overfits,
            "neutral": self.neutral,
            "delta_sum": self.delta_sum,
            "mean_delta": self.mean_delta,
            "best_delta": self.best_delta,
            "worst_delta": self.worst_delta,
            "values_tried": list(self.values_tried),
            "verdict": self.verdict,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParamImpact":
        return cls(
            tries=int(data.get("tries", 0)),
            accepted=int(data.get("accepted", 0)),
            regressions=int(data.get("regressions", 0)),
            overfits=int(data.get("overfits", 0)),
            neutral=int(data.get("neutral", 0)),
            delta_sum=float(data.get("delta_sum", 0.0)),
            best_delta=data.get("best_delta"),
            worst_delta=data.get("worst_delta"),
            values_tried=[str(v) for v in data.get("values_tried", [])],
        )


class Wiki:
    """Impact tracker + pattern verdicts + evolution log for one loop out_dir.

    ``patterns.json`` is the machine-readable pattern directory; the
    ``evolution.md`` log is the append-only human-readable trail. Both live in
    ``<out_dir>/wiki/`` and are never rolled back with the champion.
    """

    def __init__(self, wiki_dir: Path) -> None:
        self.wiki_dir = Path(wiki_dir)
        self.params: dict[str, ParamImpact] = {}
        self.trials_seen = 0

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    @classmethod
    def open(cls, wiki_dir: Path, history: list[dict[str, Any]]) -> "Wiki":
        """Open the wiki for a loop run, rebuilding stats from raw history.

        The trial log is the source of truth, so the impact tracker is always
        rebuilt from it (rejected trials included) rather than trusted from a
        possibly stale ``patterns.json``. The evolution log is append-only and
        untouched by rebuilds -- only live trials add entries.
        """
        wiki = cls(wiki_dir)
        for record in history:
            wiki.observe(record, log=False)
        return wiki

    # ------------------------------------------------------------------ #
    # Updates (the "wiki maintainer" step of each trial)
    # ------------------------------------------------------------------ #

    def observe(self, record: dict[str, Any], *, log: bool = True) -> None:
        """Fold one trial record into the impact tracker.

        Runs for accepted *and* rejected trials alike -- rejection rolls back
        the champion, never the wiki.
        """
        mutation = record.get("mutation") or {}
        decision = str(record.get("decision", "neutral"))
        delta = float(record.get("screen_delta", 0.0))
        self.trials_seen += 1
        for key, value in mutation.items():
            impact = self.params.setdefault(str(key), ParamImpact())
            before = impact.verdict
            impact.record(decision, delta, str(value))
            after = impact.verdict
            if log:
                self._append_evolution(record, str(key), str(value), before, after)

    # ------------------------------------------------------------------ #
    # Wiki-informed proposal ordering
    # ------------------------------------------------------------------ #

    def verdict(self, key: str) -> str:
        impact = self.params.get(key)
        return impact.verdict if impact else VERDICT_EXPLORE

    def proposal_order(self, keys: list[str]) -> list[str]:
        """Rank search-space params by accumulated evidence.

        prioritize (best mean delta first) > explore > struggling > avoid.
        Within a rank the original space order is kept, so with an empty wiki
        this degrades to plain coordinate descent. "avoid" params are ranked
        last rather than dropped: under a ``--max-trials`` budget they are
        effectively skipped, but the space stays exhaustively searchable.
        """
        def sort_key(item: tuple[int, str]) -> tuple[int, float, int]:
            index, key = item
            impact = self.params.get(key)
            rank = _VERDICT_RANK[self.verdict(key)]
            mean = impact.mean_delta if impact else 0.0
            return (rank, -mean, index)

        return [key for _, key in sorted(enumerate(keys), key=sort_key)]

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    @property
    def patterns_path(self) -> Path:
        return self.wiki_dir / PATTERNS_FILENAME

    @property
    def evolution_path(self) -> Path:
        return self.wiki_dir / EVOLUTION_FILENAME

    def save(self) -> Path:
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated": _now_iso(),
            "trials_seen": self.trials_seen,
            "params": {key: impact.as_dict() for key, impact in sorted(self.params.items())},
        }
        self.patterns_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return self.patterns_path

    @classmethod
    def load(cls, wiki_dir: Path) -> "Wiki":
        """Load a previously saved wiki (used for inspection, not the loop)."""
        wiki = cls(wiki_dir)
        path = wiki.patterns_path
        if not path.is_file():
            return wiki
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return wiki
        wiki.trials_seen = int(data.get("trials_seen", 0))
        for key, entry in (data.get("params") or {}).items():
            if isinstance(entry, dict):
                wiki.params[str(key)] = ParamImpact.from_dict(entry)
        return wiki

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #

    def summary_lines(self) -> list[str]:
        counts: dict[str, int] = {}
        for key in self.params:
            counts[self.verdict(key)] = counts.get(self.verdict(key), 0) + 1
        lines = [
            f"wiki               : {len(self.params)} param(s) tracked over "
            f"{self.trials_seen} trial(s)"
        ]
        for verdict in (VERDICT_PRIORITIZE, VERDICT_EXPLORE, VERDICT_STRUGGLING, VERDICT_AVOID):
            keys = sorted(k for k in self.params if self.verdict(k) == verdict)
            if keys:
                lines.append(f"  {verdict:<10}: {', '.join(keys)}")
        lines.append(f"wiki dir           : {self.wiki_dir}")
        return lines

    def _append_evolution(
        self,
        record: dict[str, Any],
        key: str,
        value: str,
        verdict_before: str,
        verdict_after: str,
    ) -> None:
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        is_new = not self.evolution_path.exists()
        with self.evolution_path.open("a", encoding="utf-8") as fh:
            if is_new:
                fh.write(
                    "# A/B loop evolution log\n\n"
                    "Append-only trail of every trial, accepted or rejected. "
                    "Persists across champion rollbacks (WikiSkill wiki layer).\n\n"
                )
            delta = float(record.get("screen_delta", 0.0))
            holdout = record.get("holdout_delta")
            line = (
                f"- trial {record.get('trial')} ({record.get('timestamp', '')}): "
                f"`{key}={value}` -> {record.get('decision')} (screen Δ{delta:+.4f}"
            )
            if holdout is not None:
                line += f", holdout Δ{float(holdout):+.4f}"
            line += ")\n"
            fh.write(line)
            if verdict_after != verdict_before:
                fh.write(
                    f"  - pattern: `{key}` is now **{verdict_after}** "
                    f"(was {verdict_before})\n"
                )
