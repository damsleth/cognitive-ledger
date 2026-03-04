#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import textwrap
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent


SCOPES = ("personal", "work", "dev")
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


def parse_ts(value):
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def canonical_scope(scope):
    s = str(scope or "").strip().lower()
    if s == "life":
        return "personal"
    return s


def source_weight(source):
    src = str(source or "").strip().lower()
    if src in {"user", "tool"}:
        return 1.0
    if src == "assistant":
        return 0.7
    if src == "inferred":
        return 0.6
    return 0.5


def recency_score(updated_ts, now_dt):
    if not updated_ts:
        return 0.0
    age_days = max(0.0, (now_dt - updated_ts).total_seconds() / 86400.0)
    return max(0.0, 1.0 - (age_days / 90.0))


def note_score(item, now_dt):
    recency = recency_score(item.get("updated_ts"), now_dt)
    confidence = item.get("confidence", 0.0)
    src_weight = source_weight(item.get("source"))
    return (0.55 * recency) + (0.25 * confidence) + (0.20 * src_weight)


def collect_notes(notes_dir):
    now_dt = dt.datetime.now(tz=dt.timezone.utc)
    rows = []

    type_map = {
        "fact": notes_dir / "02_facts",
        "preference": notes_dir / "03_preferences",
        "goal": notes_dir / "04_goals",
        "loop": notes_dir / "05_open_loops",
    }

    for note_type, folder in type_map.items():
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            fm, body = read_note(path)
            sections = parse_sections(body)
            title = extract_title(body, path.stem.replace("_", " "))

            if note_type == "loop":
                summary = "\n".join(sections.get("question or task", [])).strip() or first_content_line(body)
                status = str(fm.get("status", "open")).strip().lower() or "open"
                next_action_text = "\n".join(sections.get("next action", [])).strip() or body
                next_action = first_checkbox(next_action_text)
            else:
                summary = "\n".join(sections.get("statement", [])).strip() or first_content_line(body)
                status = ""
                next_action = ""

            try:
                confidence = float(fm.get("confidence", 0.0))
            except Exception:
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))

            updated = str(fm.get("updated", "")).strip()
            item = {
                "path": str(path),
                "name": path.name,
                "type": note_type,
                "title": title,
                "summary": summary,
                "updated": updated,
                "updated_ts": parse_ts(updated),
                "confidence": confidence,
                "source": str(fm.get("source", "")).strip().lower(),
                "scope": canonical_scope(fm.get("scope", "")),
                "status": status,
                "next_action": next_action,
            }
            item["score"] = note_score(item, now_dt)
            rows.append(item)

    return rows


def shorten(text, width=160):
    return textwrap.shorten(" ".join((text or "").split()), width=width, placeholder="...")


def render_profile(scope, items):
    scope_items = [i for i in items if i.get("scope") == scope]

    facts = sorted([i for i in scope_items if i["type"] == "fact"], key=lambda x: x["score"], reverse=True)[:6]
    preferences = sorted([i for i in scope_items if i["type"] == "preference"], key=lambda x: x["score"], reverse=True)[:8]
    loops = sorted(
        [
            i
            for i in scope_items
            if i["type"] == "loop" and i.get("status") in ACTIVE_LOOP_STATUSES and i.get("next_action")
        ],
        key=lambda x: x["score"],
        reverse=True,
    )[:10]

    md = []
    md.append(f"# Context Profile ({scope})")
    md.append("")
    md.append("Auto-generated by `./scripts/sheep index`. Edit source notes; do not edit this file directly.")
    md.append("")
    md.append("## Snapshot")
    md.append("")
    md.append(f"- Scope: {scope}")
    md.append(f"- Facts: {len(facts)}")
    md.append(f"- Preferences: {len(preferences)}")
    md.append(f"- Active loops with next action: {len(loops)}")
    md.append("")

    md.append("## Top Facts")
    md.append("")
    if facts:
        for item in facts:
            md.append(f"- `{item['name']}` - {shorten(item['summary'])}")
    else:
        md.append("- No scope-matching facts found.")
    md.append("")

    md.append("## Top Preferences")
    md.append("")
    if preferences:
        for item in preferences:
            md.append(f"- `{item['name']}` - {shorten(item['summary'])}")
    else:
        md.append("- No scope-matching preferences found.")
    md.append("")

    md.append("## Active Open Loops")
    md.append("")
    if loops:
        for item in loops:
            md.append(
                f"- `{item['name']}` ({item['status']}) - {shorten(item['summary'], 120)}; next: {shorten(item['next_action'], 100)}"
            )
    else:
        md.append("- No active scope-matching loops with explicit next action.")
    md.append("")

    md.append("## When to Search Deeper")
    md.append("")
    md.append("Before responding:")
    md.append(f"- Start with this profile: `notes/08_indices/context_profile_{scope}.md`")
    md.append("- Expand to canonical notes via `./scripts/ledger query \"<topic>\" --scope <scope>`")
    md.append("- Verify critical claims in source notes before committing to an answer")
    md.append("")

    profile_json = {
        "scope": scope,
        "facts": [
            {
                "path": item["path"],
                "title": item["title"],
                "summary": item["summary"],
                "score": round(item["score"], 6),
                "updated": item["updated"],
                "confidence": item["confidence"],
                "source": item["source"],
            }
            for item in facts
        ],
        "preferences": [
            {
                "path": item["path"],
                "title": item["title"],
                "summary": item["summary"],
                "score": round(item["score"], 6),
                "updated": item["updated"],
                "confidence": item["confidence"],
                "source": item["source"],
            }
            for item in preferences
        ],
        "active_loops": [
            {
                "path": item["path"],
                "title": item["title"],
                "summary": item["summary"],
                "status": item["status"],
                "next_action": item["next_action"],
                "score": round(item["score"], 6),
                "updated": item["updated"],
                "confidence": item["confidence"],
                "source": item["source"],
            }
            for item in loops
        ],
    }

    return "\n".join(md) + "\n", profile_json


def main():
    parser = argparse.ArgumentParser(description="Generate scoped context profiles from ledger notes")
    parser.add_argument("--notes-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    notes_dir = Path(args.notes_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    items = collect_notes(notes_dir)
    for scope in SCOPES:
        md, payload = render_profile(scope, items)
        (output_dir / f"context_profile_{scope}.md").write_text(md, encoding="utf-8")
        (output_dir / f"context_profile_{scope}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    maybe_reexec_in_repo_venv()
    main()
