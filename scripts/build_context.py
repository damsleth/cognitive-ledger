#!/usr/bin/env python3
import argparse
import csv
import os
import re
import sys
import textwrap
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent


ACTIVE_LOOP_STATUSES = {"open", "blocked", "snoozed"}


def maybe_reexec_in_repo_venv():
    if os.environ.get("COG_LEDGER_VENV_REEXEC") == "1":
        return
    venv_dir = ROOT_DIR / ".venv"
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.is_file():
        return
    try:
        in_target_venv = Path(sys.prefix).resolve() == venv_dir.resolve()
    except Exception:
        return
    if in_target_venv:
        return
    env = os.environ.copy()
    env["COG_LEDGER_VENV_REEXEC"] = "1"
    env["VIRTUAL_ENV"] = str(venv_dir.resolve())
    env["PATH"] = f"{venv_dir / 'bin'}:{env.get('PATH', '')}"
    os.execve(
        str(venv_python),
        [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        env,
    )


def strip_quotes(value):
    if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
        return value[1:-1]
    return value


def strip_inline_comment(value):
    in_single = False
    in_double = False
    escaped = False

    for idx, ch in enumerate(value):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            return value[:idx].rstrip()
    return value


def parse_inline_list(value):
    inner = value[1:-1].strip()
    if not inner:
        return []
    reader = csv.reader([inner], skipinitialspace=True)
    return [strip_quotes(item.strip()) for item in next(reader) if item.strip()]


def parse_scalar(value):
    cleaned = strip_inline_comment(value).strip()
    if not cleaned:
        return ""
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return parse_inline_list(cleaned)
    return strip_quotes(cleaned)


def parse_frontmatter(lines):
    data = {}
    current_list_key = None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        list_item_match = re.match(r"^\s*-\s+(.*)$", line)
        if list_item_match and current_list_key is not None:
            item = parse_scalar(list_item_match.group(1))
            if item != "":
                data[current_list_key].append(item)
            continue

        key_match = re.match(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$", stripped)
        if not key_match:
            current_list_key = None
            continue

        key, value = key_match.group(1), key_match.group(2)
        if value is None or value == "":
            data[key] = []
            current_list_key = key
            continue

        data[key] = parse_scalar(value)
        current_list_key = None

    return data


def read_note(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    frontmatter = {}
    body_lines = lines

    if lines and lines[0].strip() == "---":
        fm_lines = []
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                body_lines = lines[idx + 1 :]
                break
            fm_lines.append(lines[idx])
        frontmatter = parse_frontmatter(fm_lines)

    return frontmatter, "\n".join(body_lines).strip()


def normalize_section_name(name):
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    aliases = {
        "next actions": "next action",
        "next steps": "next action",
        "next step": "next action",
        "question task": "question or task",
        "why matters": "why it matters",
    }
    return aliases.get(normalized, normalized)


def parse_sections(body):
    sections = {}
    current = None
    for line in body.splitlines():
        if line.startswith("## "):
            current = normalize_section_name(line[3:].strip())
            sections[current] = []
            continue
        if line.startswith("### "):
            current = normalize_section_name(line[4:].strip())
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def extract_title(body, fallback):
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            if title.lower().startswith("loop:"):
                title = title[5:].strip()
            return title
    return fallback


def first_content_line(body):
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            return stripped[2:].strip()
        return stripped
    return ""


def first_checkbox(text):
    for line in text.splitlines():
        match = re.match(r"\s*-\s*\[[ xX]\]\s+(.*)", line)
        if match:
            return match.group(1).strip()
    return ""


def shorten(text, width=160):
    return textwrap.shorten(" ".join(text.split()), width=width, placeholder="...")


def updated_sort(items):
    items.sort(key=lambda x: x["path"])
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


def load_note_items(folder):
    items = []
    for path in sorted(folder.glob("*.md")):
        fm, body = read_note(path)
        sections = parse_sections(body)
        fallback_title = path.stem.replace("_", " ")
        title = extract_title(body, fallback_title)
        statement = "\n".join(sections.get("statement", [])).strip() or first_content_line(body)
        items.append(
            {
                "path": path,
                "updated": str(fm.get("updated", "")),
                "status": str(fm.get("status", "")).strip().lower(),
                "title": title,
                "statement": statement,
                "sections": sections,
                "body": body,
            }
        )
    return updated_sort(items)


def render_list(lines, fallback_text):
    if not lines:
        return [f"- {fallback_text}"]
    return lines


def build_context(notes_dir):
    facts = load_note_items(notes_dir / "02_facts")
    prefs = load_note_items(notes_dir / "03_preferences")
    goals = load_note_items(notes_dir / "04_goals")
    loops = load_note_items(notes_dir / "05_open_loops")

    active_loops = []
    for item in loops:
        status = item["status"] or "open"
        if status in ACTIVE_LOOP_STATUSES:
            active_loops.append(item)

    all_updated = [item["updated"] for item in facts + prefs + goals + loops if item["updated"]]
    latest_update = max(all_updated) if all_updated else "unknown"

    fact_lines = []
    for item in facts[:6]:
        summary = item["statement"] or item["title"]
        fact_lines.append(f"- `{item['path'].name}` - {shorten(summary)}")

    pref_lines = []
    for item in prefs[:8]:
        summary = item["statement"] or item["title"]
        pref_lines.append(f"- `{item['path'].name}` - {shorten(summary)}")

    loop_lines = []
    for item in active_loops[:12]:
        status = item["status"] or "open"
        question = "\n".join(item["sections"].get("question or task", [])).strip() or item["title"]
        next_action_text = "\n".join(item["sections"].get("next action", [])).strip() or item["body"]
        next_action = first_checkbox(next_action_text)
        if not next_action:
            next_action = "next action not set"
        loop_lines.append(
            f"- `{item['path'].name}` ({status}) - {shorten(question, width=120)}; next: {shorten(next_action, width=100)}"
        )

    output = [
        "# Ledger Context (Boot Summary)",
        "",
        "Auto-generated by `./scripts/sheep index`. Edit source notes; do not edit this file directly.",
        "",
        "## Snapshot",
        "",
        f"- Facts: {len(facts)}",
        f"- Preferences: {len(prefs)}",
        f"- Goals: {len(goals)}",
        f"- Active loops: {len(active_loops)}",
        f"- Latest source update: {latest_update}",
        "",
        "## Key Facts",
        "",
        *render_list(fact_lines, "No facts available."),
        "",
        "## Key Preferences",
        "",
        *render_list(pref_lines, "No preferences available."),
        "",
        "## Active Open Loops",
        "",
        *render_list(loop_lines, "No active loops."),
        "",
        "## When to Search Deeper",
        "",
        "Before responding about:",
        "- Personal details, history, family -> search `notes/02_facts/`",
        "- Past decisions or commitments -> search `notes/02_facts/` and `notes/08_indices/timeline.md`",
        "- User preferences or style -> search `notes/03_preferences/`",
        "- Ongoing threads or open questions -> search `notes/05_open_loops/`",
        "- Defined concepts or frameworks -> search `notes/06_concepts/`",
        "",
        "Rule: if about to guess or assume something about the user, search first.",
        "",
    ]
    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description="Generate deterministic boot context index from ledger notes")
    parser.add_argument("--notes-dir", required=True, help="Path to notes directory")
    parser.add_argument("--output", required=True, help="Path to output markdown file")
    args = parser.parse_args()

    notes_dir = Path(args.notes_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    content = build_context(notes_dir)
    output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    maybe_reexec_in_repo_venv()
    main()
