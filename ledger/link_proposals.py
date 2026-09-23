"""Propose [[links]] between related notes during sleep (plan 11, A-MEM links only).

Links only: nothing here rewrites what a note says. Re-summarising notes when a
related one arrives is the drift-prone half of A-MEM (plan 15) and is not done.

Similarity comes from the vectors already in the semantic index, so no model is
loaded. Proposals are printed by default; `--apply` appends `- [[stem]]` under
the note's `## Links` section, in both directions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ledger.config import get_config
from ledger.embeddings import ARCHIVE_REL_PREFIX, configured_model_for_backend, load_semantic_index
from ledger.layout import resolve_path
from ledger.parsing.links import extract_links

# Calibrated 2026-09-23 on the live bge-m3 index (554 notes): pairs at 0.78-0.82
# are genuinely related (same person in two roles, two SSH-key loops); 0.80
# proposes >=1 link for 101 notes. Above the ceiling a pair is a near-duplicate:
# a merge candidate for `sleep duplicates`, not a link.
FLOOR = 0.80
DUPLICATE_CEILING = 0.97
CAP = 5

_UPDATED_RE = re.compile(r"^updated:.*$", re.MULTILINE)


@dataclass(frozen=True)
class LinkProposal:
    source: str  # logical notes/... path that gains the link
    target: str  # logical notes/... path it links to
    score: float


def _stem(rel_path: str) -> str:
    return Path(rel_path).stem


def _existing_targets(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {Path(link.target).stem for link in extract_links(text)}


def propose_links(
    *,
    only: set[str] | None = None,
    floor: float = FLOOR,
    cap: int = CAP,
) -> list[LinkProposal]:
    """Top-`cap` neighbours in [floor, ceiling) per note, minus links that exist.

    *only* restricts the notes proposals are generated for (e.g. those changed
    since the last sleep); their backlinks are still proposed.
    """
    cfg = get_config()
    backend = str(cfg.embed_backend or "local")
    index, vectors = load_semantic_index("ledger", backend, configured_model_for_backend(backend))
    if index is None or vectors is None:
        return []
    items = index.get("items", [])
    rows = [i for i, it in enumerate(items) if not str(it.get("rel_path", "")).startswith(ARCHIVE_REL_PREFIX)]
    if len(rows) < 2:
        return []
    rels = [str(items[i]["rel_path"]) for i in rows]
    matrix = np.asarray(vectors[rows], dtype=np.float32)
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)

    def abs_path(rel: str) -> Path:
        return resolve_path(rel, ledger_root=cfg.ledger_root, ledger_notes_dir=cfg.ledger_notes_dir)

    linked: dict[str, set[str]] = {}

    def has_link(src: str, dst: str) -> bool:
        if src not in linked:
            linked[src] = _existing_targets(abs_path(src))
        return _stem(dst) in linked[src]

    proposals: dict[tuple[str, str], LinkProposal] = {}
    for a, src in enumerate(rels):
        if only is not None and src not in only:
            continue
        sims = matrix @ matrix[a]
        sims[a] = -1.0
        taken = 0
        for b in np.argsort(-sims):
            score = float(sims[b])
            if score < floor or taken >= cap:
                break
            if score >= DUPLICATE_CEILING:
                continue
            dst = rels[b]
            taken += 1
            for s, d in ((src, dst), (dst, src)):
                if (s, d) not in proposals and not has_link(s, d):
                    proposals[(s, d)] = LinkProposal(s, d, round(score, 3))
    return sorted(proposals.values(), key=lambda p: (-p.score, p.source, p.target))


def apply_link(proposal: LinkProposal, now_iso: str) -> bool:
    """Append `- [[target]]` under `## Links`; bump `updated`. False if skipped."""
    cfg = get_config()
    path = resolve_path(proposal.source, ledger_root=cfg.ledger_root, ledger_notes_dir=cfg.ledger_notes_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if _stem(proposal.target) in {Path(link.target).stem for link in extract_links(text)}:
        return False
    line = f"- [[{_stem(proposal.target)}]]"
    match = re.search(r"^## Links[ \t]*\n", text, re.MULTILINE)
    if match:
        # Insert after the last list item of the section, before the next heading.
        section_end = re.search(r"^## ", text[match.end():], re.MULTILINE)
        end = match.end() + section_end.start() if section_end else len(text)
        body = text[match.end():end].rstrip("\n")
        head = body + "\n" if body.strip() else "\n"  # keep the blank line after the heading
        text = text[:match.end()] + head + line + "\n" + ("\n" if section_end else "") + text[end:]
    else:
        text = text.rstrip("\n") + f"\n\n## Links\n\n{line}\n"
    text = _UPDATED_RE.sub(f"updated: {now_iso}", text, count=1)
    from ledger.io import safe_write_text

    safe_write_text(path, text)
    return True
