#!/usr/bin/env python3
"""Embedding duplicate-rate of a ledger fork — the T7 quality metric.

See yaams .plans/02-promote-admission-eval-t7.md. Reports the fraction of notes
that have at least one near-duplicate (cosine similarity > threshold, default
0.95). T7 ships admission gating only if this DROPS — fewer near-dup notes
promoted — without an MRR regression (measured separately by `ledger ab`).

Distinct from ledger/duplicates.py, which is lexical (Jaccard / title overlap);
this is embedding-based, catching semantic near-duplicates that share no words.

Run:        python scripts/embed_dup_rate.py --root ~/brain/ledger [--threshold 0.95] [--json]
Self-check: python scripts/embed_dup_rate.py --self-check     (no model/notes needed)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def dup_rate_from_vectors(vectors, threshold: float = 0.95) -> dict:
    """Pure: given note embeddings, count near-duplicate pairs and notes.

    ``dup_rate`` is the fraction of notes with >= 1 near-duplicate neighbor —
    the headline T7 number. Also returns the raw pair count.
    """
    n = len(vectors)
    if n < 2:
        return {"notes": n, "dup_pairs": 0, "notes_with_dup": 0, "dup_rate": 0.0,
                "threshold": threshold}
    m = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    m = m / norms
    sim = m @ m.T
    dup_pairs = int((sim[np.triu_indices(n, k=1)] > threshold).sum())
    np.fill_diagonal(sim, 0.0)
    notes_with_dup = int((sim.max(axis=1) > threshold).sum())
    return {"notes": n, "dup_pairs": dup_pairs, "notes_with_dup": notes_with_dup,
            "dup_rate": notes_with_dup / n, "threshold": threshold}


def _note_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if raw.startswith("---"):  # strip a leading YAML frontmatter block
        end = raw.find("\n---", 3)
        if end != -1:
            raw = raw[end + 4:]
    return raw.strip()


def collect_texts(root: str) -> tuple[list[Path], list[str]]:
    base = Path(root).expanduser()
    notes_dir = base / "notes" if (base / "notes").is_dir() else base
    paths = sorted(p for p in notes_dir.rglob("*.md") if "08_indices" not in p.parts)
    return paths, [_note_text(p) for p in paths]


def _self_check() -> int:
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]   # identical to a -> a near-dup pair
    c = [0.0, 1.0, 0.0]   # orthogonal to both
    r = dup_rate_from_vectors([a, b, c], threshold=0.95)
    assert r["dup_pairs"] == 1, r
    assert r["notes_with_dup"] == 2, r           # a and b each have a near-dup
    assert abs(r["dup_rate"] - 2 / 3) < 1e-9, r
    assert dup_rate_from_vectors([c, [0.0, 0.0, 1.0]], 0.95)["dup_pairs"] == 0
    assert dup_rate_from_vectors([a], 0.95)["dup_rate"] == 0.0
    print("embed_dup_rate self-check: OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="~/brain/ledger", help="Ledger fork root.")
    ap.add_argument("--threshold", type=float, default=0.95)
    ap.add_argument("--model", default=None,
                    help="Override embed model (default: local backend default).")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        return _self_check()

    from ledger.embeddings import default_model_for_backend, embed_texts
    paths, texts = collect_texts(args.root)
    if len(texts) < 2:
        print(f"need >= 2 notes under {args.root}; found {len(texts)}", file=sys.stderr)
        return 1
    model = args.model or default_model_for_backend("local")
    vecs = embed_texts(texts, backend="local", model=model)
    res = dup_rate_from_vectors(vecs, args.threshold)
    res["root"] = str(Path(args.root).expanduser())
    res["model"] = model
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"notes={res['notes']} dup_pairs={res['dup_pairs']} "
              f"notes_with_dup={res['notes_with_dup']} dup_rate={res['dup_rate']:.4f} "
              f"(cosine > {res['threshold']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
