from __future__ import annotations

import json
from pathlib import Path

from ledger.config import LedgerConfig, set_config, reset_config
from ledger import maintenance


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_temp_config(tmp_path: Path) -> LedgerConfig:
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    return config


def test_status_handles_missing_timeline(tmp_path, capsys):
    _make_temp_config(tmp_path)
    try:
        rc = maintenance.cmd_status()
    finally:
        reset_config()

    out = capsys.readouterr().out
    assert rc == 0
    assert "Timeline not found" in out


def test_lint_allows_lang_no(tmp_path):
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__lang_no.md"
        _write(
            note,
            """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: personal
lang: no
---

# Fakt

## Statement

En testnotat.
""",
        )
        _write(
            config.timeline_path,
            """# Timeline

---
2026-02-01T00:00:00Z | created | notes/02_facts/fact__lang_no.md | seed
""",
        )

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 0


def test_lint_fails_missing_frontmatter(tmp_path):
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__bad.md"
        _write(note, "# Missing frontmatter\n")
        _write(config.timeline_path, "# Timeline\n")

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 1


def test_alias_suggestions_from_tag_cooccurrence(tmp_path):
    config = _make_temp_config(tmp_path)
    try:
        note_one = config.ledger_notes_dir / "02_facts" / "fact__one.md"
        note_two = config.ledger_notes_dir / "02_facts" / "fact__two.md"
        for path, title in [(note_one, "Commute Planning"), (note_two, "Commute Scheduling")]:
            _write(
                path,
                f"""---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [commute, calendar]
confidence: 0.9
source: user
scope: personal
lang: en
---

# {title}

## Statement

Commute calendar planning details.
""",
            )

        indices_dir = config.ledger_notes_dir / "08_indices"
        indices_dir.mkdir(parents=True, exist_ok=True)
        maintenance._generate_alias_suggestions(indices_dir)

        data = json.loads((indices_dir / "aliases_suggested.json").read_text(encoding="utf-8"))
        assert "commute" in data
        assert "calendar" in data["commute"]
    finally:
        reset_config()


def test_sync_reports_missing_state(tmp_path, capsys):
    _make_temp_config(tmp_path)
    try:
        rc = maintenance.cmd_sync(apply=False)
    finally:
        reset_config()

    out = capsys.readouterr().out
    assert rc == 1
    assert "State not found." in out


def test_sync_apply_then_check_healthy(tmp_path, capsys):
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__sync.md"
        _write(
            note,
            """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [sync]
confidence: 0.9
source: user
scope: personal
lang: en
---

# Sync Fact

## Statement

Seed.
""",
        )
        _write(config.timeline_path, "# Timeline\n\n---\n")

        apply_rc = maintenance.cmd_sync(apply=True)
        capsys.readouterr()  # clear apply output
        check_rc = maintenance.cmd_sync(apply=False)
    finally:
        reset_config()

    out = capsys.readouterr().out
    assert apply_rc == 0
    assert check_rc == 0
    assert "-> Sync healthy" in out


def test_sync_detects_unlogged_note_change(tmp_path, capsys):
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__sync_drift.md"
        _write(
            note,
            """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [sync]
confidence: 0.9
source: user
scope: personal
lang: en
---

# Drift Fact

## Statement

Before.
""",
        )
        _write(config.timeline_path, "# Timeline\n\n---\n")
        maintenance.cmd_sync(apply=True)

        _write(
            note,
            """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-02T00:00:00Z
tags: [sync]
confidence: 0.9
source: user
scope: personal
lang: en
---

# Drift Fact

## Statement

After.
""",
        )

        check_rc = maintenance.cmd_sync(apply=False)
    finally:
        reset_config()

    out = capsys.readouterr().out
    assert check_rc == 1
    assert "Unlogged note changes: 1" in out
