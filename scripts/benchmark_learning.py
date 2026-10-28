#!/usr/bin/env python3
"""Benchmark learning workflows against a corpus without modifying it.

Raw reports may contain personal excerpts: output must be outside the corpus.
Run with the project's interpreter. Optional model commands are explicit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ledger.layout import INDICES_SUBDIR
from ledger.behaviour import run_suite
from ledger.improvements import queue, sync, transition
from ledger.instruction_audit import build_bundle, audit_prompt, validate_findings
from ledger.synthesize.llm import SubprocessAdapter


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def timed(fn, repeats):
    samples = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - start) * 1000)
    ordered = sorted(samples)
    return {"runs": repeats, "median_ms": statistics.median(samples),
            "p95_ms": ordered[min(len(ordered)-1, int(.95*len(ordered)))]}, result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--notes-dir", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--samples", type=int, default=30)
    p.add_argument("--max-chars", type=int, default=120000)
    p.add_argument("--cases", type=Path)
    p.add_argument("--instructions", type=Path)
    p.add_argument("--command")
    p.add_argument("--model")
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--review-command")
    args = p.parse_args()
    root, output = args.notes_dir.resolve(), args.output.resolve()
    if not root.is_dir() or output.is_relative_to(root) or args.samples < 1:
        p.error("Require an existing corpus, output outside it, and positive samples")
    if output.exists():
        p.error("Output directory must be new")
    output.mkdir(parents=True, mode=0o700)
    before = snapshot(root)

    def save(name, value):
        path = output / name
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        path.chmod(0o600)

    report = {"corpus_files": len(before)}
    report["queue_latency"], items = timed(lambda: queue(root), args.samples)
    report["open_improvements"] = sum(i["state"] == "open" for i in items)
    report["improvement_kinds"] = {k: sum(i["kind"] == k for i in items)
                                   for k in sorted({i["kind"] for i in items})}
    save("queue.json", items)
    # Mutations exercise only a scratch copy of signal/lifecycle data.
    with tempfile.TemporaryDirectory(prefix="ledger-lifecycle-benchmark-") as temp:
        scratch = Path(temp)
        (scratch / INDICES_SUBDIR).mkdir()
        for name in ("signals.jsonl", "improvements.jsonl"):
            source = root / INDICES_SUBDIR / name
            if source.exists():
                shutil.copy2(source, scratch / INDICES_SUBDIR / name)
        observed = sync(scratch)
        stable = sync(scratch)
        first = next((i for i in queue(scratch) if i["state"] == "open"), None)
        resolved = None
        if first:
            transition(scratch, first["id"], "resolve", reason="scratch benchmark")
            resolved = next(i["state"] for i in queue(scratch) if i["id"] == first["id"])
        report["lifecycle"] = {"observed": observed, "repeat_sync_changes": stable,
                                "resolution_state": resolved}
    report["audit_latency"], bundle = timed(lambda: build_bundle(args.manifest, root, max_chars=args.max_chars), args.samples)
    report["audit_bundle"] = {"documents": len(bundle["documents"]),
                              "truncated": sum(d["truncated"] for d in bundle["documents"]),
                              "omitted": len(bundle["omitted"]),
                              "prompt_chars": len(audit_prompt(bundle))}
    save("bundle.json", bundle)
    save("manifest.json", {"path": str(args.manifest.resolve()), "text": args.manifest.read_text()})
    if args.review_command:
        started = time.perf_counter()
        response = SubprocessAdapter(shlex.split(args.review_command), timeout=120).complete(audit_prompt(bundle))
        review = json.loads(response.text)
        save("review.json", review)
        validated = validate_findings(bundle, review)
        save("validated-findings.json", validated)
        report["audit_review"] = {"findings": len(validated),
                                   "elapsed_ms": (time.perf_counter()-started)*1000}
    if args.cases:
        behaviour = run_suite(args.cases, root, command=shlex.split(args.command) if args.command else None,
                              model=args.model, instructions=args.instructions.read_text() if args.instructions else "",
                              runs=args.runs)
        save("behaviour.json", behaviour)
        report["behaviour"] = {"totals": behaviour["totals"],
                               "agent_pass_rate_delta": behaviour["agent_pass_rate_delta"]}
    after = snapshot(root)
    report["corpus_unchanged"] = before == after
    report["changed_corpus_paths"] = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    save("summary.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["corpus_unchanged"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
