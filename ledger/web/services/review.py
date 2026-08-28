"""Mutation service for the local web review queue.

The web layer deliberately reuses the inbox triage primitives. Candidates stay
in ``00_inbox`` until a human decision, accepted notes pass the same lint gate
as the TUI, and rejected YAAMS signatures are recorded by the canonical
rejection path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.inbox import (
    REJECT_REASONS,
    InboxCandidate,
    TriageAction,
    apply_actions,
    load_candidates_for_triage,
    reject_inbox_item,
)
from ledger.io.safe_write import safe_write_text
from ledger.layout import resolve_path
from ledger.parsing.frontmatter import parse_frontmatter_text, serialize_frontmatter
from ledger.validation import validate_note_type


ANSWER_PLACEHOLDER = "{{answer}}"
MAX_TITLE_LENGTH = 240
MAX_BODY_LENGTH = 100_000
_SOURCE_CAPTURE_PREFIXES = (
    "note__ingest_summary_",
    "session__",
    "session_notes__",
    "uncommitted_note_changes",
)


@dataclass(frozen=True)
class ReviewItem:
    """An inbox candidate with a stable, non-path identifier for web forms."""

    id: str
    candidate: InboxCandidate
    requires_rewrite: bool = False


def _relative_candidate_path(candidate: InboxCandidate, notes_dir: Path) -> str:
    inbox = (Path(notes_dir) / "00_inbox").resolve()
    return candidate.path.resolve().relative_to(inbox).as_posix()


def candidate_id(candidate: InboxCandidate, notes_dir: Path) -> str:
    relative = _relative_candidate_path(candidate, notes_dir)
    return hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]


def is_source_capture(candidate: InboxCandidate) -> bool:
    """Return whether an inbox file is source material, not an atomic proposal."""
    return candidate.filename.startswith(_SOURCE_CAPTURE_PREFIXES)


def load_review_queue(notes_dir: Path) -> tuple[list[ReviewItem], int]:
    """Load the queue and source-capture count with one inbox scan."""
    items: list[ReviewItem] = []
    source_count = 0
    for candidate in load_candidates_for_triage(notes_dir):
        if is_source_capture(candidate):
            source_count += 1
            continue
        items.append(
            ReviewItem(
                candidate_id(candidate, notes_dir),
                candidate,
                candidate.review_requires_rewrite,
            )
        )
    return items, source_count


def load_review_items(notes_dir: Path) -> list[ReviewItem]:
    return load_review_queue(notes_dir)[0]


def source_capture_count(notes_dir: Path) -> int:
    """Count inbox source captures deliberately excluded from quick review."""
    return load_review_queue(notes_dir)[1]


def find_review_item(notes_dir: Path, item_id: str) -> ReviewItem:
    for item in load_review_items(notes_dir):
        if item.id == item_id:
            return item
    raise FileNotFoundError("Review candidate not found; the queue may have changed")


def _replace_title(body: str, title: str) -> str:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("# "):
            lines[index] = f"# {title}"
            return "\n".join(lines).strip() + "\n"
    return f"# {title}\n\n{body.strip()}\n"


def _validate_edit(title: str, body: str) -> tuple[str, str]:
    clean_title = " ".join(title.split())
    if not clean_title:
        raise ValueError("Title cannot be empty")
    if len(clean_title) > MAX_TITLE_LENGTH:
        raise ValueError(f"Title cannot exceed {MAX_TITLE_LENGTH} characters")
    if not body.strip():
        raise ValueError("Note body cannot be empty")
    if len(body) > MAX_BODY_LENGTH:
        raise ValueError(f"Note body cannot exceed {MAX_BODY_LENGTH} characters")
    return clean_title, body


def _prepare_human_confirmed_candidate(
    item: ReviewItem,
    *,
    notes_dir: Path,
    title: str,
    body: str,
    target_type: str,
    answer: str | None = None,
) -> InboxCandidate:
    """Apply a reviewed edit to the inbox file before lint-gated promotion."""
    target_type = validate_note_type(target_type)
    if target_type == "all":
        raise ValueError("'all' is not a promotable note type")
    title, body = _validate_edit(title, body)

    allowed_options = item.candidate.review_options
    if answer is not None:
        if answer not in allowed_options:
            raise ValueError("Answer is not one of this candidate's review options")
        if ANSWER_PLACEHOLDER not in title and ANSWER_PLACEHOLDER not in body:
            raise ValueError(
                f"Choice candidates must contain {ANSWER_PLACEHOLDER} in title or body"
            )
        title = title.replace(ANSWER_PLACEHOLDER, answer)
        body = body.replace(ANSWER_PLACEHOLDER, answer)

    text = item.candidate.path.read_text(encoding="utf-8")
    frontmatter, _ = parse_frontmatter_text(text)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    frontmatter = dict(frontmatter)
    frontmatter["updated"] = now
    frontmatter["reviewed_at"] = now
    frontmatter["reviewed_by"] = "user"
    frontmatter["source"] = "user"
    try:
        frontmatter["confidence"] = max(float(frontmatter.get("confidence", 0.6)), 0.9)
    except (TypeError, ValueError):
        frontmatter["confidence"] = 0.9
    if target_type == "loops" and not frontmatter.get("status"):
        frontmatter["status"] = "open"
    if answer is not None:
        frontmatter["review_answer"] = answer
    frontmatter.pop("review_question", None)
    frontmatter.pop("review_options", None)
    frontmatter.pop("review_requires_rewrite", None)

    rewritten_body = _replace_title(body, title)
    safe_write_text(
        item.candidate.path,
        serialize_frontmatter(frontmatter) + "\n\n" + rewritten_body,
    )

    # Re-read so apply_actions validates and promotes the exact bytes written.
    refreshed = find_review_item(notes_dir, item.id)
    return refreshed.candidate


def approve(
    item: ReviewItem,
    *,
    notes_dir: Path,
    title: str,
    body: str,
    target_type: str,
    answer: str | None = None,
    confirm_conflict: bool = False,
) -> dict[str, Any]:
    """Rewrite if requested, mark human confirmation, lint, and promote."""
    target_type = validate_note_type(target_type)
    if item.candidate.conflict_classification == "contradict" and not confirm_conflict:
        raise ValueError("Contradictions require explicit confirmation before approval")
    candidate = _prepare_human_confirmed_candidate(
        item,
        notes_dir=notes_dir,
        title=title,
        body=body,
        target_type=target_type,
        answer=answer,
    )
    summary = apply_actions(
        [candidate],
        [TriageAction(row=1, action="accept", target_type=target_type)],
        notes_dir=notes_dir,
    )
    if summary["failed"]:
        raise ValueError(summary["errors"][0])
    return summary


def reject(
    item: ReviewItem,
    *,
    notes_dir: Path,
    reason: str,
) -> dict[str, Any]:
    if reason not in REJECT_REASONS:
        raise ValueError("Unknown rejection reason")
    result = reject_inbox_item(
        item.candidate.path,
        reason=reason,
        notes_dir=notes_dir,
        remove=True,
    )
    return {"rejected": 1, "result": result}


def merge(item: ReviewItem, *, notes_dir: Path) -> dict[str, Any]:
    if not item.candidate.merge_with:
        raise ValueError("Candidate has no merge target")
    config = get_config()
    target = resolve_path(
        item.candidate.merge_with,
        ledger_root=config.ledger_root,
        ledger_notes_dir=notes_dir,
    )
    summary = apply_actions(
        [item.candidate],
        [TriageAction(row=1, action="merge", target_note=target)],
        notes_dir=notes_dir,
    )
    if summary["failed"]:
        raise ValueError(summary["errors"][0])
    return summary
