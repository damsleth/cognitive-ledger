"""Autonomous A/B optimization loop for retrieval config.

Plan: ``.plans/autoresearch-ab-loop.md`` (now archived). Ports autoresearch's
propose -> test -> measure -> accept loop onto the existing ``ledger ab`` probe
infrastructure.

A *candidate* is just a dict of config ``env_overrides`` layered on the current
champion's overrides -- no git branches. Each trial:

  1. propose one bounded single-key mutation (coordinate-descent over space.yaml),
  2. screen-probe champion + candidate on the screen shard, compare objectives,
  3. only if the candidate wins the screen, holdout-probe both and require the
     holdout objective not to regress beyond ``--holdout-tol`` (overfit guard),
  4. accept (advance champion, persist champion.json) or discard, always logging
     one JSONL line to ``ab_loop_results.jsonl``.

Champion advancement requires winning *both* the screen and the holdout. The
champion is in-memory + persisted to ``champion.json``; ``--resume`` reloads it.

A persistent wiki (``ledger/ab_wiki.py``, WikiSkill-style; see
``docs/wikiskill.md``) sits between the raw trial log and the proposer: every
trial -- rejected ones included -- updates a per-param impact tracker under
``<out_dir>/wiki/``, and the proposer consults it to rank which param to
mutate next. Disable with ``--no-wiki`` for blind coordinate descent.

ponytail: probes run against the live corpus in ``repo_root`` (the same direct
path ``ledger ab run`` takes when both refs are HEAD) -- no per-trial git
worktree. Switch to worktree isolation only if a trial ever needs to mutate
tracked files.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from ledger import ab
from ledger.ab_wiki import WIKI_DIRNAME, Wiki
from ledger.config import get_config
from ledger.retrieval import resolve_retrieval_mode


# --------------------------------------------------------------------------- #
# Search space + proposal stream (Step 6 v1: bounded coordinate descent)
# --------------------------------------------------------------------------- #

def load_space(path: str | Path) -> dict[str, list[str]]:
    """Load a search space YAML into ``{param_key: [value, ...]}``.

    Each param is either an explicit list of values or a ``{min, max, step}``
    range (inclusive). Values are normalized to strings -- the probe coerces
    them back to the config field's type.
    """
    raw = yaml.safe_load(Path(path).expanduser().read_text()) or {}
    params = raw.get("params", raw)
    if not isinstance(params, dict) or not params:
        raise ValueError(f"search space {path} has no params")
    space: dict[str, list[str]] = {}
    for key, spec in params.items():
        space[str(key)] = _expand_values(spec)
    return space


def _expand_values(spec: Any) -> list[str]:
    if isinstance(spec, (list, tuple)):
        return [_fmt(v) for v in spec]
    if isinstance(spec, dict):
        lo, hi = float(spec["min"]), float(spec["max"])
        step = float(spec.get("step", (hi - lo) / 4 if hi > lo else 1.0))
        if step <= 0:
            raise ValueError(f"step must be > 0 (got {step})")
        values: list[str] = []
        v = lo
        # +1e-9 so the inclusive upper bound survives float drift.
        while v <= hi + 1e-9:
            values.append(_fmt(v))
            v += step
        return values
    return [_fmt(spec)]


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return f"{v:.6g}"
    return str(v)


def next_proposal(
    space: dict[str, list[str]],
    champion_env: dict[str, str],
    history: list[dict[str, Any]],
    wiki: Wiki | None = None,
) -> dict[str, str] | None:
    """Return one untried single-key mutation, or ``None`` if space is exhausted.

    Coordinate descent: sweep params, propose the first value that differs from
    the champion's current value for that key and hasn't been tried before. When
    the champion advances, its values update, so previously-skipped values become
    eligible again -- the classic re-centering behaviour.

    With a ``wiki``, the param sweep order is wiki-informed: params with an
    accepted mutation on record come first, params with a consistent failure
    record come last (see ``Wiki.proposal_order``). An empty wiki degrades to
    plain space order, so behaviour without history is unchanged.
    """
    tried: set[tuple[str, str]] = set()
    for entry in history:
        mutation = entry.get("mutation") or {}
        for k, val in mutation.items():
            tried.add((str(k), str(val)))
    keys = wiki.proposal_order(list(space)) if wiki is not None else list(space)
    for key in keys:
        values = space[key]
        current = str(champion_env.get(key, ""))
        for val in values:
            if val == current:
                continue
            if (key, val) in tried:
                continue
            return {key: val}
    return None


# --------------------------------------------------------------------------- #
# Probing + objective
# --------------------------------------------------------------------------- #

def _probe(
    repo_root: Path,
    corpus_dir: Path,
    cases_rel: Path,
    env: dict[str, str],
    *,
    retrieval_mode: str,
    embed_backend: str,
    embed_model: str | None,
    k: int,
    eval_runs: int,
    query_runs: int,
    cold_query: bool,
    label: str,
) -> dict[str, Any]:
    return ab.run_probe_for_side(
        repo_root,
        repo_root,
        cases_rel=cases_rel,
        k=k,
        eval_runs=eval_runs,
        query_runs=query_runs,
        retrieval_mode=retrieval_mode,
        cold_query=cold_query,
        embed_backend=embed_backend,
        embed_model=embed_model,
        side_label=label,
        corpus_dir=corpus_dir,
        env_overrides=env or None,
    )


def _query_p95(probe: dict[str, Any]) -> float:
    return float(probe["latency"]["query"]["summary"].get("p95_ms", 0.0))


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

class _Stop:
    """SIGINT flag: finish the current trial, then exit cleanly."""

    def __init__(self) -> None:
        self.requested = False

    def __call__(self, *_: Any) -> None:
        self.requested = True
        print("\n[ab loop] SIGINT received -- finishing current trial...", file=sys.stderr)


def run_loop(args: argparse.Namespace) -> int:
    cfg = get_config()
    repo_root = ab._resolve_repo_root()
    corpus_dir = (
        ab.normalize_corpus_root(Path(args.corpus).expanduser().resolve())
        if args.corpus
        else ab.normalize_corpus_root(cfg.ledger_notes_dir)
    )
    retrieval_mode = resolve_retrieval_mode(args.mode)
    cases = {
        name: ab.normalize_cases_path(corpus_dir, path)
        for name, path in (
            ("screen", args.screen_cases),
            ("holdout", args.holdout_cases),
            ("benchmark", args.benchmark_cases),
        )
    }

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    champ_path = out_dir / "champion.json"
    log_path = out_dir / "ab_loop_results.jsonl"

    space = load_space(args.space)

    def probe(env: dict[str, str], cases_rel: Path, label: str) -> dict[str, Any]:
        return _probe(
            repo_root, corpus_dir, cases_rel, env,
            retrieval_mode=retrieval_mode,
            embed_backend=args.embed_backend, embed_model=args.embed_model,
            k=args.k, eval_runs=args.eval_runs, query_runs=args.query_runs,
            cold_query=args.cold_query, label=label,
        )

    # --- resume or cold start ------------------------------------------------
    history: list[dict[str, Any]] = []
    if args.resume and log_path.exists():
        history = [
            json.loads(line)
            for line in log_path.read_text().splitlines()
            if line.strip()
        ]
    champion: dict[str, Any]
    if args.resume and champ_path.exists():
        champion = json.loads(champ_path.read_text())
        print(f"[ab loop] resumed champion obj={champion['screen_objective']:.4f} "
              f"env={champion['env']} ({len(history)} prior trials)")
    else:
        base = probe({}, cases["screen"], "champion")
        champion = {
            "env": {},
            "screen_objective": ab.compute_objective(base["quality"]),
            "screen_quality": base["quality"],
            "trial": -1,
        }
        champ_path.write_text(json.dumps(champion, indent=2))
        print(f"[ab loop] baseline objective={champion['screen_objective']:.4f}")

    # --- wiki (persistent knowledge layer; rebuilt from the raw trial log) ---
    wiki: Wiki | None = None
    if not getattr(args, "no_wiki", False):
        wiki = Wiki.open(out_dir / WIKI_DIRNAME, history)
        wiki.save()
        if wiki.params:
            print(f"[ab loop] wiki: {wiki.trials_seen} prior trial(s) folded into "
                  f"{len(wiki.params)} param record(s)")

    stop = _Stop()
    signal.signal(signal.SIGINT, stop)

    trial = (history[-1]["trial"] + 1) if history else 0
    accepted_count = sum(1 for h in history if h.get("accepted"))

    # --- main loop -----------------------------------------------------------
    while trial < args.max_trials and not stop.requested:
        mutation = next_proposal(space, champion["env"], history, wiki)
        if mutation is None:
            print("[ab loop] search space exhausted")
            break

        cand_env = {**champion["env"], **mutation}
        cand = probe(cand_env, cases["screen"], "candidate")
        cand_obj = ab.compute_objective(cand["quality"])
        screen_delta = cand_obj - champion["screen_objective"]

        record: dict[str, Any] = {
            "trial": trial,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mutation": mutation,
            "candidate_env": cand_env,
            "screen_objective": cand_obj,
            "screen_quality": cand["quality"],
            "screen_delta": screen_delta,
            "latency_p95_ms": _query_p95(cand),
            "holdout_objective": None,
            "holdout_delta": None,
            "decision": "neutral",
            "accepted": False,
        }

        if screen_delta > ab.EPSILON:
            # Candidate beat the champion on screen -> re-probe both on holdout
            # to reject overfit winners.
            champ_hold = probe(champion["env"], cases["holdout"], "champion-holdout")
            cand_hold = probe(cand_env, cases["holdout"], "candidate-holdout")
            champ_hold_obj = ab.compute_objective(champ_hold["quality"])
            cand_hold_obj = ab.compute_objective(cand_hold["quality"])
            holdout_delta = cand_hold_obj - champ_hold_obj
            record["holdout_objective"] = cand_hold_obj
            record["holdout_delta"] = holdout_delta
            if holdout_delta >= -args.holdout_tol:
                record["decision"] = "beneficial"
                record["accepted"] = True
                champion = {
                    "env": cand_env,
                    "screen_objective": cand_obj,
                    "screen_quality": cand["quality"],
                    "trial": trial,
                }
                champ_path.write_text(json.dumps(champion, indent=2))
                accepted_count += 1
            else:
                record["decision"] = "overfit"
        elif screen_delta < -ab.EPSILON:
            record["decision"] = "regression"

        history.append(record)
        with log_path.open("a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        if wiki is not None:
            # The wiki is never rolled back: rejected candidates update it too.
            wiki.observe(record)
            wiki.save()
        print(
            f"[ab loop] trial {trial}: {mutation} screen_obj={cand_obj:.4f} "
            f"(Δ{screen_delta:+.4f}) {record['decision']}"
            + (f" holdoutΔ{record['holdout_delta']:+.4f}" if record["holdout_delta"] is not None else "")
        )
        trial += 1

    # --- final: measure champion on the benchmark shard ----------------------
    final = probe(champion["env"], cases["benchmark"], "champion-benchmark")
    final_obj = ab.compute_objective(final["quality"])
    print("\n=== ab loop summary ===")
    print(f"trials run        : {trial}")
    print(f"candidates accepted: {accepted_count}")
    print(f"champion env       : {champion['env'] or '(baseline / no overrides)'}")
    print(f"screen objective   : {champion['screen_objective']:.4f}")
    print(f"benchmark objective: {final_obj:.4f}  quality={final['quality']}")
    print(f"champion saved to  : {champ_path}")
    print(f"results log        : {log_path}")
    if wiki is not None:
        for line in wiki.summary_lines():
            print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    idx = "08_indices"
    parser = argparse.ArgumentParser(
        prog="ledger ab loop",
        description="Autonomous propose/screen/holdout A/B optimization loop",
    )
    parser.add_argument("--space", required=True, help="YAML search space file")
    parser.add_argument("--max-trials", type=int, default=50, help="Hard cap on trials")
    parser.add_argument("--holdout-tol", type=float, default=0.0,
                        help="Max allowed holdout objective regression for a winner")
    parser.add_argument("--screen-cases", default=f"{idx}/retrieval_eval_screen.yaml")
    parser.add_argument("--holdout-cases", default=f"{idx}/retrieval_eval_holdout.yaml")
    parser.add_argument("--benchmark-cases", default=f"{idx}/retrieval_eval_cases.yaml")
    parser.add_argument("--mode", default=None, help="Retrieval mode (default: configured)")
    parser.add_argument("--embed-backend", default="local")
    parser.add_argument("--embed-model", default=None)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--eval-runs", type=int, default=3)
    parser.add_argument("--query-runs", type=int, default=3)
    parser.add_argument("--cold-query", action="store_true")
    parser.add_argument("--corpus", default=None, help="Ledger corpus root override")
    parser.add_argument("--out-dir", default="ab_loop_out",
                        help="Where champion.json + ab_loop_results.jsonl live")
    parser.add_argument("--resume", action="store_true",
                        help="Continue from existing champion.json + results log")
    parser.add_argument("--no-wiki", action="store_true",
                        help="Disable the persistent wiki (blind coordinate descent)")
    return parser


def main_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_loop(args)
    except ab.InvalidSetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main_cli())
