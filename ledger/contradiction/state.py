from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from ledger.io import safe_write_text
from ledger.io.hashing import digest
from pathlib import Path
from typing import Any
import json


STATE_FILE_VERSION = 1


STATE_FILENAME = "contradiction_state.json"


def _content_hash(text: str) -> str:
    return digest(text.encode("utf-8"))[:16]


@dataclass
class ContradictionState:
    """In-memory view of the scan state file.

    scanned_hashes: {rel_path -> content_hash}
        Tracks which version of each note has been scanned. When a note's
        hash changes, it is re-scanned as a candidate.
    resolved_pairs: {canonical_pair_key -> action}
        Tracks already-resolved (note_a, note_b) pairs to prevent re-processing.
        canonical_pair_key = "notes/a.md|notes/b.md" (alphabetical order).
    """
    scanned_hashes: dict[str, str] = field(default_factory=dict)
    resolved_pairs: dict[str, str] = field(default_factory=dict)


def _pair_key(ref_a: str, ref_b: str) -> str:
    """Canonical sorted pair key for idempotency tracking."""
    a, b = sorted([ref_a, ref_b])
    return f"{a}|{b}"


def load_state(indices_dir: Path) -> ContradictionState:
    """Load the contradiction state file; return empty state on any error."""
    state_path = indices_dir / STATE_FILENAME
    if not state_path.is_file():
        return ContradictionState()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if data.get("version") != STATE_FILE_VERSION:
            return ContradictionState()
        return ContradictionState(
            scanned_hashes=data.get("scanned_hashes", {}),
            resolved_pairs=data.get("resolved_pairs", {}),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return ContradictionState()


def save_state(indices_dir: Path, state: ContradictionState) -> None:
    """Persist the contradiction state file atomically."""
    indices_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "version": STATE_FILE_VERSION,
        "scanned_hashes": state.scanned_hashes,
        "resolved_pairs": state.resolved_pairs,
    }
    state_path = indices_dir / STATE_FILENAME
    safe_write_text(state_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
