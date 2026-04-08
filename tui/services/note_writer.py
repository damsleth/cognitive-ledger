"""Note writing service - handles frontmatter updates and timeline logging."""

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ledger.layout import timeline_path as ledger_timeline_path
from ..models.note import Note
from ledger.io import FileLock, safe_write_text, append_timeline_entry


class NoteWriter:
    """Writes changes to note files and timeline."""

    def __init__(self, root_dir: Path, ledger_notes_dir: Path):
        self.root_dir = root_dir
        self.ledger_notes_dir = ledger_notes_dir
        self.timeline_path = ledger_timeline_path(ledger_notes_dir)

    def update_frontmatter(self, note: Note, changes: dict) -> None:
        """Update frontmatter fields in a note file.

        Args:
            note: The note to update
            changes: Dict of field -> new_value
        """
        # Hold the lock for both read and write to prevent TOCTOU races
        # (e.g. concurrent $EDITOR saves overwritten by stale snapshot).
        with FileLock(note.path):
            content = note.path.read_text(encoding="utf-8")

            # Split into frontmatter and body using explicit boundary lines.
            # Avoid content.split('---') because markdown bodies often contain '---' horizontal rules.
            lines = content.splitlines(keepends=True)
            if not lines or lines[0].strip() != "---":
                raise ValueError("Note has no frontmatter")

            end_idx = None
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    end_idx = i
                    break
            if end_idx is None:
                raise ValueError("Malformed frontmatter")

            frontmatter_text = "".join(lines[1:end_idx])
            body = "".join(lines[end_idx + 1 :])

            # Parse existing frontmatter
            frontmatter = yaml.safe_load(frontmatter_text) or {}

            # Apply changes
            for key, value in changes.items():
                if hasattr(value, "value"):  # Enum
                    frontmatter[key] = value.value
                else:
                    frontmatter[key] = value

            # Always update timestamp
            now = datetime.now(timezone.utc)
            frontmatter["updated"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Rebuild frontmatter
            new_frontmatter = yaml.safe_dump(
                frontmatter,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

            if not new_frontmatter.endswith("\n"):
                new_frontmatter += "\n"

            # Write back (lock already held, skip double-locking)
            if body.startswith("\n") or body == "":
                new_content = f"---\n{new_frontmatter}---{body}"
            else:
                new_content = f"---\n{new_frontmatter}---\n{body}"

            safe_write_text(note.path, new_content, use_lock=False)

        # Log to timeline
        changed_fields = ", ".join(changes.keys())
        self.append_to_timeline("updated", note.path, f"changed {changed_fields}")

    def append_to_timeline(self, action: str, path: Path, description: str) -> None:
        """Append an entry to the timeline.

        Args:
            action: created|updated|archived|deleted|closed|sleep
            path: Path to the affected note (relative to root)
            description: Brief description of the change
        """
        append_timeline_entry(
            timeline_path=self.timeline_path,
            action=action,
            note_path=path,
            description=description,
            root_dir=self.root_dir,
            ledger_notes_dir=self.ledger_notes_dir,
        )

    def add_section(self, note: Note, section_name: str, content: str) -> None:
        """Add a new section to a note.

        Args:
            note: The note to update
            section_name: Name of the section (without ##)
            content: Section content
        """
        with FileLock(note.path):
            file_content = note.path.read_text(encoding="utf-8")

            # Add section before Links if it exists, otherwise at the end
            new_section = f"\n## {section_name}\n{content}\n"

            if "## Links" in file_content:
                file_content = file_content.replace("## Links", f"{new_section}## Links")
            else:
                file_content = file_content.rstrip() + new_section

            safe_write_text(note.path, file_content, use_lock=False)

        self.append_to_timeline("updated", note.path, f"added {section_name} section")

    def add_checkbox(self, note: Note, section_name: str, checkbox_text: str) -> None:
        """Add a checkbox item to a section.

        Args:
            note: The note to update
            section_name: Section to add to (e.g., "Next action")
            checkbox_text: Text for the checkbox
        """
        with FileLock(note.path):
            lines = note.path.read_text(encoding="utf-8").splitlines(keepends=True)

            # Find the section and add checkbox
            in_section = False
            insert_index = -1

            for i, line in enumerate(lines):
                if line.strip() == f"## {section_name}":
                    in_section = True
                    insert_index = i + 1
                elif in_section and line.startswith("## "):
                    break
                elif in_section:
                    insert_index = i + 1

            if insert_index > 0:
                lines.insert(insert_index, f"- [ ] {checkbox_text}\n")
                safe_write_text(note.path, "".join(lines), use_lock=False)

        self.append_to_timeline("updated", note.path, "added next action checkbox")
