"""Shared pytest fixtures for ledger tests."""

from __future__ import annotations

# Path setup - MUST happen at top before any project imports
import sys
from pathlib import Path as _Path
from pathlib import Path  # Keep plain Path for type hints

_ROOT_DIR = _Path(__file__).resolve().parents[1]
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

# Now other imports are safe
import os
import tempfile
from datetime import datetime, timezone
from typing import Generator
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_notes_dir() -> Generator[Path, None, None]:
    """Create a temporary notes directory structure for testing.

    Yields:
        Path to the temporary notes directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        notes_dir = Path(tmpdir) / "notes"
        # Create standard ledger folder structure
        folders = [
            "00_inbox",
            "01_identity",
            "02_facts",
            "03_preferences",
            "04_goals",
            "05_open_loops",
            "06_concepts",
            "07_projects",
            "08_indices",
            "09_archive",
        ]
        for folder in folders:
            (notes_dir / folder).mkdir(parents=True)

        yield notes_dir


@pytest.fixture
def sample_fact_note(temp_notes_dir: Path) -> Path:
    """Create a sample fact note for testing.

    Returns:
        Path to the created note file.
    """
    content = """---
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

This is a test fact note.

## Context

Created for unit testing purposes.

## Links

- [[related_note]]
"""
    note_path = temp_notes_dir / "02_facts" / "fact__sample.md"
    note_path.write_text(content)
    return note_path


@pytest.fixture
def sample_loop_note(temp_notes_dir: Path) -> Path:
    """Create a sample open loop note for testing.

    Returns:
        Path to the created note file.
    """
    content = """---
created: 2026-02-13T10:00:00Z
updated: 2026-02-13T10:00:00Z
tags: [test, loop]
confidence: 0.8
source: user
scope: dev
lang: en
status: open
---

# Loop: Test Open Loop

## Question or Task

What should we test next?

## Context

This is a test loop for unit testing.

## Next Action

- [ ] Write more tests
- [ ] Review results

## Exit Condition

All tests pass.
"""
    note_path = temp_notes_dir / "05_open_loops" / "loop__test.md"
    note_path.write_text(content)
    return note_path


@pytest.fixture
def sample_frontmatter_text() -> str:
    """Return sample YAML frontmatter text."""
    return """---
created: 2026-02-13T10:00:00Z
updated: 2026-02-13T10:00:00Z
tags: [alpha, beta, gamma]
confidence: 0.85
source: user
scope: dev
lang: en
---

# Title

Body content here.
"""


@pytest.fixture
def iso_timestamp() -> str:
    """Return a fixed ISO timestamp for testing."""
    return "2026-02-13T10:00:00Z"


@pytest.fixture
def mock_now(iso_timestamp: str) -> Generator[None, None, None]:
    """Mock datetime.now() to return a fixed time.

    Useful for testing timestamp generation.
    """
    fixed_dt = datetime(2026, 2, 13, 10, 0, 0, tzinfo=timezone.utc)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_dt
        mock_dt.utcnow.return_value = fixed_dt
        mock_dt.fromisoformat = datetime.fromisoformat
        yield


@pytest.fixture
def mock_env_vars() -> Generator[None, None, None]:
    """Clear ledger-related environment variables for clean tests."""
    env_vars = [
        "LEDGER_NOTES_DIR",
        "LEDGER_TIMELINE_PATH",
        "LEDGER_TEMPLATE_DIR",
        "LEDGER_DEFAULT_LANG",
        "LEDGER_DEFAULT_SCOPE",
        "LEDGER_STOPWORDS",
    ]
    original = {k: os.environ.get(k) for k in env_vars}

    # Clear all
    for k in env_vars:
        if k in os.environ:
            del os.environ[k]

    yield

    # Restore
    for k, v in original.items():
        if v is not None:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]
