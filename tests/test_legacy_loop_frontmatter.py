"""Loops written by the removed Things3 sync still carry `things_uuid` /
`things_list_id` frontmatter. Those must stay harmless unknown keys."""

from __future__ import annotations

from pathlib import Path

from ledger import maintenance
from ledger.config import LedgerConfig, reset_config, set_config
from ledger.notes import get_notes

_LOOP = """\
---
created: 2026-01-01T00:00:00Z
updated: 2026-01-01T00:00:00Z
tags: [open_loop]
confidence: 0.8
source: user
status: open
scope: dev
lang: en
{extra}---

# Loop: Legacy synced loop

## Question or task

Does a leftover key break anything?

## Next action
- [ ] Nothing.
"""

_EXTRA = "things_uuid: AAAAAAAAAAAAAAAAAAAAAA\nthings_list_id: BBBBBBBBBBBBBBBBBBBBBB\n"


def _lint(tmp_path: Path, extra: str, capsys) -> tuple[int, str]:
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    try:
        note = config.ledger_notes_dir / "05_open_loops" / "loop__legacy.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(_LOOP.format(extra=extra), encoding="utf-8")
        config.timeline_path.parent.mkdir(parents=True, exist_ok=True)
        config.timeline_path.write_text("# Timeline\n", encoding="utf-8")
        capsys.readouterr()
        rc = maintenance.cmd_lint()
        out = capsys.readouterr()
        loops = get_notes("loops", loop_status="open", notes_dir=config.ledger_notes_dir)
        assert [n.status for n in loops] == ["open"]
        return rc, out.out + out.err
    finally:
        reset_config()


def test_things_keys_are_harmless_unknown_keys(tmp_path, capsys):
    base_rc, base_out = _lint(tmp_path / "a", "", capsys)
    rc, out = _lint(tmp_path / "b", _EXTRA, capsys)
    assert rc == base_rc == 0
    assert "things" not in out.lower()
    assert out.replace(str(tmp_path / "b"), "") == base_out.replace(str(tmp_path / "a"), "")
