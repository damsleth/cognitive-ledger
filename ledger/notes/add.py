"""Create a single note from a body of text.

The canonical write path for `ledger notes add` and for AI/automation
callers (owa-* "save this", etc.). Builds a properly
shaped markdown note with frontmatter, derives a slug, places the
file in either ``00_inbox/`` (default, conservative) or the typed
folder (``--no-inbox``), and records a timeline entry.

This is the missing companion to :func:`ledger.inbox.promote` — read
side has had ``ledger notes --type X`` for ages, but until now there
was no programmatic write verb. The :func:`ledger.inbox.promote`
helper handled inbox → typed-folder moves, but new captures had to
be written by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ledger.config import LedgerConfig, get_config
from ledger.io.safe_write import append_timeline_entry, safe_write_text
from ledger.layout import (
    INBOX_SUBDIR,
    NOTE_LAYOUTS,
    inbox_dir,
    note_type_dir,
    note_type_layout,
)


# Accept the natural variants callers (humans, owa-*) use.
# Canonical keys are the NOTE_LAYOUTS keys (plural for everything
# except identity/loops which are already singular-shaped or plural-shaped
# correctly).
_TYPE_ALIASES: dict[str, str] = {
  # canonical -> canonical
  "facts": "facts",
  "preferences": "preferences",
  "goals": "goals",
  "loops": "loops",
  "concepts": "concepts",
  "identity": "identity",
  # singular -> canonical
  "fact": "facts",
  "preference": "preferences",
  "pref": "preferences",
  "prefs": "preferences",
  "goal": "goals",
  "loop": "loops",
  "concept": "concepts",
  "id": "identity",
}


@dataclass(frozen=True)
class AddResult:
  """Outcome of a successful add_note() call."""

  path: Path
  note_type: str  # canonical key (e.g. "facts")
  inbox: bool
  slug: str

  def to_dict(self, *, ledger_notes_dir: Path | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
      "path": str(self.path),
      "type": self.note_type,
      "inbox": self.inbox,
      "slug": self.slug,
    }
    if ledger_notes_dir is not None:
      try:
        rel = self.path.relative_to(Path(ledger_notes_dir))
        out["logical_path"] = f"notes/{rel.as_posix()}"
      except ValueError:
        pass
    return out


class AddNoteError(Exception):
  """Raised when a note cannot be added."""


def normalize_type(note_type: str) -> str:
  """Map a user-supplied type label to its canonical NOTE_LAYOUTS key.

  Raises :class:`AddNoteError` for unknown labels with a helpful
  ``choose from {...}`` message that mirrors argparse's own format.
  """
  key = note_type.strip().lower()
  if key in _TYPE_ALIASES:
    return _TYPE_ALIASES[key]
  choices = sorted(set(_TYPE_ALIASES.keys()))
  raise AddNoteError(
    f"unknown note type {note_type!r}; choose from {choices}"
  )


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SLUG_TRIM = re.compile(r"^_+|_+$")


def derive_slug(seed: str, *, max_length: int = 60) -> str:
  """Derive a filename-safe slug from a free-text seed."""
  slug = seed.lower()
  slug = _SLUG_NON_ALNUM.sub("_", slug)
  slug = _SLUG_TRIM.sub("", slug)
  if len(slug) > max_length:
    slug = slug[:max_length].rsplit("_", 1)[0] or slug[:max_length]
  return slug or "untitled"


def _extract_title_from_body(body: str) -> str:
  """First H1 heading, or first non-blank line, truncated to ~60 chars."""
  for line in body.splitlines():
    stripped = line.strip()
    if stripped.startswith("# "):
      return stripped[2:].strip()
  for line in body.splitlines():
    stripped = line.strip()
    if stripped:
      return stripped[:60]
  return "untitled"


def _has_frontmatter(text: str) -> bool:
  return text.lstrip().startswith("---")


def _build_frontmatter(
  *,
  now: datetime,
  tags: Iterable[str],
  confidence: float,
  source: str,
  scope: str,
  lang: str,
  status: str | None = None,
) -> str:
  ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
  tag_list = ", ".join(sorted({t.strip() for t in tags if t and t.strip()}))
  status_line = f"status: {status}\n" if status else ""
  return (
    "---\n"
    f"created: {ts}\n"
    f"updated: {ts}\n"
    f"tags: [{tag_list}]\n"
    f"confidence: {confidence}\n"
    f"source: {source}\n"
    f"scope: {scope}\n"
    f"lang: {lang}\n"
    f"{status_line}"
    "---\n\n"
  )


def _normalize_link(link: str) -> str:
  """Render note references as wikilinks while preserving external links."""
  value = link.strip()
  if (
    (value.startswith("[[") and value.endswith("]]"))
    or value.startswith(("http://", "https://", "mailto:"))
    or (value.startswith("[") and "](" in value and value.endswith(")"))
  ):
    return value
  return f"[[{value}]]"


def _build_body(
  *,
  raw_body: str,
  title: str,
  links: Iterable[str],
  ensure_next_action: bool = False,
) -> str:
  body = raw_body.strip()
  has_h1 = any(line.strip().startswith("# ") for line in body.splitlines())
  out: list[str] = []
  if not has_h1:
    out.append(f"# {title}")
    out.append("")
  out.append(body)
  if ensure_next_action and not re.search(
    r"^##\s+Next action\s*$", body, re.MULTILINE | re.IGNORECASE
  ):
    out.extend(["", "## Next action", "", "- [ ] Define the next concrete action."])
  link_list = [link for link in links if link and link.strip()]
  if link_list:
    out.append("")
    out.append("## Links")
    for link in link_list:
      out.append(f"- {_normalize_link(link)}")
  if not out[-1].endswith("\n"):
    out.append("")
  return "\n".join(out)


def _resolve_target(
  *,
  notes_dir: Path,
  note_type: str,
  prefix: str,
  slug: str,
  inbox: bool,
) -> Path:
  if inbox:
    target_dir = inbox_dir(notes_dir)
  else:
    target_dir = note_type_dir(notes_dir, note_type)
  target_dir.mkdir(parents=True, exist_ok=True)
  candidate = target_dir / f"{prefix}{slug}.md"
  counter = 1
  while candidate.exists():
    candidate = target_dir / f"{prefix}{slug}_{counter}.md"
    counter += 1
  return candidate


def add_note(
  *,
  body: str,
  note_type: str,
  inbox: bool = True,
  slug: str | None = None,
  title: str | None = None,
  tags: Iterable[str] = (),
  links: Iterable[str] = (),
  source: str = "assistant",
  scope: str = "work",
  lang: str = "en",
  confidence: float = 0.7,
  config: LedgerConfig | None = None,
  now: datetime | None = None,
) -> AddResult:
  """Create a new note from ``body`` and return where it landed.

  Args:
    body: The note's main text. Can be plain prose or already-formatted
      markdown. If it doesn't contain an H1 heading, one is prepended
      from ``title`` (or derived from the first line).
    note_type: Type label. Accepts canonical (``facts``, ``preferences``,
      ...) and natural variants (``fact``, ``pref``, ``id``, ...).
    inbox: If True (default), write to ``00_inbox/``; the user can
      later promote via ``ledger inbox triage``. If False, write
      straight to the typed folder.
    slug: Filename slug (no prefix). Auto-derived from title/body if
      omitted.
    title: H1 heading text. Defaults to derived-from-body.
    tags / links: Optional metadata. Tags go into frontmatter; links
      become a ``## Links`` section.
    source / scope / lang / confidence: Frontmatter fields. Defaults
      match the typical AI-assistant capture.
    config: Test injection point. Real callers pass nothing.
    now: Test injection point for timestamps.

  Returns:
    :class:`AddResult` describing the written file.

  Raises:
    :class:`AddNoteError`: bad type label or empty body.
  """
  if not body or not body.strip():
    raise AddNoteError("body is empty")

  canonical_type = normalize_type(note_type)
  layout = note_type_layout(canonical_type)

  effective_title = (title or _extract_title_from_body(body)).strip() or "untitled"
  effective_slug = (slug or derive_slug(effective_title)).strip() or derive_slug(effective_title)

  cfg = config if config is not None else get_config()
  now = now or datetime.now(timezone.utc)

  frontmatter = _build_frontmatter(
    now=now,
    tags=tags,
    confidence=confidence,
    source=source,
    scope=scope,
    lang=lang,
    status="open" if canonical_type == "loops" else None,
  )
  body_text = _build_body(
    raw_body=body,
    title=effective_title,
    links=links,
    ensure_next_action=canonical_type == "loops",
  )
  content = frontmatter + body_text + ("\n" if not body_text.endswith("\n") else "")

  target = _resolve_target(
    notes_dir=cfg.ledger_notes_dir,
    note_type=canonical_type,
    prefix=layout.prefix,
    slug=effective_slug,
    inbox=inbox,
  )

  safe_write_text(target, content)

  append_timeline_entry(
    cfg.timeline_path,
    "created",
    target,
    f"added to {INBOX_SUBDIR}" if inbox else f"added to {layout.subdir}",
    root_dir=cfg.ledger_root,
    ledger_notes_dir=cfg.ledger_notes_dir,
  )

  return AddResult(
    path=target,
    note_type=canonical_type,
    inbox=inbox,
    slug=effective_slug,
  )


__all__ = [
  "AddNoteError",
  "AddResult",
  "add_note",
  "derive_slug",
  "normalize_type",
]
