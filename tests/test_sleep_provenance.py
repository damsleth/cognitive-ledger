"""Tests for `ledger sleep provenance` (plan 42 deferred item 8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.config import LedgerConfig, set_config, reset_config
from ledger.maintenance import cmd_provenance
from ledger.parsing.frontmatter import parse_frontmatter_text
from ledger.signals import append_signal


@pytest.fixture
def ledger(tmp_path):
    notes = tmp_path / "notes"
    facts = notes / "02_facts"
    facts.mkdir(parents=True)

    def _note(name: str, provenance: str = "") -> Path:
        prov_line = f"provenance: {provenance}\n" if provenance else ""
        (facts / name).write_text(
            "---\ncreated: 2026-06-01T00:00:00Z\nupdated: 2026-06-01T00:00:00Z\n"
            f"tags: [x]\nconfidence: 0.9\nsource: user\nscope: all\n{prov_line}---\n"
            "# T\n\n## Statement\n\nbody\n",
            encoding="utf-8",
        )
        return facts / name

    _note("fact__corrected.md")
    _note("fact__explicit.md", provenance="validated")
    _note("fact__clean.md")

    cfg = LedgerConfig(ledger_root=tmp_path, ledger_notes_dir=notes)
    set_config(cfg)
    # Real correction signal on the first note; affirmation (not correction) on clean.
    append_signal("correction", note="notes/02_facts/fact__corrected.md", detail="fixed")
    append_signal("correction", note="notes/02_facts/fact__explicit.md", detail="fixed")
    append_signal("affirmation", note="notes/02_facts/fact__clean.md")
    yield cfg
    reset_config()


def _provenance_of(cfg, name) -> str:
    text = (cfg.ledger_notes_dir / "02_facts" / name).read_text(encoding="utf-8")
    fm, _ = parse_frontmatter_text(text)
    return str(fm.get("provenance", "") or "")


class TestProvenanceStamp:
    def test_check_does_not_write(self, ledger, capsys):
        rc = cmd_provenance(apply=False)
        assert rc == 0
        assert _provenance_of(ledger, "fact__corrected.md") == ""  # unchanged
        out = capsys.readouterr().out
        assert "Would stamp 1" in out
        assert "fact__corrected.md" in out

    def test_apply_stamps_corrected(self, ledger):
        cmd_provenance(apply=True)
        assert _provenance_of(ledger, "fact__corrected.md") == "corrected"

    def test_explicit_provenance_left_alone(self, ledger):
        cmd_provenance(apply=True)
        assert _provenance_of(ledger, "fact__explicit.md") == "validated"

    def test_note_without_correction_untouched(self, ledger):
        cmd_provenance(apply=True)
        assert _provenance_of(ledger, "fact__clean.md") == ""

    def test_idempotent(self, ledger):
        cmd_provenance(apply=True)
        # second run: already corrected → nothing more to change
        rc = cmd_provenance(apply=False, as_json=True)
        assert rc == 0

    def test_json_output(self, ledger, capsys):
        import json
        cmd_provenance(apply=False, as_json=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["count"] == 1
        assert "notes/02_facts/fact__corrected.md" in payload["changed"]
        assert "notes/02_facts/fact__explicit.md" in payload["skipped_explicit"]
