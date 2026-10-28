"""People and project hubs: one note per person or project, with an append-only log.

A hub is an ordinary typed note (``10_people/person__<slug>.md``,
``07_projects/project__<slug>.md``) with a ``## Log`` section. Other notes point
at a hub with ``people: [slug]`` / ``projects: [slug]`` frontmatter or a
``[[person__slug]]`` wikilink; :func:`connected` finds them at read time, so
nothing is generated into the hub file.

Agent-written content is marked so nothing downstream mistakes it for reviewed:
``reviewed: false`` in frontmatter (a note an agent created or rewrote) and
``- YYYY-MM-DD (agent) ...`` on log lines. :func:`unreviewed_count` feeds the
note index (``unreviewed``), which yaams reads to rank such notes below curated
ones. :func:`accept` clears the marks once the owner has looked.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.io.safe_write import append_timeline_entry, safe_write_text
from ledger.layout import (
    INDICES_SUBDIR,
    LEDGER_NOTE_SUBDIRS,
    NOTE_LAYOUTS,
    logical_path,
    note_type_dir,
    timeline_path,
)
from ledger.parsing.frontmatter import parse_frontmatter_text, serialize_frontmatter

HUB_KINDS = ("people", "projects")
AGENT_MARK = "(agent)"
_AGENT_LINE = re.compile(r"^- \d{4}-\d{2}-\d{2} \(agent\) ", re.M)
_LOG_DATE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) ", re.M)
_SECTIONS = {
    "people": ("Who", "Working with them", "Log"),
    "projects": ("Goal", "State", "Decisions", "Log"),
}
# Frontmatter field bumped to the log date on every log append.
_LAST_FIELD = {"people": "last_interaction", "projects": "last_activity"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _notes_dir(notes_dir: Path | None) -> Path:
    return Path(notes_dir) if notes_dir is not None else Path(get_config().ledger_notes_dir)


def _check_kind(kind: str) -> None:
    if kind not in HUB_KINDS:
        raise ValueError(f"unknown hub kind {kind!r}; expected one of {HUB_KINDS}")


def hub_path(kind: str, slug: str, notes_dir: Path | None = None) -> Path:
    _check_kind(kind)
    layout = NOTE_LAYOUTS[kind]
    return note_type_dir(_notes_dir(notes_dir), kind) / f"{layout.prefix}{slug}.md"


def _slug_of(path: Path, kind: str) -> str:
    return path.stem.removeprefix(NOTE_LAYOUTS[kind].prefix)


def _read(path: Path) -> tuple[dict[str, Any], str]:
    return parse_frontmatter_text(path.read_text(encoding="utf-8"))


def _write(path: Path, frontmatter: dict[str, Any], body: str, notes_dir: Path, action: str, why: str) -> None:
    safe_write_text(path, serialize_frontmatter(frontmatter) + "\n" + body.lstrip("\n"))
    append_timeline_entry(timeline_path(notes_dir), action, path, why, ledger_notes_dir=notes_dir)


def _is_false(value: Any) -> bool:
    return value is False or str(value).strip().lower() == "false"


def unreviewed_count(frontmatter: dict[str, Any], body: str) -> int:
    """Agent-written content the owner has not reviewed: agent log lines + 1 for ``reviewed: false``."""
    return len(_AGENT_LINE.findall(body)) + (1 if _is_false(frontmatter.get("reviewed")) else 0)


def _title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _section_span(body: str, heading: str) -> tuple[int, int] | None:
    """(start, end) offsets of a ``## heading`` section's content, or None."""
    match = re.search(rf"^## {re.escape(heading)}[ \t]*\n", body, re.M | re.I)
    if match is None:
        return None
    nxt = re.search(r"^## ", body[match.end():], re.M)
    end = match.end() + nxt.start() if nxt else len(body)
    return match.end(), end


def summary(kind: str, path: Path) -> dict[str, Any]:
    fm, body = _read(path)
    dates = _LOG_DATE.findall(body)
    return {
        "slug": _slug_of(path, kind),
        "title": _title(body, path.stem),
        "status": str(fm.get("status", "") or ""),
        "entity": str(fm.get("entity", "") or ""),
        "last": str(fm.get(_LAST_FIELD[kind], "") or (max(dates) if dates else "")),
        "unreviewed": unreviewed_count(fm, body),
        "path": str(path),
    }


def list_hubs(kind: str, notes_dir: Path | None = None) -> list[dict[str, Any]]:
    _check_kind(kind)
    folder = note_type_dir(_notes_dir(notes_dir), kind)
    if not folder.is_dir():
        return []
    prefix = NOTE_LAYOUTS[kind].prefix
    return [summary(kind, p) for p in sorted(folder.glob(f"{prefix}*.md"))]


def _iter_notes(notes_dir: Path):
    for subdir in LEDGER_NOTE_SUBDIRS:
        if subdir == INDICES_SUBDIR:
            continue
        folder = notes_dir / subdir
        if folder.is_dir():
            yield from sorted(folder.glob("*.md"))


def connected(kind: str, slug: str, notes_dir: Path | None = None) -> list[str]:
    """Logical paths of notes that point at a hub via frontmatter or wikilink."""
    _check_kind(kind)
    root = _notes_dir(notes_dir)
    hub = hub_path(kind, slug, root)
    link = f"[[{hub.stem}"
    out = []
    for path in _iter_notes(root):
        if path == hub:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter_text(text)
        refs = fm.get(kind)
        refs = refs if isinstance(refs, list) else [refs] if refs else []
        if slug in [str(r).strip() for r in refs] or link in body:
            out.append(logical_path(path, ledger_notes_dir=root).as_posix())
    return out


def create(
    kind: str,
    slug: str,
    title: str,
    *,
    sections: dict[str, str] | None = None,
    fields: dict[str, Any] | None = None,
    agent: bool = False,
    scope: str = "work",
    lang: str = "en",
    notes_dir: Path | None = None,
) -> Path:
    """Create a hub. Refuses to overwrite. ``agent=True`` marks it ``reviewed: false``."""
    root = _notes_dir(notes_dir)
    path = hub_path(kind, slug, root)
    if path.exists():
        raise FileExistsError(f"{path} exists; use log/set-section to change it")
    now = _now()
    frontmatter: dict[str, Any] = {
        "created": now,
        "updated": now,
        "tags": [NOTE_LAYOUTS[kind].label],
        "confidence": 0.6 if agent else 0.9,
        "source": "assistant" if agent else "user",
        "scope": scope,
        "lang": lang,
    }
    if kind == "projects":
        frontmatter["status"] = "active"
    frontmatter.update({k: v for k, v in (fields or {}).items() if v is not None})
    if agent:
        frontmatter["reviewed"] = False
    sections = sections or {}
    parts = [f"# {title}", ""]
    for heading in _SECTIONS[kind]:
        parts += [f"## {heading}", sections.get(heading, "").strip(), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, frontmatter, "\n".join(parts), root, "created", f"{NOTE_LAYOUTS[kind].label} hub: {title}")
    return path


def log(
    kind: str,
    slug: str,
    text: str,
    *,
    agent: bool = False,
    date: str | None = None,
    notes_dir: Path | None = None,
) -> Path:
    """Append ``- DATE [(agent)] text`` to the hub's ``## Log``."""
    root = _notes_dir(notes_dir)
    path = hub_path(kind, slug, root)
    if not path.exists():
        raise FileNotFoundError(f"no {kind} hub {slug!r} at {path}")
    date = date or _today()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    line = f"- {date} {AGENT_MARK + ' ' if agent else ''}{' '.join(text.split())}"
    fm, body = _read(path)
    span = _section_span(body, "Log")
    if span is None:
        body = body.rstrip("\n") + f"\n\n## Log\n{line}\n"
    else:
        start, end = span
        content = body[start:end].rstrip("\n")
        body = body[:start] + (content + "\n" if content else "") + line + "\n" + ("\n" if end < len(body) else "") + body[end:]
    fm["updated"] = _now()
    if date >= str(fm.get(_LAST_FIELD[kind], "") or ""):
        fm[_LAST_FIELD[kind]] = date
    _write(path, fm, body, root, "updated", f"log: {text[:80]}")
    return path


def set_section(
    kind: str,
    slug: str,
    heading: str,
    text: str,
    *,
    agent: bool = False,
    notes_dir: Path | None = None,
) -> Path:
    """Replace a section's content (e.g. a project's ``## State``). Agent rewrites mark the note unreviewed."""
    if heading.lower() == "log":
        raise ValueError("## Log is append-only; use log()")
    root = _notes_dir(notes_dir)
    path = hub_path(kind, slug, root)
    fm, body = _read(path)
    span = _section_span(body, heading)
    if span is None:
        log_span = _section_span(body, "Log")
        insert_at = (log_span[0] - len("## Log\n")) if log_span else len(body)
        body = body[:insert_at].rstrip("\n") + f"\n\n## {heading}\n{text.strip()}\n\n" + body[insert_at:]
    else:
        start, end = span
        body = body[:start] + text.strip() + "\n" + ("\n" if end < len(body) else "") + body[end:]
    fm["updated"] = _now()
    if agent:
        fm["reviewed"] = False
    _write(path, fm, body, root, "updated", f"{heading}: rewritten{' by agent' if agent else ''}")
    return path


def unreviewed(notes_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every note with agent-written content the owner has not reviewed."""
    root = _notes_dir(notes_dir)
    out = []
    for path in _iter_notes(root):
        fm, body = _read(path)
        n = unreviewed_count(fm, body)
        if n:
            out.append({"path": logical_path(path, ledger_notes_dir=root).as_posix(), "unreviewed": n})
    return out


def accept(path: Path, notes_dir: Path | None = None) -> int:
    """Mark a note reviewed: drop ``reviewed: false`` and the ``(agent)`` log marks. Returns marks cleared."""
    root = _notes_dir(notes_dir)
    path = Path(path)
    if not path.is_absolute():
        rel = path.as_posix().removeprefix("notes/")
        path = root / rel
    fm, body = _read(path)
    n = unreviewed_count(fm, body)
    if not n:
        return 0
    fm.pop("reviewed", None)
    if fm.get("source") == "assistant":
        fm["source"] = "user"
    body = re.sub(r"^(- \d{4}-\d{2}-\d{2}) \(agent\) ", r"\1 ", body, flags=re.M)
    fm["updated"] = _now()
    _write(path, fm, body, root, "updated", "reviewed: agent content accepted")
    return n


def resolve(kind: str, name: str, notes_dir: Path | None = None) -> str | None:
    """Slug for a name: hub slug/title/entity/aliases first, then the yaams canonical name."""
    wanted = {name.strip().lower()}
    hubs = list_hubs(kind, notes_dir)
    canonical = _yaams_canonical(name) if kind == "people" else None
    if canonical:
        wanted.add(canonical.lower())
    for hub in hubs:
        fm, _ = _read(Path(hub["path"]))
        aliases = fm.get("aliases") or []
        keys = {hub["slug"], hub["title"], hub["entity"], *(aliases if isinstance(aliases, list) else [aliases])}
        if wanted & {str(k).strip().lower() for k in keys if k}:
            return hub["slug"]
    return None


def _yaams_canonical(name: str) -> str | None:
    """Canonical person name from yaams' entity dictionary, or None (yaams missing/unknown name)."""
    if shutil.which("yaams") is None:
        return None
    try:
        proc = subprocess.run(
            ["yaams", "entities", "show", name, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        doc = json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return doc.get("canonical") or doc.get("name") or None


def show(kind: str, slug: str, notes_dir: Path | None = None) -> dict[str, Any]:
    root = _notes_dir(notes_dir)
    path = hub_path(kind, slug, root)
    if not path.exists():
        raise FileNotFoundError(f"no {kind} hub {slug!r} at {path}")
    return {
        **summary(kind, path),
        "text": path.read_text(encoding="utf-8"),
        "connected": connected(kind, slug, root),
    }
