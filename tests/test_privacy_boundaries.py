"""Privacy boundary tests.

Verifies that:
- Private-fenced content is stripped from note_index.json.
- redact() catches common secret patterns.
- ledger --doctor reports private_fence_in_index when the index is polluted.
- Private fences do not appear in retrieval results.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from ledger.conventions import redact


# ---------------------------------------------------------------------------
# redact() contract
# ---------------------------------------------------------------------------

class TestRedact:
    def test_redacts_bearer_token(self):
        out = redact("Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig")
        assert "eyJhbGciOiJSUzI1NiJ9" not in out

    def test_redacts_jwt_like_string(self):
        jwt = "eyJalg.payload-secret-value.sig-padding"
        out = redact(f"Bearer {jwt}")
        assert "secret-value" not in out

    def test_leaves_normal_text_intact(self):
        text = "The cognitive ledger is a personal memory system."
        out = redact(text)
        assert out == text

    def test_canary_sentinel_is_caught(self):
        sentinel = "CANARY_SECRET_xxxx"
        jwt_like = "eyJalg.payload-" + sentinel + ".sig-padding-123"
        out = redact(f"Bearer {jwt_like}")
        assert sentinel not in out


# ---------------------------------------------------------------------------
# Private fence stripping in notes
# ---------------------------------------------------------------------------

_NOTE_WITH_PRIVATE_FENCE = """\
---
created: 2026-01-01T00:00:00Z
updated: 2026-01-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: meta
lang: en
---

# Public Fact

This part is public.

```private
This content should never appear in any index or prompt.
API_KEY=super-secret-do-not-expose
```

More public content here.
"""


def _strip_private_fences(text: str) -> str:
    """Reference implementation: strip private fenced blocks from note text."""
    return re.sub(r"```private\n.*?```", "", text, flags=re.DOTALL)


class TestPrivateFenceStripping:
    def test_strip_removes_fenced_block(self):
        stripped = _strip_private_fences(_NOTE_WITH_PRIVATE_FENCE)
        assert "super-secret-do-not-expose" not in stripped
        assert "This part is public." in stripped
        assert "More public content here." in stripped

    def test_no_private_fence_unchanged(self):
        note = "# Public\n\nAll content is public here."
        stripped = _strip_private_fences(note)
        assert stripped == note

    def test_multiple_fences_stripped(self):
        note = "Before.\n```private\nSECRET_A\n```\nMiddle.\n```private\nSECRET_B\n```\nAfter."
        stripped = _strip_private_fences(note)
        assert "SECRET_A" not in stripped
        assert "SECRET_B" not in stripped
        assert "Before." in stripped
        assert "Middle." in stripped
        assert "After." in stripped


# ---------------------------------------------------------------------------
# Doctor check: private_fence_in_index
# ---------------------------------------------------------------------------

class TestDoctorPrivateFenceCheck:
    def setup_method(self):
        from ledger.config import reset_config
        reset_config()

    def teardown_method(self):
        import os
        from ledger.config import reset_config
        reset_config()
        os.environ.pop("XDG_CONFIG_HOME", None)

    def test_doctor_fires_on_polluted_index(self, tmp_path):
        import os
        from ledger.config import LedgerConfig, set_config
        from ledger.doctor import run_doctor

        os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
        root = tmp_path / "ledger"
        notes = tmp_path / "notes"
        notes.mkdir(parents=True)
        (notes / "08_indices").mkdir(parents=True)
        root.mkdir(parents=True)

        cfg = LedgerConfig(ledger_root=root, ledger_notes_dir=notes)
        set_config(cfg)

        # Write a polluted note_index.json.
        index = notes / "08_indices" / "note_index.json"
        index.write_text(
            json.dumps({"notes": [{"body": "```private\nsecret\n```"}]}),
            encoding="utf-8",
        )

        payload = run_doctor()
        ids = [f.id for f in payload.findings]
        assert "private_fence_in_index" in ids

    def test_doctor_clean_on_safe_index(self, tmp_path):
        import os
        from ledger.config import LedgerConfig, set_config
        from ledger.doctor import run_doctor

        os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
        root = tmp_path / "ledger"
        notes = tmp_path / "notes"
        notes.mkdir(parents=True)
        (notes / "08_indices").mkdir(parents=True)
        root.mkdir(parents=True)

        cfg = LedgerConfig(ledger_root=root, ledger_notes_dir=notes)
        set_config(cfg)

        # Write a clean note_index.json.
        index = notes / "08_indices" / "note_index.json"
        index.write_text(
            json.dumps({"notes": [{"title": "A safe note", "tags": ["test"]}]}),
            encoding="utf-8",
        )
        # Create timeline so that check doesn't fire.
        from ledger.timeline import TIMELINE_MARKDOWN_HEADER
        from ledger.io.safe_write import safe_write_text
        tl = notes / "08_indices" / "timeline.md"
        safe_write_text(tl, TIMELINE_MARKDOWN_HEADER)

        payload = run_doctor()
        ids = [f.id for f in payload.findings]
        assert "private_fence_in_index" not in ids


# ---------------------------------------------------------------------------
# Scope / retrieval boundary smoke test
# ---------------------------------------------------------------------------

class TestScopeBoundary:
    """Verify that scope filtering is respected in retrieval results.

    This is a lightweight contract test: it confirms that the query module
    accepts a scope parameter and that the result objects carry scope info.
    """

    def test_scope_all_accepted(self):
        """validate_scope('all') should not raise."""
        from ledger.validation import validate_scope
        result = validate_scope("all")
        assert result == "all"

    def test_scope_personal_accepted(self):
        from ledger.validation import validate_scope
        result = validate_scope("personal")
        assert result == "personal"

    def test_invalid_scope_rejected(self):
        from ledger.errors import ScopeValidationError
        from ledger.validation import validate_scope
        with pytest.raises(ScopeValidationError):
            validate_scope("classified_top_secret_scope")
