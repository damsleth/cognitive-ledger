from __future__ import annotations
from ledger.io.hashing import file_digest
from ledger.layout import CONTENT_NOTE_LAYOUTS
from ledger.layout import INDICES_SUBDIR
from ledger.layout import NOTE_LAYOUTS
from ledger.layout import logical_path
from ledger.layout import resolve_path
from pathlib import Path
from typing import Any
import hashlib


CORPUS_PATTERNS = (
    *(f"{layout.subdir}/**/*.md" for layout in CONTENT_NOTE_LAYOUTS),
    f"{INDICES_SUBDIR}/aliases.json",
)


CORPUS_REQUIRED_DIRS = (NOTE_LAYOUTS["facts"].subdir, NOTE_LAYOUTS["loops"].subdir, INDICES_SUBDIR)


class InvalidSetupError(Exception):
    """Raised when the A/B harness cannot produce a valid comparison."""


def normalize_cases_path(base_root: Path, cases_arg: str) -> Path:
    candidate = Path(cases_arg)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(base_root.resolve())
        except ValueError as exc:
            raise InvalidSetupError(
                "--cases absolute path must point inside the configured corpus"
            ) from exc
    return candidate


def normalize_corpus_root(root: Path | str) -> Path:
    candidate = Path(root).expanduser().resolve()
    notes_child = candidate / "notes"
    if notes_child.is_dir() and not (candidate / INDICES_SUBDIR).is_dir():
        return notes_child.resolve()
    return candidate


def is_corpus_root(root: Path | str) -> bool:
    corpus_root = normalize_corpus_root(root)
    return all((corpus_root / dirname).is_dir() for dirname in CORPUS_REQUIRED_DIRS)


def sha256_file(path: Path) -> str:
    return file_digest(path)


def collect_corpus_paths(worktree_root: Path, cases_rel_path: Path) -> list[Path]:
    corpus_root = normalize_corpus_root(worktree_root)
    files: set[Path] = set()
    for pattern in CORPUS_PATTERNS:
        for path in sorted(corpus_root.glob(pattern)):
            if path.is_file():
                files.add(path.resolve())

    cases_path = resolve_path(
        cases_rel_path,
        ledger_root=corpus_root,
        ledger_notes_dir=corpus_root,
    )
    if not cases_path.is_file():
        raise InvalidSetupError(
            f"Cases file not found in corpus: {cases_rel_path.as_posix()}"
        )
    files.add(cases_path)

    return sorted(files, key=lambda item: item.as_posix())


def build_corpus_file_map(worktree_root: Path, cases_rel_path: Path) -> dict[str, str]:
    corpus_root = normalize_corpus_root(worktree_root)
    file_map: dict[str, str] = {}
    for path in collect_corpus_paths(worktree_root, cases_rel_path):
        rel = logical_path(path, ledger_notes_dir=corpus_root).as_posix()
        file_map[rel] = sha256_file(path)
    return file_map


def compute_fingerprint(file_map: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for rel_path in sorted(file_map):
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_map[rel_path].encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def compute_corpus_fingerprint(
    worktree_root: Path, cases_rel_path: Path
) -> dict[str, Any]:
    file_map = build_corpus_file_map(worktree_root, cases_rel_path)
    return {
        "fingerprint": compute_fingerprint(file_map),
        "file_count": len(file_map),
        "file_map": file_map,
    }


def diff_file_maps(
    baseline_map: dict[str, str], candidate_map: dict[str, str]
) -> dict[str, Any]:
    baseline_keys = set(baseline_map.keys())
    candidate_keys = set(candidate_map.keys())

    added = sorted(candidate_keys - baseline_keys)
    removed = sorted(baseline_keys - candidate_keys)
    changed = sorted(
        key
        for key in (baseline_keys & candidate_keys)
        if baseline_map[key] != candidate_map[key]
    )

    changed_details = [
        {
            "path": key,
            "baseline_hash": baseline_map[key],
            "candidate_hash": candidate_map[key],
        }
        for key in changed
    ]

    return {
        "added": added,
        "removed": removed,
        "changed": changed_details,
        "is_equal": not (added or removed or changed),
    }
