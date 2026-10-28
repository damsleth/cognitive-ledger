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
from ledger.io.safe_write import FileLock, append_timeline_entry, safe_write_text
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
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
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


def _split(text: str) -> tuple[str, str]:
    """(frontmatter block incl. ``---`` lines, body). No frontmatter -> ("", text)."""
    match = re.match(r"---\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", text, re.S)
    return (match.group(0), text[match.end():]) if match else ("", text)


def _patch_frontmatter(block: str, set_fields: dict[str, Any], drop: tuple[str, ...] = ()) -> str:
    """Change only the named single-line keys; every other byte of the block is kept."""
    if not block:
        block = "---\n---\n"
    for key in drop:
        block = re.sub(rf"^{re.escape(key)}:.*\r?\n", "", block, flags=re.M)
    for key, value in set_fields.items():
        line = serialize_frontmatter({key: value}).splitlines()[1]
        pattern = rf"^{re.escape(key)}:.*$"
        existing = re.search(rf"^{re.escape(key)}:(.*)\r?\n([ \t]+\S)?", block, re.M)
        if existing and (existing.group(1).strip() in ("|", ">", "|-", ">-", "|+", ">+") or existing.group(2)):
            raise ValueError(f"frontmatter '{key}' spans several lines; edit it by hand")
        if re.search(pattern, block, re.M):
            block = re.sub(pattern, lambda _m: line, block, count=1, flags=re.M)
        else:
            closing = block.rstrip().rfind("---")
            block = block[:closing] + line + "\n" + block[closing:]
    return block


def _mutate(path: Path, notes_dir: Path, why: str, edit) -> Any:
    """Locked read-modify-write: ``edit(fm, block, body) -> (block, body, result)``."""
    with FileLock(path):
        if not path.exists():
            raise FileNotFoundError(f"no hub at {path}")
        text = path.read_text(encoding="utf-8")
        block, body = _split(text)
        fm, _ = parse_frontmatter_text(text)
        block, body, result = edit(fm, block, body)
        safe_write_text(path, block + body, use_lock=False)
    append_timeline_entry(timeline_path(notes_dir), "updated", path, why, ledger_notes_dir=notes_dir)
    return result


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


def _section_span(body: str, heading: str) -> tuple[int, int, int] | None:
    """(heading start, content start, end) of the one ``## heading`` section, or None.

    The section ends at the next heading of level 1 or 2 (or EOF). Two sections
    with the same heading are ambiguous and refused.
    """
    matches = list(re.finditer(rf"^## {re.escape(heading)}[ \t]*(?:\r?\n|\Z)", body, re.M | re.I))
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"note has {len(matches)} '## {heading}' sections; fix it by hand first")
    match = matches[0]
    nxt = re.search(r"^#{1,2} ", body[match.end():], re.M)
    end = match.end() + nxt.start() if nxt else len(body)
    return match.start(), match.end(), end


def _replace_span(body: str, start: int, end: int, content: str) -> str:
    """Put ``content`` between start and end, keeping one blank line before what follows."""
    tail = body[end:]
    content = content.rstrip("\n")
    return body[:start] + (content + "\n" if content else "") + ("\n" if tail else "") + tail


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
    target = hub.stem.lower()
    out = []
    for path in _iter_notes(root):
        if path == hub:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter_text(text)
        refs = fm.get(kind)
        refs = refs if isinstance(refs, list) else [refs] if refs else []
        links = {m.strip().lower() for m in _WIKILINK.findall(body)}
        if slug in [str(r).strip() for r in refs] or target in links:
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
    with FileLock(path):
        if path.exists():
            raise FileExistsError(f"{path} exists; use log/set to change it")
        safe_write_text(path, serialize_frontmatter(frontmatter) + "\n" + "\n".join(parts), use_lock=False)
    append_timeline_entry(timeline_path(root), "created", path, f"{NOTE_LAYOUTS[kind].label} hub: {title}",
                          ledger_notes_dir=root)
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
    if "<private" in text.lower():
        # A log line is one line: a private fence would not survive it. Keep it out.
        raise ValueError("private content does not belong in a hub log")
    line = f"- {date} {AGENT_MARK + ' ' if agent else ''}{' '.join(text.split())}"
    last_field = _LAST_FIELD[kind]

    def edit(fm, block, body):
        span = _section_span(body, "Log")
        if span is None:
            body = body.rstrip("\n") + f"\n\n## Log\n{line}\n"
        else:
            _h, start, end = span
            existing = body[start:end].rstrip("\n")
            body = _replace_span(body, start, end, (existing + "\n" if existing else "") + line)
        changes = {"updated": _now()}
        if date >= str(fm.get(last_field, "") or ""):
            changes[last_field] = date
        return _patch_frontmatter(block, changes), body, None

    _mutate(path, root, f"log: {text[:80]}", edit)
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

    def edit(fm, block, body):
        span = _section_span(body, heading)
        if span is None:
            log_span = _section_span(body, "Log")
            at = log_span[0] if log_span else len(body)
            head = body[:at].rstrip("\n")
            body = head + f"\n\n## {heading}\n{text.strip()}\n\n" + body[at:]
        else:
            _h, start, end = span
            body = _replace_span(body, start, end, text.strip())
        changes: dict[str, Any] = {"updated": _now()}
        if agent:
            changes["reviewed"] = False
        return _patch_frontmatter(block, changes), body, None

    _mutate(path, root, f"{heading}: rewritten{' by agent' if agent else ''}", edit)
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


def _corpus_note(path: Path | str, root: Path) -> Path:
    """Resolve a logical (notes/...) or physical path, refusing anything outside the corpus."""
    path = Path(path)
    if not path.is_absolute():
        path = root / path.as_posix().removeprefix("notes/")
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"{path} is outside the ledger corpus {root}") from None
    if resolved.suffix != ".md" or not resolved.is_file():
        raise ValueError(f"{path} is not a note")
    return resolved


def accept(path: Path | str, notes_dir: Path | None = None) -> int:
    """Mark a note reviewed: drop ``reviewed: false`` and the ``(agent)`` log marks. Returns marks cleared."""
    root = _notes_dir(notes_dir)
    path = _corpus_note(path, root)

    def edit(fm, block, body):
        n = unreviewed_count(fm, body)
        if not n:
            return block, body, 0
        changes: dict[str, Any] = {"updated": _now()}
        if fm.get("source") == "assistant":
            changes["source"] = "user"
        body = re.sub(r"^(- \d{4}-\d{2}-\d{2}) \(agent\) ", r"\1 ", body, flags=re.M)
        return _patch_frontmatter(block, changes, drop=("reviewed",)), body, n

    return _mutate(path, root, "reviewed: agent content accepted", edit)


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
