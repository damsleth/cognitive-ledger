"""Inbox triage system for Cognitive Ledger.

Raw passive captures land in notes/00_inbox/. This module provides
listing, triage suggestions, and promotion to proper note types.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ledger.config import get_config
from ledger.layout import (
    inbox_dir as layout_inbox_dir,
    is_logical_note_path,
    note_type_dir,
    rejected_candidates_path,
    resolve_path,
)
from ledger.io.safe_write import append_timeline_entry
from ledger.parsing.frontmatter import (
    parse_frontmatter_text,
    serialize_frontmatter,
)


REJECT_REASONS: tuple[str, ...] = ("discarded", "duplicate", "merged", "not_durable")


# Heuristics for suggesting target type from content
_TYPE_SIGNALS: list[tuple[str, list[str]]] = [
    ("preferences", ["prefer", "always", "never", "style", "habit", "going forward"]),
    ("facts", ["decided", "confirmed", "learned", "discovered", "is a", "was a"]),
    ("goals", ["goal", "objective", "target", "achieve", "milestone"]),
    ("loops", ["todo", "revisit", "unresolved", "blocked", "next step", "investigate"]),
    ("concepts", ["concept", "framework", "model", "definition", "pattern"]),
]


def _inbox_dir(notes_dir: Path | None = None) -> Path:
    config = get_config()
    nd = notes_dir or config.ledger_notes_dir
    return layout_inbox_dir(nd)


def list_inbox(notes_dir: Path | None = None) -> list[dict[str, Any]]:
    """List all items in the inbox.

    Returns:
        List of dicts with path, title, created, tags, confidence.
    """
    inbox = _inbox_dir(notes_dir)
    if not inbox.is_dir():
        return []

    items = []
    for path in sorted(inbox.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter_text(text)

        # Extract title from first H1
        title = path.stem.replace("_", " ")
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        items.append({
            "path": str(path),
            "filename": path.name,
            "title": title,
            "created": str(fm.get("created", "")),
            "tags": fm.get("tags", []),
            "confidence": fm.get("confidence", 0.6),
        })

    return items


def triage_suggestions(notes_dir: Path | None = None) -> list[dict[str, Any]]:
    """Suggest target types for inbox items based on content analysis.

    Returns:
        List of dicts with path, title, suggested_type, reason.
    """
    items = list_inbox(notes_dir)
    suggestions = []

    for item in items:
        path = Path(item["path"])
        text = path.read_text(encoding="utf-8").lower()

        suggested = "facts"  # default fallback
        reason = "no strong signal detected, defaulting to fact"

        for note_type, signals in _TYPE_SIGNALS:
            for signal in signals:
                if signal in text:
                    suggested = note_type
                    reason = f"content contains '{signal}'"
                    break
            if suggested != "facts" or reason != "no strong signal detected, defaulting to fact":
                break

        suggestions.append({
            "path": item["path"],
            "filename": item["filename"],
            "title": item["title"],
            "suggested_type": suggested,
            "reason": reason,
        })

    return suggestions


def cleanup_inbox(
    notes_dir: Path | None = None,
    stale_days: int = 14,
    apply: bool = False,
) -> dict[str, list[str]]:
    """Remove orphaned lock files and archive stale auto-generated inbox items.

    Orphaned locks: a .lock file with no corresponding .md file.
    Stale auto-generated: session__* or uncommitted_note_changes* files older
    than stale_days days. These are produced by the ledger pipeline itself and
    accumulate when sessions end without cleanup.

    Args:
        notes_dir: Optional notes directory override.
        stale_days: Age threshold in days for auto-generated items.
        apply: If False, only report what would be removed (dry-run).

    Returns:
        Dict with keys "orphaned_locks", "stale_items", each a list of filenames.
    """
    inbox = _inbox_dir(notes_dir)
    if not inbox.is_dir():
        return {"orphaned_locks": [], "stale_items": []}

    now = datetime.now(timezone.utc)
    orphaned_locks: list[str] = []
    stale_items: list[str] = []

    _AUTO_PREFIXES = ("session__", "session_notes__", "uncommitted_note_changes")

    for lock_file in sorted(inbox.glob("*.lock")):
        md_file = lock_file.with_suffix("")
        if not md_file.exists():
            orphaned_locks.append(lock_file.name)
            if apply:
                lock_file.unlink()

    for md_file in sorted(inbox.glob("*.md")):
        if not any(md_file.name.startswith(p) for p in _AUTO_PREFIXES):
            continue
        try:
            age_days = (now - datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc)).days
        except OSError:
            continue
        if age_days < stale_days:
            continue
        stale_items.append(md_file.name)
        if apply:
            md_file.unlink()
            lock_file = md_file.with_suffix(".md.lock")
            if lock_file.exists():
                lock_file.unlink()

    return {"orphaned_locks": orphaned_locks, "stale_items": stale_items}


def promote(
    path: str | Path,
    target_type: str,
    notes_dir: Path | None = None,
) -> Path:
    """Promote an inbox item to a proper note type.

    Moves the file to the correct folder with the correct prefix,
    updates the timeline.

    Args:
        path: Path to the inbox note.
        target_type: Target note type (facts, preferences, goals, loops, concepts).
        notes_dir: Optional notes directory override.

    Returns:
        Path to the promoted note.
    """
    config = get_config()
    nd = notes_dir or config.ledger_notes_dir
    source = Path(path)

    if not source.is_file():
        raise FileNotFoundError(f"Inbox item not found: {source}")

    type_config = config.note_types.get(target_type)
    if type_config is None:
        raise ValueError(f"Unknown note type: {target_type}")

    # Generate target filename
    prefix = type_config["prefix"]
    slug = source.stem
    # Strip any existing prefix
    for nt in config.note_types.values():
        p = nt["prefix"]
        if slug.startswith(p):
            slug = slug[len(p):]
            break
    # Clean slug
    slug = re.sub(r"[^a-z0-9_]", "_", slug.lower()).strip("_")
    if not slug:
        slug = "untitled"

    target_dir = note_type_dir(nd, target_type)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{prefix}{slug}.md"

    # Avoid overwriting
    counter = 1
    while target.exists():
        target = target_dir / f"{prefix}{slug}_{counter}.md"
        counter += 1

    shutil.move(str(source), str(target))

    append_timeline_entry(
        config.timeline_path,
        "created",
        target,
        f"promoted from inbox to {target_type}",
        root_dir=config.ledger_root,
        ledger_notes_dir=config.ledger_notes_dir,
    )

    return target


def _resolve_inbox_path(path: str | Path, config: Any, nd: Path) -> Path:
    """Resolve an inbox reference to an absolute path.

    Accepts a bare filename (looked up in 00_inbox), a logical `notes/...`
    path, or an absolute path. Mirrors how the rest of the code resolves
    note references.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    if is_logical_note_path(candidate):
        return resolve_path(
            candidate,
            ledger_root=config.ledger_root,
            ledger_notes_dir=nd,
        )
    # Bare name (possibly with subdirs): treat as relative to the inbox.
    if len(candidate.parts) == 1:
        return (layout_inbox_dir(nd) / candidate).resolve()
    # Otherwise fall back to repo-relative resolution.
    return resolve_path(
        candidate,
        ledger_root=config.ledger_root,
        ledger_notes_dir=nd,
    )


def reject_inbox_item(
    path: str | Path,
    *,
    reason: str = "discarded",
    notes_dir: Path | None = None,
    remove: bool = True,
) -> dict[str, Any]:
    """Log a durable rejection signature for an inbox item, then remove it.

    Appends one JSON line to ``08_indices/rejected_candidates.jsonl`` capturing
    the YAAMS provenance carried in the inbox file's frontmatter (contract
    version 1), so YAAMS can suppress regenerating the same candidate. The
    inbox file is deleted afterwards unless ``remove`` is False.

    Args:
        path: Bare filename in 00_inbox, a logical `notes/...` path, or an
            absolute path to the inbox note.
        reason: One of ``discarded | duplicate | merged | not_durable``.
        notes_dir: Optional notes directory override.
        remove: If True (default), delete the inbox file after logging.

    Returns:
        Dict describing what happened.
    """
    if reason not in REJECT_REASONS:
        raise ValueError(
            f"Invalid reason: {reason!r}. Must be one of {', '.join(REJECT_REASONS)}."
        )

    config = get_config()
    nd = notes_dir or config.ledger_notes_dir
    source = _resolve_inbox_path(path, config, nd)

    if not source.is_file():
        raise FileNotFoundError(f"Inbox item not found: {source}")

    text = source.read_text(encoding="utf-8")
    fm, body = parse_frontmatter_text(text)

    # Title from first H1 in the body, filename fallback (mirrors list_inbox).
    title = source.stem.replace("_", " ")
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # Provenance fields degrade open: absent → empty string / empty list.
    source_item_ids = fm.get("yaams_source_item_ids", [])
    if not isinstance(source_item_ids, list):
        source_item_ids = [source_item_ids] if source_item_ids else []

    record = {
        "contract_version": 1,
        "rejected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "yaams_candidate_id": str(fm.get("yaams_candidate_id", "") or ""),
        "yaams_entity": str(fm.get("yaams_entity", "") or ""),
        "yaams_source_item_ids": [str(i) for i in source_item_ids],
        "title": title,
        "filename": source.name,
        "reason": reason,
    }

    rejected_path = rejected_candidates_path(nd)
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    with rejected_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    removed = False
    if remove:
        source.unlink()
        removed = True

    return {
        "filename": source.name,
        "rejected_to": str(rejected_path),
        "yaams_candidate_id": record["yaams_candidate_id"],
        "yaams_entity": record["yaams_entity"],
        "reason": reason,
        "removed": removed,
    }


# ---------------------------------------------------------------------------
# Batch triage (Plan 37, Phase B) data ops
# ---------------------------------------------------------------------------

_CONFLICTS_SUBDIR = "_conflicts"


@dataclass
class InboxCandidate:
    """A single inbox file parsed for the interactive triage view."""

    path: Path
    filename: str
    type: str
    title: str
    confidence: float
    signature: str | None
    yaams_entity: str | None
    merge_with: str | None
    body: str
    created: str
    promoted_by: str | None = None


@dataclass
class TriageAction:
    """A queued decision for a single candidate row (1-based)."""

    row: int
    action: Literal["accept", "reject", "merge", "defer", "skip"]
    target_type: str | None = None
    target_note: Path | None = None  # for merge


def _candidate_title(stem: str, body: str) -> str:
    """First H1 in the body, falling back to a humanized filename stem."""
    title = stem.replace("_", " ")
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return title


def _candidate_type(fm: dict[str, Any], path: Path) -> str:
    """Best-effort target type for a candidate.

    Honours an explicit ``type`` frontmatter field; otherwise infers from a
    note-type prefix on the filename; defaults to ``facts``.
    """
    explicit = str(fm.get("type", "") or "").strip().lower()
    if explicit:
        return explicit
    config = get_config()
    for name, nt in config.note_types.items():
        if path.stem.startswith(nt["prefix"]):
            return name
    return "facts"


def _load_one_candidate(path: Path) -> InboxCandidate:
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter_text(text)

    candidate_id = str(fm.get("yaams_candidate_id", "") or "") or None
    confidence_raw = fm.get("confidence", 0.6)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.6

    return InboxCandidate(
        path=path,
        filename=path.name,
        type=_candidate_type(fm, path),
        title=_candidate_title(path.stem, body),
        confidence=confidence,
        signature=candidate_id,
        yaams_entity=str(fm.get("yaams_entity", "") or "") or None,
        merge_with=str(fm.get("merge_with", "") or "") or None,
        body=body,
        created=str(fm.get("created", "") or ""),
        promoted_by=str(fm.get("promoted_by", "") or "") or None,
    )


def _triage_sort_key(c: InboxCandidate) -> tuple[int, str, str]:
    # yaams candidates first, then group by signature so near-dupes cluster,
    # then oldest-created first.
    is_yaams = 0 if c.promoted_by == "yaams" else 1
    return (is_yaams, c.signature or "~", c.created or "~")


def load_candidates_for_triage(notes_dir: Path | None = None) -> list[InboxCandidate]:
    """Read all inbox files (incl. ``00_inbox/_conflicts/``), parse, sort.

    Sort order: yaams candidates first, then by signature/candidate_id so
    near-duplicates group together, then by created ascending.
    """
    inbox = _inbox_dir(notes_dir)
    if not inbox.is_dir():
        return []

    paths: list[Path] = sorted(inbox.glob("*.md"))
    conflicts = inbox / _CONFLICTS_SUBDIR
    if conflicts.is_dir():
        paths.extend(sorted(conflicts.glob("*.md")))

    candidates = [_load_one_candidate(p) for p in paths]
    candidates.sort(key=_triage_sort_key)
    return candidates


def merge_into(source: Path, target: Path, notes_dir: Path | None = None) -> Path:
    """Merge an inbox candidate's body into an existing promoted note.

    Backs the target up to ``target.bak.YYYYMMDD`` first, appends a clearly
    delimited provenance section (never a blind whole-body append), bumps the
    target's ``updated:`` timestamp, and logs a timeline entry. Returns the
    backup path so callers can surface it to the user.
    """
    config = get_config()
    nd = notes_dir or config.ledger_notes_dir
    source = Path(source)
    target = Path(target)

    if not source.is_file():
        raise FileNotFoundError(f"Inbox item not found: {source}")
    if not target.is_file():
        raise FileNotFoundError(f"Merge target not found: {target}")

    # Back up the target before touching it.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup = target.with_name(f"{target.name}.bak.{stamp}")
    counter = 1
    while backup.exists():
        backup = target.with_name(f"{target.name}.bak.{stamp}.{counter}")
        counter += 1
    shutil.copy2(target, backup)

    src_text = source.read_text(encoding="utf-8")
    src_fm, src_body = parse_frontmatter_text(src_text)
    src_signature = str(src_fm.get("yaams_candidate_id", "") or "") or "(none)"

    tgt_text = target.read_text(encoding="utf-8")
    tgt_fm, tgt_body = parse_frontmatter_text(tgt_text)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    section = (
        f"\n\n## Added from inbox candidate {today}\n\n"
        f"- provenance: merged from `{source.name}` (signature {src_signature})\n\n"
        f"{src_body.strip()}\n"
    )

    tgt_fm = dict(tgt_fm)
    tgt_fm["updated"] = now_iso
    new_text = serialize_frontmatter(tgt_fm) + "\n" + tgt_body.rstrip("\n") + section
    target.write_text(new_text, encoding="utf-8")

    append_timeline_entry(
        config.timeline_path,
        "updated",
        target,
        f"merged inbox candidate {source.name} (signature {src_signature})",
        root_dir=config.ledger_root,
        ledger_notes_dir=config.ledger_notes_dir,
    )

    return backup


def _validate_promoted(path: Path) -> bool:
    """Run note lint on a promoted file; True if it passes (no errors)."""
    from ledger.maintenance import LintCounters, _lint_note

    counters = LintCounters()
    try:
        _lint_note(path, counters)
    except Exception:
        return False
    return counters.errors == 0


def apply_actions(
    candidates: list[InboxCandidate],
    actions: list[TriageAction],
    notes_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute queued triage actions, one row at a time.

    Per-row failures are caught and recorded; the batch continues. Accept-time
    safety: after ``promote()`` the promoted file is linted, and if it fails it
    is moved back to the inbox and the row is marked failed.

    Returns a summary dict with per-action counts plus ``failed`` and detail
    lists (``accepted_paths``, ``backups``, ``errors``).
    """
    config = get_config()
    nd = notes_dir or config.ledger_notes_dir

    summary: dict[str, Any] = {
        "accepted": 0,
        "rejected": 0,
        "merged": 0,
        "deferred": 0,
        "skipped": 0,
        "failed": 0,
        "accepted_paths": [],
        "backups": [],
        "errors": [],
    }

    by_row = {c_idx + 1: c for c_idx, c in enumerate(candidates)}

    for action in actions:
        candidate = by_row.get(action.row)
        if candidate is None:
            summary["failed"] += 1
            summary["errors"].append(f"#{action.row}: no such candidate")
            continue

        try:
            if action.action == "accept":
                target_type = action.target_type or candidate.type
                target = promote(candidate.path, target_type, notes_dir=nd)
                if not _validate_promoted(target):
                    # Move it back to the inbox and fail the row.
                    restored = _inbox_dir(nd) / candidate.filename
                    counter = 1
                    while restored.exists():
                        restored = _inbox_dir(nd) / f"{candidate.path.stem}_{counter}.md"
                        counter += 1
                    shutil.move(str(target), str(restored))
                    summary["failed"] += 1
                    summary["errors"].append(
                        f"#{action.row}: {candidate.filename} failed validation, left in inbox"
                    )
                    continue
                summary["accepted"] += 1
                summary["accepted_paths"].append(str(target))

            elif action.action == "reject":
                reject_inbox_item(
                    candidate.path, reason="discarded", notes_dir=nd, remove=True
                )
                summary["rejected"] += 1

            elif action.action == "merge":
                if action.target_note is None:
                    summary["failed"] += 1
                    summary["errors"].append(
                        f"#{action.row}: no merge target"
                    )
                    continue
                backup = merge_into(candidate.path, action.target_note, notes_dir=nd)
                summary["backups"].append(str(backup))
                # Remove the inbox candidate after a successful merge.
                if candidate.path.is_file():
                    candidate.path.unlink()
                summary["merged"] += 1

            elif action.action == "defer":
                summary["deferred"] += 1

            elif action.action == "skip":
                summary["skipped"] += 1

            else:  # pragma: no cover - guarded by parser
                summary["failed"] += 1
                summary["errors"].append(
                    f"#{action.row}: unknown action {action.action!r}"
                )
        except Exception as exc:  # noqa: BLE001 - report and continue
            summary["failed"] += 1
            summary["errors"].append(f"#{action.row}: {exc}")

    return summary
