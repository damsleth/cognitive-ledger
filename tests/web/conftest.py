"""Shared fixtures for tests/web.

Builds a real ``LedgerConfig`` rooted at a temporary directory, installs
it as the global config, and yields a FastAPI ``TestClient`` against the
factory-built app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.web.server import create_app


SAMPLE_FACT = """---
created: 2026-02-13T10:00:00Z
updated: 2026-04-01T10:00:00Z
tags: [test, web, alpha]
confidence: 0.9
source: user
scope: dev
lang: en
---

# Sample Fact

## Statement

This is a test fact with a wikilink to [[fact__other_one]] and a
broken one to [[fact__does_not_exist]].

## Context

Created for web UI tests.
"""

SAMPLE_OTHER_FACT = """---
created: 2026-02-13T10:00:00Z
updated: 2026-03-15T10:00:00Z
tags: [test]
confidence: 0.8
source: user
scope: dev
lang: en
---

# Other Fact

## Statement

A second fact note used as a wikilink target.
"""

SAMPLE_LOOP_OPEN = """---
created: 2026-02-13T10:00:00Z
updated: 2026-04-10T10:00:00Z
tags: [test, loop]
confidence: 0.8
source: user
scope: dev
lang: en
status: open
---

# Test Open Loop

## Question or Task

What should we test next?

## Next Action

- [ ] Write more tests
"""

SAMPLE_LOOP_CLOSED = """---
created: 2026-02-13T10:00:00Z
updated: 2026-04-09T10:00:00Z
tags: [test, loop]
confidence: 0.8
source: user
scope: dev
lang: en
status: closed
---

# Closed Loop

## Question or Task

This is resolved.

## Next Action

- [x] Already done
"""


@pytest.fixture
def web_ledger_root(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary ledger root and install a config pointing at it."""
    ledger_root = tmp_path / "ledger"
    notes_dir = ledger_root / "notes"
    for sub in (
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
    ):
        (notes_dir / sub).mkdir(parents=True)

    (notes_dir / "02_facts" / "fact__sample.md").write_text(SAMPLE_FACT)
    (notes_dir / "02_facts" / "fact__other_one.md").write_text(SAMPLE_OTHER_FACT)
    (notes_dir / "05_open_loops" / "loop__open_test.md").write_text(SAMPLE_LOOP_OPEN)
    (notes_dir / "05_open_loops" / "loop__closed_test.md").write_text(SAMPLE_LOOP_CLOSED)

    reset_config()
    cfg = LedgerConfig(ledger_root=ledger_root, ledger_notes_dir=notes_dir)
    set_config(cfg)

    yield ledger_root

    reset_config()


@pytest.fixture
def client(web_ledger_root: Path) -> Generator[TestClient, None, None]:
    """FastAPI TestClient over the Phase 1 app."""
    app = create_app()
    with TestClient(app) as c:
        yield c
