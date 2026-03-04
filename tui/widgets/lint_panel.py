"""Lint warnings panel."""

from textual.widgets import Static
from textual.reactive import reactive

from ..models.note import Note, LintWarning
from ..models.enums import Source


class LintPanel(Static):
    """Displays lint warnings for the current note."""

    current_note: reactive[Note | None] = reactive(None)

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)

    def watch_current_note(self, note: Note | None) -> None:
        """Re-render when note changes."""
        self.update(self._build_content(note))

    def _build_content(self, note: Note | None) -> str:
        """Render lint warnings."""
        if note is None:
            return ""

        warnings = self._check_lint(note)
        if not warnings:
            return "[dim]No warnings[/dim]"

        lines = ["[bold yellow]LINT WARNINGS[/bold yellow]", ""]
        for warning in warnings:
            icon = "!" if warning.severity == "warning" else "X"
            color = "yellow" if warning.severity == "warning" else "red"
            lines.append(f"[{color}]{icon}[/{color}] {warning.message}")
            if warning.suggested_fix:
                fix = warning.suggested_fix.get("action", "")
                lines.append(f"  [dim]Fix: {fix}[/dim]")

        return "\n".join(lines)

    def _check_lint(self, note: Note) -> list[LintWarning]:
        """Check for common lint issues."""
        warnings = []
        fm = note.frontmatter

        # High confidence on inferred note
        if fm.source == Source.INFERRED and fm.confidence > 0.8:
            warnings.append(
                LintWarning(
                    code="HIGH_CONFIDENCE_INFERRED",
                    message="High confidence on inferred note",
                    severity="warning",
                    suggested_fix={
                        "field": "confidence",
                        "value": 0.6,
                        "action": "Lower confidence to < 0.7",
                    },
                )
            )

        # Open loop missing Next action section
        if note.note_type.value == "loop" and fm.status and fm.status.value == "open":
            sections = note.sections
            if "Next action" not in sections and "Next Action" not in sections:
                warnings.append(
                    LintWarning(
                        code="MISSING_NEXT_ACTION",
                        message="Open loop missing Next action section",
                        severity="warning",
                        suggested_fix={
                            "action": "Add ## Next action section with checkbox",
                        },
                    )
                )
            else:
                # Check for checkbox in Next action
                next_action = sections.get("Next action", sections.get("Next Action", ""))
                if "- [ ]" not in next_action and "- [x]" not in next_action:
                    warnings.append(
                        LintWarning(
                            code="MISSING_CHECKBOX",
                            message="Next action section missing checkbox",
                            severity="warning",
                            suggested_fix={
                                "action": "Add - [ ] checkbox item",
                            },
                        )
                    )

        # Placeholder links
        if "Links" in note.sections:
            links_section = note.sections["Links"]
            for line in links_section.splitlines():
                stripped = line.strip()
                if stripped == "-" or stripped == "- ":
                    warnings.append(
                        LintWarning(
                            code="PLACEHOLDER_LINK",
                            message="Placeholder bullet in Links section",
                            severity="warning",
                            suggested_fix={"action": "Remove empty bullet"},
                        )
                    )
                    break  # Only report once

        return warnings
