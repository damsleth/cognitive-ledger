"""Source ingest pipeline for Cognitive Ledger.

Provides scaffolding for feeding raw sources into the ledger:
scanning sources, diffing against a manifest, preparing ingest
context for the LLM, and recording provenance.

The LLM does the distillation - this module handles logistics.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.io.safe_write import safe_write_text, append_timeline_entry


MANIFEST_FILENAME = "source_manifest.json"


def _manifest_path(notes_dir: Path | None = None) -> Path:
    config = get_config()
    nd = notes_dir or config.notes_dir
    return nd / "08_indices" / MANIFEST_FILENAME


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_manifest(notes_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load the source manifest.

    Returns:
        List of manifest entries.
    """
    mp = _manifest_path(notes_dir)
    if not mp.is_file():
        return []
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_manifest(entries: list[dict[str, Any]], notes_dir: Path | None = None) -> Path:
    """Save the source manifest.

    Returns:
        Path to the manifest file.
    """
    mp = _manifest_path(notes_dir)
    mp.parent.mkdir(parents=True, exist_ok=True)
    safe_write_text(mp, json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    return mp


def scan_sources(source_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Scan source files and return metadata.

    Args:
        source_root: Root directory of source notes. Defaults to config.source_root.

    Returns:
        List of dicts with path, sha256, modified, size.
    """
    config = get_config()
    root = Path(source_root) if source_root else config.source_root
    root = root.expanduser().resolve()

    if not root.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.md")):
        # Skip hidden dirs and common non-content dirs
        parts = path.relative_to(root).parts
        if any(p.startswith(".") for p in parts):
            continue

        stat = path.stat()
        results.append({
            "path": str(path.relative_to(root)),
            "sha256": _sha256(path),
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": stat.st_size,
        })

    return results


def diff_manifest(
    manifest: list[dict[str, Any]],
    scan: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Diff current scan against the manifest.

    Returns:
        Dict with 'new', 'modified', 'deleted' lists.
    """
    manifest_by_path = {e["path"]: e for e in manifest}
    scan_by_path = {e["path"]: e for e in scan}

    manifest_paths = set(manifest_by_path.keys())
    scan_paths = set(scan_by_path.keys())

    new = [scan_by_path[p] for p in sorted(scan_paths - manifest_paths)]
    deleted = [manifest_by_path[p] for p in sorted(manifest_paths - scan_paths)]
    modified = [
        scan_by_path[p]
        for p in sorted(scan_paths & manifest_paths)
        if scan_by_path[p]["sha256"] != manifest_by_path[p]["sha256"]
    ]

    return {"new": new, "modified": modified, "deleted": deleted}


def prepare_ingest_context(
    source_path: str | Path,
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Prepare structured context for LLM distillation.

    Args:
        source_path: Relative path within source_root.
        source_root: Source notes root.

    Returns:
        Dict with source_content, related_notes, and ingest_prompt.
    """
    config = get_config()
    root = Path(source_root) if source_root else config.source_root
    root = root.expanduser().resolve()

    full_path = root / source_path
    if not full_path.is_file():
        raise FileNotFoundError(f"Source not found: {full_path}")

    content = full_path.read_text(encoding="utf-8")

    # Find related existing notes
    from ledger.retrieval import related_to_text
    related = related_to_text(content[:2000], top_k=5)

    return {
        "source_path": str(source_path),
        "source_content": content,
        "related_notes": related,
        "ingest_prompt": (
            "Read the source below and distill it into 3-8 atomic ledger notes. "
            "Each note should capture one durable idea (fact, preference, goal, "
            "concept, or open loop). Use the related notes to avoid duplicates. "
            "Tag each note with 'ingested' and link back to the source."
        ),
    }


def record_ingest(
    source_path: str,
    derived_notes: list[str],
    source_root: str | Path | None = None,
    notes_dir: Path | None = None,
) -> None:
    """Record that a source was ingested, updating the manifest and timeline.

    Args:
        source_path: Relative path within source_root.
        derived_notes: List of relative paths to notes created from this source.
        source_root: Source notes root.
        notes_dir: Notes directory override.
    """
    config = get_config()
    root = Path(source_root) if source_root else config.source_root
    root = root.expanduser().resolve()
    full_path = root / source_path

    manifest = load_manifest(notes_dir)

    # Update or add entry
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "path": source_path,
        "sha256": _sha256(full_path) if full_path.is_file() else "",
        "ingested_at": now,
        "derived_notes": derived_notes,
    }

    # Replace existing or append
    found = False
    for i, existing in enumerate(manifest):
        if existing["path"] == source_path:
            manifest[i] = entry
            found = True
            break
    if not found:
        manifest.append(entry)

    save_manifest(manifest, notes_dir)

    append_timeline_entry(
        config.timeline_path,
        "updated",
        f"notes/08_indices/{MANIFEST_FILENAME}",
        f"ingested {source_path} -> {len(derived_notes)} note(s)",
        root_dir=config.root_dir,
    )
