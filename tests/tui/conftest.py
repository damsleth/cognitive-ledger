"""TUI-specific pytest fixtures."""
from __future__ import annotations

# Path setup MUST happen FIRST before any project imports
import sys as _sys
from pathlib import Path as _Path
from pathlib import Path

_ROOT = _Path(__file__).resolve().parents[2]
_sys.path.insert(0, str(_ROOT))

# Now we can safely import
import tempfile
from datetime import datetime, timezone
from typing import Generator

import pytest

from tui.models.enums import NoteType, Source, Scope, LoopStatus
from tui.models.note import Frontmatter, Note, NoteLink


@pytest.fixture
def temp_ledger_root() -> Generator[Path, None, None]:
    """Create a temporary ledger root directory with proper structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create note folders
        for note_type in NoteType:
            (root / note_type.folder).mkdir(parents=True)

        # Create indices folder
        (root / "notes" / "08_indices").mkdir(parents=True)

        # Create timeline file
        timeline = root / "notes" / "08_indices" / "timeline.md"
        timeline.write_text("# Timeline\n")

        yield root


@pytest.fixture
def sample_fact_note_text() -> str:
    """Sample fact note markdown."""
    return """---
created: 2026-02-13T10:00:00Z
updated: 2026-02-13T10:00:00Z
tags: [test, sample]
confidence: 0.9
source: user
scope: dev
lang: en
---

# Sample Fact

## Statement

This is a test fact.

## Context

Created for unit testing.

## Links

- [[related_note]]
"""


@pytest.fixture
def sample_loop_note_text() -> str:
    """Sample loop note markdown."""
    return """---
created: 2026-02-13T10:00:00Z
updated: 2026-02-13T10:00:00Z
tags: [test, loop]
confidence: 0.8
source: user
scope: dev
lang: en
status: open
---

# Loop: Test Loop

## Question or Task

What should we test?

## Why it matters

Testing is important.

## Next action

- [ ] Write first test
- [ ] Review results

## Exit Condition

All tests pass.

## Links

- [[test_framework]]
"""


@pytest.fixture
def sample_frontmatter() -> Frontmatter:
    """Sample Frontmatter object."""
    return Frontmatter(
        created=datetime(2026, 2, 13, 10, 0, 0, tzinfo=timezone.utc),
        updated=datetime(2026, 2, 13, 10, 0, 0, tzinfo=timezone.utc),
        tags=["test", "sample"],
        confidence=0.9,
        source=Source.USER,
        scope=Scope.DEV,
        lang="en",
    )


@pytest.fixture
def sample_note(temp_ledger_root: Path, sample_frontmatter: Frontmatter) -> Note:
    """Create a sample Note object."""
    path = temp_ledger_root / "notes" / "02_facts" / "fact__test.md"
    return Note(
        path=path,
        note_type=NoteType.FACT,
        frontmatter=sample_frontmatter,
        title="Test Fact",
        body="## Statement\n\nThis is a test.",
    )


@pytest.fixture
def populated_ledger(temp_ledger_root: Path) -> Path:
    """Create a ledger with sample notes for testing."""
    # Create sample fact
    fact_path = temp_ledger_root / "notes" / "02_facts" / "fact__sample.md"
    fact_path.write_text("""---
created: 2026-02-13T10:00:00Z
updated: 2026-02-13T10:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
---

# Sample Fact

## Statement

A sample fact for testing.
""")

    # Create sample loop
    loop_path = temp_ledger_root / "notes" / "05_open_loops" / "loop__sample.md"
    loop_path.write_text("""---
created: 2026-02-13T09:00:00Z
updated: 2026-02-13T09:00:00Z
tags: [test, loop]
confidence: 0.8
source: user
scope: dev
lang: en
status: open
---

# Loop: Sample Loop

## Question or Task

What should we do?

## Next action

- [ ] First action
""")

    # Create another fact with different scope
    fact2_path = temp_ledger_root / "notes" / "02_facts" / "fact__work_item.md"
    fact2_path.write_text("""---
created: 2026-02-12T10:00:00Z
updated: 2026-02-12T10:00:00Z
tags: [work]
confidence: 0.85
source: user
scope: work
lang: en
---

# Work Item Fact

## Statement

A work-related fact.
""")

    return temp_ledger_root
