"""Note browsing helpers shared by CLI and other callers."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.parsing import (
    extract_title,
    first_checkbox,
    first_content_line,
    parse_frontmatter_text,
    parse_sections,
    shorten,
)


@dataclass
class BrowseItem:
    path: str
    frontmatter: dict[str, Any]
    body: str
    type: str
    title: str = ""
    statement: str = ""
    question: str = ""
    why: str = ""
    next_action: str = ""
    context: str = ""
    implications: str = ""
    links: str = ""


def _cfg():
    return get_config()


def _note_types() -> dict[str, dict[str, Any]]:
    config = _cfg()
    return {
        name: {
            "dir": config.notes_dir / info["dir"].removeprefix("notes/"),
            "label": info["label"],
        }
        for name, info in config.note_types.items()
    }


def _root_dir() -> Path:
    return _cfg().root_dir.resolve()


def _rel_display_path(path: str | Path) -> Path:
    return Path(path).resolve().relative_to(_root_dir())


def read_note(path: str | Path) -> tuple[dict[str, Any], str]:
    text = Path(path).read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter_text(text)
    return frontmatter, body.strip()


def loop_item(path: str | Path) -> BrowseItem:
    frontmatter, body = read_note(path)
    sections = parse_sections(body)

    question = "\n".join(sections.get("question or task", [])).strip()
    title = extract_title(body)
    if not question:
        question = title or Path(path).name.replace("loop__", "").replace("_", " ")

    next_action_text = "\n".join(sections.get("next action", [])).strip()
    if not next_action_text:
        next_action_text = body
    next_action = first_checkbox(next_action_text) or (
        next_action_text.splitlines()[0].strip() if next_action_text else ""
    )

    return BrowseItem(
        path=str(path),
        frontmatter=frontmatter,
        question=question,
        why="\n".join(sections.get("why it matters", [])).strip(),
        next_action=next_action,
        links="\n".join(sections.get("links", [])).strip(),
        title=title or "",
        body=body,
        type="loops",
    )


def generic_item(path: str | Path, note_type: str) -> BrowseItem:
    frontmatter, body = read_note(path)
    sections = parse_sections(body)
    title = extract_title(body) or Path(path).name.replace("_", " ")

    statement = "\n".join(sections.get("statement", [])).strip()
    if not statement:
        statement = first_content_line(body)

    return BrowseItem(
        path=str(path),
        frontmatter=frontmatter,
        title=title,
        statement=statement,
        context="\n".join(sections.get("context", [])).strip(),
        implications="\n".join(sections.get("implications", [])).strip(),
        links="\n".join(sections.get("links", [])).strip(),
        body=body,
        type=note_type,
    )


def sorted_items(note_type: str, loop_status: str | None = None) -> list[BrowseItem]:
    items: list[BrowseItem] = []
    note_types = _note_types()

    if note_type == "all":
        for current_type in note_types:
            items.extend(sorted_items(current_type))
        items.sort(key=lambda item: str(item.frontmatter.get("updated", "")), reverse=True)
        return items

    if note_type not in note_types:
        return items

    notes_dir = note_types[note_type]["dir"]
    if not notes_dir.is_dir():
        return items

    dated_items: list[tuple[str, BrowseItem]] = []
    for path in notes_dir.iterdir():
        if path.suffix != ".md":
            continue
        if note_type == "loops":
            item = loop_item(path)
            status = str(item.frontmatter.get("status", "open")).strip().lower()
            if loop_status and loop_status != "all" and status != loop_status:
                continue
        else:
            item = generic_item(path, note_type)
        dated_items.append((str(item.frontmatter.get("updated", "")), item))

    dated_items.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in dated_items]


def compact_line(
    item: BrowseItem,
    width: int,
    *,
    show_path: bool = False,
    prefix_type: bool = False,
) -> str:
    if item.type == "loops":
        line = compact_loop_line(item, width, show_path=show_path)
    else:
        line = compact_generic_line(item, width, show_path=show_path)

    if prefix_type:
        label = _note_types().get(item.type, {}).get("label", item.type)
        line = f"{label} | {line}"
    return line


def compact_loop_line(item: BrowseItem, width: int, *, show_path: bool = False) -> str:
    fm = item.frontmatter
    status = fm.get("status", "?")
    updated = str(fm.get("updated", "") or "")
    updated_short = updated.split("T")[0] if updated else "unknown"
    question = item.question or "untitled"
    next_action = item.next_action or "none"

    conf = ""
    if "confidence" in fm:
        try:
            conf_val = float(fm.get("confidence", "1"))
            if conf_val < 0.7:
                conf = f" conf {conf_val:.2g}"
        except ValueError:
            pass

    base = f"{status} | {question} - next: {next_action} (updated {updated_short}{conf})"
    if show_path:
        base = f"{base} [{_rel_display_path(item.path)}]"
    return shorten(base, width)


def compact_generic_line(item: BrowseItem, width: int, *, show_path: bool = False) -> str:
    fm = item.frontmatter
    updated = str(fm.get("updated", "") or "")
    updated_short = updated.split("T")[0] if updated else "unknown"
    title = item.title or "untitled"
    statement = item.statement or "no summary"

    conf = ""
    if "confidence" in fm:
        try:
            conf_val = float(fm.get("confidence", "1"))
            if conf_val < 0.7:
                conf = f" conf {conf_val:.2g}"
        except ValueError:
            pass

    base = f"{title} - {statement} (updated {updated_short}{conf})"
    if show_path:
        base = f"{base} [{_rel_display_path(item.path)}]"
    return shorten(base, width)


def format_detail(item: BrowseItem, width: int) -> list[str]:
    fm = item.frontmatter
    lines = [
        f"Path: {_rel_display_path(item.path)}",
        f"Updated: {fm.get('updated', 'unknown')}",
    ]
    conf = fm.get("confidence", "unknown")
    lines.append(f"Confidence: {conf} (source: {fm.get('source', 'unknown')})")
    scope = fm.get("scope", "unknown")
    tags = fm.get("tags", [])
    tags_text = ", ".join(tags) if isinstance(tags, list) else str(tags)
    lines.append(f"Scope: {scope} (tags: {tags_text or 'none'})")

    def add_section(title: str, text: str) -> None:
        if not text:
            return
        lines.append("")
        lines.append(f"{title}:")
        wrapped = textwrap.wrap(text, width=max(10, width - 2)) or [""]
        lines.extend([f"  {line}" for line in wrapped])

    if item.type == "loops":
        lines.append(f"Status: {fm.get('status', '?')}")
        add_section("Question or task", item.question)
        add_section("Why it matters", item.why)
        add_section("Next action", item.next_action)
        add_section("Links", item.links)
    else:
        add_section("Statement", item.statement)
        add_section("Context", item.context)
        add_section("Implications", item.implications)
        add_section("Links", item.links)
    return lines
