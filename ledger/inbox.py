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
from ledger.io.safe_write import append_timeline_entry, safe_write_text
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


def _triage_text(raw: str) -> str:
    """Return the lowercased text used for type inference.

    The note *type* should reflect its core claim, so we scope inference to the
    title plus the ``## Statement`` section when one is present (the standard
    note shape). Incidental words in ``## Detail`` / ``## Sources`` — e.g. a
    "Modell:" line or a prose "pattern" — must not hijack the bucket. Notes
    without a Statement section fall back to the whole body.
    """
    body = raw
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) == 3:
            body = parts[2]

    title = ""
    statement: list[str] = []
    in_statement = False
    for line in body.splitlines():
        stripped = line.strip()
        if not title and stripped.startswith("# "):
            title = stripped[2:]
        if stripped.lower().startswith("## statement"):
            in_statement = True
            continue
        if stripped.startswith("## "):
            in_statement = False
            continue
        if in_statement:
            statement.append(line)

    scoped = f"{title}\n{chr(10).join(statement)}" if statement else body
    return scoped.lower()


def _signal_matches(signal: str, text: str) -> bool:
    """Whole-word match so 'model' does not match 'Modell', 'is a' is a real
    phrase rather than an incidental substring, etc."""
    return re.search(rf"\b{re.escape(signal)}\b", text) is not None


def triage_suggestions(notes_dir: Path | None = None) -> list[dict[str, Any]]:
    """Suggest target types for inbox items based on content analysis.

    Returns:
        List of dicts with path, title, suggested_type, reason.
    """
    items = list_inbox(notes_dir)
    suggestions = []

    for item in items:
        path = Path(item["path"])
        text = _triage_text(path.read_text(encoding="utf-8"))

        suggested = "facts"  # default fallback
        reason = "no strong signal detected, defaulting to fact"

        for note_type, signals in _TYPE_SIGNALS:
            for signal in signals:
                if _signal_matches(signal, text):
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


def reap_unheld_locks(
    notes_dir: Path | None = None,
    apply: bool = False,
) -> list[str]:
    """Remove ``*.lock`` files across the notes tree that nobody holds.

    ``FileLock`` deliberately does not unlink on release — unlinking after
    releasing the flock lets a waiter and a newcomer both believe they hold the
    lock. The files therefore accumulate, and ``cleanup_inbox`` never caught
    them: it only looked in ``00_inbox`` and only at locks whose ``.md`` sibling
    was *missing*. A batch import leaves one lock per note next to a note that
    very much exists, in whatever typed folder it landed in. 42 of them were
    sitting in ``05_open_loops/`` when this was written.

    Safe because it is an explicit maintenance sweep, not the write path: a lock
    is only removed if a non-blocking flock succeeds, which proves no process
    holds it, and the unlink happens while we hold it.
    """
    import fcntl
    import os

    nd = Path(notes_dir) if notes_dir else Path(get_config().ledger_notes_dir)
    if not nd.is_dir():
        return []

    reaped: list[str] = []
    for lock_file in sorted(nd.rglob("*.lock")):
        fd = None
        try:
            fd = os.open(str(lock_file), os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            continue          # someone holds it — leave it alone
        except OSError:
            continue
        else:
            reaped.append(str(lock_file.relative_to(nd)))
            if apply:
                try:
                    lock_file.unlink()
                except OSError:
                    reaped.pop()
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
    return reaped


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

    YAAMS candidates (files with ``yaams_candidate_id`` frontmatter or
    ``promoted_by: yaams``) are logged to rejected_candidates.jsonl via
    ``reject_inbox_item`` instead of plain unlink when ``apply`` is True.

    Args:
        notes_dir: Optional notes directory override.
        stale_days: Age threshold in days for auto-generated items.
        apply: If False, only report what would be removed (dry-run).

    Returns:
        Dict with keys "orphaned_locks", "stale_items",
        "logged_rejections" (filenames logged as rejections), and
        "unheld_locks" (accumulated lock files reaped tree-wide).
    """
    inbox = _inbox_dir(notes_dir)
    if not inbox.is_dir():
        return {
            "orphaned_locks": [],
            "stale_items": [],
            "logged_rejections": [],
            "unheld_locks": reap_unheld_locks(notes_dir=notes_dir, apply=apply),
        }

    now = datetime.now(timezone.utc)
    orphaned_locks: list[str] = []
    stale_items: list[str] = []
    logged_rejections: list[str] = []

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
            # Check if this is a YAAMS candidate before removing.
            is_yaams = False
            try:
                text = md_file.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter_text(text)
                if fm.get("yaams_candidate_id") or fm.get("promoted_by") == "yaams":
                    is_yaams = True
            except Exception:
                pass

            if is_yaams:
                try:
                    reject_inbox_item(md_file, reason="discarded", notes_dir=notes_dir, remove=True)
                    logged_rejections.append(md_file.name)
                except Exception:
                    # Fallback to plain unlink if rejection logging fails.
                    md_file.unlink()
            else:
                md_file.unlink()
                lock_file = md_file.with_suffix(".md.lock")
                if lock_file.exists():
                    lock_file.unlink()

    return {
        "orphaned_locks": orphaned_locks,
        "stale_items": stale_items,
        "logged_rejections": logged_rejections,
        "unheld_locks": reap_unheld_locks(notes_dir=notes_dir, apply=apply),
    }


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

    Raises ValueError if the resolved path escapes the inbox directory.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    elif is_logical_note_path(candidate):
        resolved = resolve_path(
            candidate,
            ledger_root=config.ledger_root,
            ledger_notes_dir=nd,
        )
    elif len(candidate.parts) == 1:
        # Bare name: look up in inbox
        resolved = (layout_inbox_dir(nd) / candidate).resolve()
    else:
        # Otherwise fall back to repo-relative resolution.
        resolved = resolve_path(
            candidate,
            ledger_root=config.ledger_root,
            ledger_notes_dir=nd,
        )
    # Containment check: resolved path must be under the inbox directory
    inbox_dir = layout_inbox_dir(nd).resolve()
    try:
        resolved.relative_to(inbox_dir)
    except ValueError:
        raise ValueError(
            f"Path {resolved!r} escapes the inbox directory {inbox_dir!r}"
        )
    return resolved


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


def list_rejections(
    notes_dir: Path | None = None,
    since_days: int | None = None,
) -> list[dict[str, Any]]:
    """Return rejection records from rejected_candidates.jsonl, newest first.

    Missing file returns [].
    Malformed JSONL lines are silently skipped (file is never rewritten).

    Args:
        notes_dir: Optional notes directory override.
        since_days: Keep records whose ``rejected_at`` is within the last N
            days.  Parses ISO-Z timestamps via strptime; records with
            unparseable timestamps are excluded when a filter is active.

    Returns:
        List of record dicts sorted by ``rejected_at`` descending.
    """
    config = get_config()
    nd = notes_dir or config.ledger_notes_dir
    rpath = rejected_candidates_path(nd)

    if not rpath.exists():
        return []

    records: list[dict[str, Any]] = []
    for line in rpath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        records.append(rec)

    if since_days is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400
        filtered: list[dict[str, Any]] = []
        for rec in records:
            try:
                ts = datetime.strptime(rec["rejected_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                if ts.timestamp() >= cutoff:
                    filtered.append(rec)
            except (KeyError, ValueError):
                pass
        records = filtered

    records.sort(
        key=lambda r: r.get("rejected_at", ""),
        reverse=True,
    )
    return records


def clear_rejections(
    notes_dir: Path | None = None,
    before: datetime | None = None,
) -> int:
    """Remove rejection records; return number removed.

    - ``before`` is None: truncate the whole file; returns previous
      good-line count.
    - ``before`` set (UTC-aware datetime): rewrite keeping records with
      ``rejected_at >= before``.  Malformed lines are preserved verbatim.
    - Missing file returns 0.
    - Write is atomic via a temp file + os.replace.

    Args:
        notes_dir: Optional notes directory override.
        before: Optional upper bound (exclusive) for removal; records
            *before* this timestamp are removed.

    Returns:
        Number of well-formed records that were removed.
    """
    import os
    import tempfile

    config = get_config()
    nd = notes_dir or config.ledger_notes_dir
    rpath = rejected_candidates_path(nd)

    if not rpath.exists():
        return 0

    raw_lines = rpath.read_text(encoding="utf-8").splitlines(keepends=True)

    if before is None:
        # Count valid lines then truncate.
        removed = 0
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
                removed += 1
            except json.JSONDecodeError:
                pass
        rpath.write_text("", encoding="utf-8")
        return removed

    # Selective rewrite: keep records with rejected_at >= before; preserve
    # malformed lines verbatim.
    keep_lines: list[str] = []
    removed = 0
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            keep_lines.append(line)
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            # Preserve malformed line as-is.
            keep_lines.append(line)
            continue
        try:
            ts = datetime.strptime(rec["rejected_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except (KeyError, ValueError):
            keep_lines.append(line)
            continue
        if ts >= before:
            keep_lines.append(line)
        else:
            removed += 1

    # Atomic write.
    dir_ = rpath.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.writelines(keep_lines)
        os.replace(tmp_path, rpath)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return removed


def is_rejected(
    yaams_candidate_id: str,
    notes_dir: Path | None = None,
) -> bool:
    """Return True if ``yaams_candidate_id`` appears in the rejection log.

    Empty/None id returns False immediately (no I/O).
    Performs an exact-match linear scan; no caching.

    Args:
        yaams_candidate_id: The candidate id to look up.
        notes_dir: Optional notes directory override.

    Returns:
        True if a matching record exists, False otherwise.
    """
    if not yaams_candidate_id:
        return False

    config = get_config()
    nd = notes_dir or config.ledger_notes_dir
    rpath = rejected_candidates_path(nd)

    if not rpath.exists():
        return False

    for line in rpath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("yaams_candidate_id") == yaams_candidate_id:
            return True

    return False


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
    dedup_similarity: float | None = None
    conflict_classification: str | None = None
    conflict_confidence: float | None = None
    conflict_reason: str | None = None
    review_question: str | None = None
    review_options: tuple[str, ...] = ()
    review_requires_rewrite: bool = False


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

    def _coerce_float(val: object) -> float | None:
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    raw_review_options = fm.get("review_options", [])
    if isinstance(raw_review_options, list):
        review_options = tuple(
            str(option).strip() for option in raw_review_options if str(option).strip()
        )
    elif raw_review_options:
        review_options = (str(raw_review_options).strip(),)
    else:
        review_options = ()

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
        dedup_similarity=_coerce_float(fm.get("dedup_similarity")),
        conflict_classification=str(fm.get("conflict_classification", "") or "") or None,
        conflict_confidence=_coerce_float(fm.get("conflict_confidence")),
        conflict_reason=str(fm.get("conflict_reason", "") or "") or None,
        review_question=str(fm.get("review_question", "") or "").strip() or None,
        review_options=review_options,
        review_requires_rewrite=str(fm.get("review_requires_rewrite", "")).lower()
        in {"1", "true", "yes", "on"},
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
    safe_write_text(target, new_text)

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
                    # Move it back to the original inbox subdirectory (preserves
                    # _conflicts/ origin if the note came from there).
                    restore_dir = candidate.path.parent
                    restored = restore_dir / candidate.filename
                    counter = 1
                    while restored.exists():
                        restored = restore_dir / f"{candidate.path.stem}_{counter}.md"
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
