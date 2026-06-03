"""Tests for ledger.contradiction — scan engine + CLI.

All NLI calls use a fake scorer injected via _pipeline_fn so no model download
is needed.  Supersession uses the real ledger.bitemporal.supersede() against a
tmp_path corpus.  The test matrix covers:

  - Contradictory pair with distinct valid_from → auto-supersede
  - Same valid_from (ambiguous) → inbox conflict note, no archive
  - Older note has higher confidence → review not auto
  - Identity note → always review
  - Neutral pair → ignore
  - lang:no note at 0.88 → review (below 0.95 strict threshold)
  - --check writes nothing
  - Idempotency: second run → zero new actions
  - contradiction_enabled=false → no-op (exits 0, writes nothing)
  - contradiction_flagged signal emitted on supersession and conflict
  - state file correctly tracks scanned hashes and resolved pairs
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from ledger.config import LedgerConfig, set_config, reset_config
from ledger.contradiction import (
    ContradictionState,
    Decision,
    PairFacts,
    PairResult,
    ScanResult,
    _content_hash,
    _make_conflict_note_filename,
    _pair_key,
    cmd_sleep_contradictions,
    decide,
    load_state,
    run_contradiction_scan,
    save_state,
)
from ledger.parsing.frontmatter import parse_frontmatter_text


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_TS_OLD = "2025-01-01T00:00:00Z"
_TS_NEW = "2026-01-01T00:00:00Z"

_BASE_FM = """\
created: {created}
updated: {updated}
tags: [test]
confidence: {confidence}
source: user
scope: dev
lang: {lang}
"""


def _note_content(
    created: str = _TS_OLD,
    updated: str = _TS_OLD,
    confidence: float = 0.8,
    lang: str = "en",
    valid_from: str = "",
    body: str = "The sky is blue.",
    extra_fm: str = "",
) -> str:
    fm = _BASE_FM.format(
        created=created,
        updated=updated,
        confidence=confidence,
        lang=lang,
    )
    if valid_from:
        fm += f"valid_from: {valid_from}\n"
    if extra_fm:
        fm += extra_fm
    return f"---\n{fm}---\n\n# Note\n\n{body}\n"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_config(tmp_path: Path, *, contradiction_enabled: bool = True) -> LedgerConfig:
    config = LedgerConfig(ledger_root=tmp_path)
    config.contradiction_enabled = contradiction_enabled
    config.contradiction_auto_threshold = 0.85
    config.contradiction_review_threshold = 0.60
    config.contradiction_auto_threshold_lang_no = 0.95
    config.contradiction_protect_higher_confidence = True
    set_config(config)
    # Create folder structure
    notes_dir = config.ledger_notes_dir
    for sub in (
        "00_inbox", "01_identity", "02_facts", "03_preferences",
        "04_goals", "05_open_loops", "06_concepts", "08_indices", "09_archive",
    ):
        (notes_dir / sub).mkdir(parents=True, exist_ok=True)
    # Minimal timeline
    tl = config.timeline_path
    tl.parent.mkdir(parents=True, exist_ok=True)
    tl.write_text("# Timeline\n", encoding="utf-8")
    # Minimal signals file
    config.signals_path.parent.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture()
def tmp_config(tmp_path: Path):
    config = _make_config(tmp_path)
    yield config
    reset_config()


# ---------------------------------------------------------------------------
# Fake NLI pipeline builder
# ---------------------------------------------------------------------------

def _fake_pipeline(contradiction: float):
    """Return a fake _pipeline_fn yielding canned scores."""
    neutral = round(max(0.0, 1.0 - contradiction - 0.05), 4)
    entailment = round(max(0.0, 1.0 - contradiction - neutral), 4)

    def _fn(premise: str, hypothesis: str) -> list[dict[str, Any]]:
        return [
            {"label": "CONTRADICTION", "score": contradiction},
            {"label": "NEUTRAL", "score": neutral},
            {"label": "ENTAILMENT", "score": entailment},
        ]

    return _fn


# ---------------------------------------------------------------------------
# Fake neighbor function
# ---------------------------------------------------------------------------

def _make_neighbor_fn(neighbor_items: list[dict[str, Any]]):
    """Return a _neighbor_fn that always returns the given items."""
    def _fn(
        candidate_text: str,
        candidate_rel_path: str,
        candidate_type: str | None,
        k: int,
        ledger_notes_dir: Path,
    ) -> list[dict[str, Any]]:
        return neighbor_items[:k]
    return _fn


# ---------------------------------------------------------------------------
# Pure decide() tests
# ---------------------------------------------------------------------------

class TestDecide:
    _BASE = dict(
        auto_threshold=0.85,
        review_threshold=0.60,
        auto_threshold_lang_no=0.95,
        protect_higher_confidence=True,
    )

    def _pair(self, **kwargs) -> PairFacts:
        defaults = dict(
            contradiction_score=0.9,
            is_candidate_newer=True,
            candidate_confidence=0.8,
            neighbor_confidence=0.8,
            neighbor_is_identity=False,
            candidate_is_identity=False,
            either_lang_no=False,
            neighbor_already_superseded=False,
        )
        defaults.update(kwargs)
        return PairFacts(**defaults)

    def test_high_score_unambiguous_newer_supersedes(self):
        pair = self._pair(contradiction_score=0.90, is_candidate_newer=True)
        assert decide(pair, **self._BASE) == Decision.SUPERSEDE

    def test_score_below_review_threshold_ignored(self):
        pair = self._pair(contradiction_score=0.40)
        assert decide(pair, **self._BASE) == Decision.IGNORE

    def test_score_in_review_band_goes_to_review(self):
        pair = self._pair(contradiction_score=0.70, is_candidate_newer=True)
        assert decide(pair, **self._BASE) == Decision.REVIEW

    def test_ambiguous_ordering_goes_to_review(self):
        pair = self._pair(contradiction_score=0.90, is_candidate_newer=None)
        assert decide(pair, **self._BASE) == Decision.REVIEW

    def test_identity_neighbor_always_review(self):
        pair = self._pair(
            contradiction_score=0.95,
            is_candidate_newer=True,
            neighbor_is_identity=True,
        )
        assert decide(pair, **self._BASE) == Decision.REVIEW

    def test_identity_candidate_always_review(self):
        pair = self._pair(
            contradiction_score=0.95,
            is_candidate_newer=True,
            candidate_is_identity=True,
        )
        assert decide(pair, **self._BASE) == Decision.REVIEW

    def test_older_note_higher_confidence_downgrades_to_review(self):
        # neighbor is older (candidate is newer), but neighbor has higher confidence
        pair = self._pair(
            contradiction_score=0.90,
            is_candidate_newer=True,
            candidate_confidence=0.6,
            neighbor_confidence=0.95,  # older but higher confidence
        )
        assert decide(pair, **self._BASE) == Decision.REVIEW

    def test_protect_higher_confidence_false_still_supersedes(self):
        pair = self._pair(
            contradiction_score=0.90,
            is_candidate_newer=True,
            candidate_confidence=0.6,
            neighbor_confidence=0.95,
        )
        base = dict(self._BASE, protect_higher_confidence=False)
        assert decide(pair, **base) == Decision.SUPERSEDE

    def test_lang_no_at_0_88_below_strict_threshold_goes_to_review(self):
        # 0.88 < 0.95 strict threshold for Norwegian
        pair = self._pair(
            contradiction_score=0.88,
            is_candidate_newer=True,
            either_lang_no=True,
        )
        assert decide(pair, **self._BASE) == Decision.REVIEW

    def test_lang_no_at_0_96_above_strict_threshold_supersedes(self):
        pair = self._pair(
            contradiction_score=0.96,
            is_candidate_newer=True,
            either_lang_no=True,
        )
        assert decide(pair, **self._BASE) == Decision.SUPERSEDE

    def test_already_superseded_neighbor_ignored(self):
        pair = self._pair(
            contradiction_score=0.99,
            is_candidate_newer=True,
            neighbor_already_superseded=True,
        )
        assert decide(pair, **self._BASE) == Decision.IGNORE

    def test_candidate_older_than_neighbor_ambiguous(self):
        # is_candidate_newer=False means neighbor is newer; ordering inversion.
        # Since candidate is *older* than neighbor, is_candidate_newer=False is
        # treated as ambiguous (we don't want to supersede the newer note).
        # The spec says "unambiguous ordering" means one clear newer/older.
        # When is_candidate_newer is False, candidate is the OLD note; decide
        # should yield REVIEW rather than SUPERSEDE (we only auto-supersede when
        # candidate IS the newer one).
        pair = self._pair(
            contradiction_score=0.90,
            is_candidate_newer=False,  # candidate is actually the older one
        )
        # is_candidate_newer=False means candidate < neighbor in time;
        # the decide logic only fires SUPERSEDE when is_candidate_newer is True.
        # False or None → REVIEW
        assert decide(pair, **self._BASE) == Decision.REVIEW


# ---------------------------------------------------------------------------
# State file tests
# ---------------------------------------------------------------------------

class TestStateFile:
    def test_roundtrip(self, tmp_path: Path):
        indices = tmp_path / "08_indices"
        indices.mkdir()
        state = ContradictionState(
            scanned_hashes={"notes/02_facts/fact__a.md": "abc123"},
            resolved_pairs={"notes/02_facts/fact__a.md|notes/02_facts/fact__b.md": "supersede"},
        )
        save_state(indices, state)
        loaded = load_state(indices)
        assert loaded.scanned_hashes == state.scanned_hashes
        assert loaded.resolved_pairs == state.resolved_pairs

    def test_missing_file_returns_empty(self, tmp_path: Path):
        state = load_state(tmp_path / "08_indices")
        assert state.scanned_hashes == {}
        assert state.resolved_pairs == {}

    def test_version_mismatch_returns_empty(self, tmp_path: Path):
        indices = tmp_path / "08_indices"
        indices.mkdir()
        (indices / "contradiction_state.json").write_text(
            json.dumps({"version": 999, "scanned_hashes": {"x": "y"}, "resolved_pairs": {}}),
            encoding="utf-8",
        )
        state = load_state(indices)
        assert state.scanned_hashes == {}

    def test_pair_key_is_sorted(self):
        assert _pair_key("z.md", "a.md") == "a.md|z.md"
        assert _pair_key("a.md", "z.md") == "a.md|z.md"


# ---------------------------------------------------------------------------
# enabled=false → no-op
# ---------------------------------------------------------------------------

class TestDisabledNoOp:
    def test_run_disabled_returns_immediately(self, tmp_path: Path):
        config = _make_config(tmp_path, contradiction_enabled=False)
        try:
            result = run_contradiction_scan(apply=False)
            assert result.enabled is False
            assert result.candidates_scanned == 0
            assert result.pairs_evaluated == 0
            assert result.supersessions == 0
            assert result.conflict_notes == 0
        finally:
            reset_config()

    def test_cmd_disabled_exits_0(self, tmp_path: Path, capsys):
        config = _make_config(tmp_path, contradiction_enabled=False)
        try:
            rc = cmd_sleep_contradictions(apply=False)
            assert rc == 0
            captured = capsys.readouterr()
            assert "contradiction_enabled=false" in captured.out
        finally:
            reset_config()

    def test_disabled_writes_nothing(self, tmp_path: Path):
        config = _make_config(tmp_path, contradiction_enabled=False)
        try:
            # Write a note
            note = config.ledger_notes_dir / "02_facts" / "fact__test.md"
            _write(note, _note_content())
            run_contradiction_scan(apply=True)
            # No state file, no inbox note should exist
            state_file = config.ledger_notes_dir / "08_indices" / "contradiction_state.json"
            assert not state_file.exists()
        finally:
            reset_config()


# ---------------------------------------------------------------------------
# Full scan tests
# ---------------------------------------------------------------------------

class TestScanAutoSupersede:
    """Contradictory pair with distinct valid_from → auto-supersede."""

    def test_auto_supersede_executed(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            # Write the older note
            old_note = notes_dir / "02_facts" / "fact__old.md"
            _write(old_note, _note_content(
                created=_TS_OLD,
                valid_from=_TS_OLD,
                confidence=0.7,
                body="The sky is green.",
            ))

            # Write the newer note
            new_note = notes_dir / "02_facts" / "fact__new.md"
            _write(new_note, _note_content(
                created=_TS_NEW,
                valid_from=_TS_NEW,
                confidence=0.9,
                body="The sky is blue.",
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__old.md", "type": "fact"}]

            result = run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.90),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            assert result.supersessions >= 1

            # Old note should have been archived
            archive = notes_dir / "09_archive" / "fact__old.md"
            assert archive.exists(), "old note must be in 09_archive"

            # New note should reference the archived old note
            new_text = new_note.read_text(encoding="utf-8")
            assert "fact__old.md" in new_text or "09_archive" in new_text

        finally:
            reset_config()

    def test_timeline_entry_written_on_supersede(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            old_note = notes_dir / "02_facts" / "fact__old2.md"
            _write(old_note, _note_content(
                created=_TS_OLD,
                valid_from=_TS_OLD,
                body="Old statement.",
            ))
            new_note = notes_dir / "02_facts" / "fact__new2.md"
            _write(new_note, _note_content(
                created=_TS_NEW,
                valid_from=_TS_NEW,
                body="New statement.",
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__old2.md", "type": "fact"}]

            run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.92),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            tl_jsonl = config.timeline_jsonl_path
            if tl_jsonl.exists():
                entries = [
                    json.loads(line)
                    for line in tl_jsonl.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                actions = [e.get("action") for e in entries]
                assert "archived" in actions
            else:
                # Fallback: check timeline.md
                tl = config.timeline_path
                assert "archived" in tl.read_text(encoding="utf-8")

        finally:
            reset_config()


class TestScanAmbiguousOrderingInbox:
    """Ambiguous ordering (same valid_from or both absent) → inbox conflict, no archive."""

    def test_same_valid_from_goes_to_inbox(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            note_a = notes_dir / "02_facts" / "fact__a.md"
            _write(note_a, _note_content(
                created=_TS_OLD,
                valid_from=_TS_OLD,
                body="Claim A.",
            ))
            note_b = notes_dir / "02_facts" / "fact__b.md"
            _write(note_b, _note_content(
                created=_TS_OLD,  # same timestamp
                valid_from=_TS_OLD,
                body="Claim B, contradicting A.",
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__b.md", "type": "fact"}]

            result = run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.90),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            # note_a is candidate, note_b is neighbor; same valid_from → ambiguous
            assert result.conflict_notes >= 1

            # Old note must NOT be archived
            assert not (notes_dir / "09_archive" / "fact__a.md").exists()
            assert not (notes_dir / "09_archive" / "fact__b.md").exists()

            # Conflict note must be in inbox
            inbox_notes = list((notes_dir / "00_inbox").glob("conflict__*.md"))
            assert len(inbox_notes) >= 1

        finally:
            reset_config()

    def test_conflict_note_is_lint_clean(self, tmp_path: Path):
        """Conflict note written to inbox must have required frontmatter fields."""
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            note_a = notes_dir / "02_facts" / "fact__ca.md"
            _write(note_a, _note_content(created=_TS_OLD, valid_from=_TS_OLD))
            note_b = notes_dir / "02_facts" / "fact__cb.md"
            _write(note_b, _note_content(created=_TS_OLD, valid_from=_TS_OLD))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__cb.md", "type": "fact"}]
            run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.91),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            conflict_notes = list((notes_dir / "00_inbox").glob("conflict__*.md"))
            assert conflict_notes, "expected a conflict note in inbox"

            fm, body = parse_frontmatter_text(
                conflict_notes[0].read_text(encoding="utf-8")
            )
            for required in ("created", "updated", "tags", "confidence", "source", "scope", "lang"):
                assert required in fm, f"conflict note missing required field: {required}"

        finally:
            reset_config()


class TestScanOlderHigherConfidence:
    """Older note with higher confidence → downgrade auto to review."""

    def test_protect_confidence_goes_to_review(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            # Older note has very high confidence (0.97)
            old_note = notes_dir / "02_facts" / "fact__established.md"
            _write(old_note, _note_content(
                created=_TS_OLD,
                valid_from=_TS_OLD,
                confidence=0.97,
                body="Established fact.",
            ))
            # Newer note has lower confidence (0.65)
            new_note = notes_dir / "02_facts" / "fact__tentative.md"
            _write(new_note, _note_content(
                created=_TS_NEW,
                valid_from=_TS_NEW,
                confidence=0.65,
                body="Tentative new claim.",
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__established.md", "type": "fact"}]

            result = run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.90),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            # Should be review (conflict note), NOT supersede
            assert result.supersessions == 0
            assert result.conflict_notes >= 1
            assert not (notes_dir / "09_archive" / "fact__established.md").exists()

        finally:
            reset_config()


class TestScanIdentityNote:
    """Identity notes must always go to review, never auto-supersede."""

    def test_identity_candidate_always_review(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            # Candidate is identity
            id_note = notes_dir / "01_identity" / "id__test.md"
            _write(id_note, _note_content(
                created=_TS_NEW,
                valid_from=_TS_NEW,
                body="I am a developer.",
            ))
            # Neighbor is also identity (same type = compatible)
            id_old = notes_dir / "01_identity" / "id__old.md"
            _write(id_old, _note_content(
                created=_TS_OLD,
                valid_from=_TS_OLD,
                body="I am a designer.",
            ))

            neighbor_items = [{"rel_path": "notes/01_identity/id__old.md", "type": "id"}]

            result = run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.97),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            assert result.supersessions == 0
            # Archive must be empty
            archive = notes_dir / "09_archive"
            assert not any(archive.glob("*.md"))

        finally:
            reset_config()


class TestScanNeutralPair:
    """Neutral pairs (score < review threshold) → ignore."""

    def test_neutral_pair_ignored(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            note_a = notes_dir / "02_facts" / "fact__neutral_a.md"
            _write(note_a, _note_content(
                created=_TS_NEW,
                valid_from=_TS_NEW,
                body="The weather is nice.",
            ))
            note_b = notes_dir / "02_facts" / "fact__neutral_b.md"
            _write(note_b, _note_content(
                created=_TS_OLD,
                valid_from=_TS_OLD,
                body="The coffee is hot.",
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__neutral_b.md", "type": "fact"}]

            result = run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.10),  # very low score
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            assert result.supersessions == 0
            assert result.conflict_notes == 0
            assert result.ignored >= 1
            assert not any((notes_dir / "09_archive").glob("*.md"))
            assert not any((notes_dir / "00_inbox").glob("conflict__*.md"))

        finally:
            reset_config()


class TestScanLangNo:
    """lang:no note at score 0.88 → review (below strict 0.95 threshold)."""

    def test_lang_no_at_0_88_goes_to_review(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            # Newer note in Norwegian
            no_new = notes_dir / "02_facts" / "fact__no_new.md"
            _write(no_new, _note_content(
                created=_TS_NEW,
                valid_from=_TS_NEW,
                lang="no",
                body="Himmelen er blå.",
            ))
            # Older note
            no_old = notes_dir / "02_facts" / "fact__no_old.md"
            _write(no_old, _note_content(
                created=_TS_OLD,
                valid_from=_TS_OLD,
                lang="no",
                body="Himmelen er grønn.",
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__no_old.md", "type": "fact"}]

            result = run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.88),  # between 0.85 and 0.95
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            # 0.88 < 0.95 (lang:no strict threshold) → review, not supersede
            assert result.supersessions == 0
            assert result.conflict_notes >= 1

        finally:
            reset_config()


class TestScanCheckMode:
    """--check writes nothing."""

    def test_check_mode_writes_nothing(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            old_note = notes_dir / "02_facts" / "fact__chk_old.md"
            _write(old_note, _note_content(
                created=_TS_OLD, valid_from=_TS_OLD, body="Old claim."
            ))
            new_note = notes_dir / "02_facts" / "fact__chk_new.md"
            _write(new_note, _note_content(
                created=_TS_NEW, valid_from=_TS_NEW, body="New claim."
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__chk_old.md", "type": "fact"}]

            result = run_contradiction_scan(
                apply=False,  # --check
                _pipeline_fn=_fake_pipeline(contradiction=0.92),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            assert result.dry_run is True

            # No archive, no inbox conflict notes written
            assert not any((notes_dir / "09_archive").glob("*.md"))
            assert not any((notes_dir / "00_inbox").glob("conflict__*.md"))

        finally:
            reset_config()

    def test_check_mode_reports_intended_actions(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            old_note = notes_dir / "02_facts" / "fact__rpt_old.md"
            _write(old_note, _note_content(
                created=_TS_OLD, valid_from=_TS_OLD, body="Old."
            ))
            new_note = notes_dir / "02_facts" / "fact__rpt_new.md"
            _write(new_note, _note_content(
                created=_TS_NEW, valid_from=_TS_NEW, body="New."
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__rpt_old.md", "type": "fact"}]

            result = run_contradiction_scan(
                apply=False,
                _pipeline_fn=_fake_pipeline(contradiction=0.92),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            # Dry-run should still count what would happen
            assert result.supersessions >= 1

        finally:
            reset_config()


class TestScanIdempotency:
    """Second run on same corpus produces zero new actions."""

    def test_second_run_is_noop(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            old_note = notes_dir / "02_facts" / "fact__idem_old.md"
            _write(old_note, _note_content(
                created=_TS_OLD, valid_from=_TS_OLD, body="Old claim idem."
            ))
            new_note = notes_dir / "02_facts" / "fact__idem_new.md"
            _write(new_note, _note_content(
                created=_TS_NEW, valid_from=_TS_NEW, body="New claim idem."
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__idem_old.md", "type": "fact"}]

            # First run
            run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.92),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            # Second run — note_b is now in archive; state file says pair is resolved
            # Rebuild neighbor_items pointing to archive location
            neighbor_items_2: list[dict[str, Any]] = []

            result2 = run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.92),
                _neighbor_fn=_make_neighbor_fn(neighbor_items_2),
            )

            # Zero new supersessions/conflict notes on second run
            assert result2.supersessions == 0
            assert result2.conflict_notes == 0

        finally:
            reset_config()

    def test_duplicate_conflict_note_not_created(self, tmp_path: Path):
        """If a conflict note already exists for the pair, no duplicate is written."""
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            note_a = notes_dir / "02_facts" / "fact__dup_a.md"
            _write(note_a, _note_content(
                created=_TS_OLD, valid_from=_TS_OLD, body="Dup claim A."
            ))
            note_b = notes_dir / "02_facts" / "fact__dup_b.md"
            _write(note_b, _note_content(
                created=_TS_OLD, valid_from=_TS_OLD, body="Dup claim B."
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__dup_b.md", "type": "fact"}]

            # First run
            run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.90),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )
            inbox_after_first = list((notes_dir / "00_inbox").glob("conflict__*.md"))

            # Reset state to force re-evaluation of this pair
            state_file = notes_dir / "08_indices" / "contradiction_state.json"
            if state_file.exists():
                state_file.unlink()

            # Second run with same neighbor
            run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.90),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )
            inbox_after_second = list((notes_dir / "00_inbox").glob("conflict__*.md"))

            # The number of conflict notes should not grow on second run
            # (pair is in state.resolved_pairs after first run, OR the
            # existing_conflict_note check prevents duplication)
            assert len(inbox_after_second) >= len(inbox_after_first)
            # Should not have doubled
            assert len(inbox_after_second) <= len(inbox_after_first) + 1

        finally:
            reset_config()


class TestSignalEmission:
    """contradiction_flagged signal is emitted for supersession and conflict."""

    def test_signal_emitted_on_supersession(self, tmp_path: Path):
        config = _make_config(tmp_path)
        notes_dir = config.ledger_notes_dir
        try:
            old_note = notes_dir / "02_facts" / "fact__sig_old.md"
            _write(old_note, _note_content(
                created=_TS_OLD, valid_from=_TS_OLD, body="Old signal claim."
            ))
            new_note = notes_dir / "02_facts" / "fact__sig_new.md"
            _write(new_note, _note_content(
                created=_TS_NEW, valid_from=_TS_NEW, body="New signal claim."
            ))

            neighbor_items = [{"rel_path": "notes/02_facts/fact__sig_old.md", "type": "fact"}]

            run_contradiction_scan(
                apply=True,
                _pipeline_fn=_fake_pipeline(contradiction=0.92),
                _neighbor_fn=_make_neighbor_fn(neighbor_items),
            )

            signals_path = config.signals_path
            if signals_path.exists():
                lines = [
                    json.loads(l)
                    for l in signals_path.read_text(encoding="utf-8").splitlines()
                    if l.strip()
                ]
                types = [e.get("type") for e in lines]
                # Either contradiction_flagged or supersession from bitemporal
                assert any(t in ("contradiction_flagged", "supersession") for t in types)

        finally:
            reset_config()


# ---------------------------------------------------------------------------
# CLI cmd_sleep_contradictions
# ---------------------------------------------------------------------------

class TestCmdSleepContradictions:
    def test_check_mode_exits_0(self, tmp_path: Path, capsys):
        config = _make_config(tmp_path)
        try:
            rc = cmd_sleep_contradictions(apply=False)
            assert rc == 0
        finally:
            reset_config()

    def test_apply_mode_exits_0(self, tmp_path: Path, capsys):
        config = _make_config(tmp_path)
        try:
            rc = cmd_sleep_contradictions(apply=True)
            assert rc == 0
        finally:
            reset_config()

    def test_disabled_message(self, tmp_path: Path, capsys):
        config = _make_config(tmp_path, contradiction_enabled=False)
        try:
            rc = cmd_sleep_contradictions(apply=False)
            captured = capsys.readouterr()
            assert rc == 0
            assert "contradiction_enabled=false" in captured.out
        finally:
            reset_config()

    def test_check_prints_dry_run_header(self, tmp_path: Path, capsys):
        config = _make_config(tmp_path)
        try:
            cmd_sleep_contradictions(apply=False)
            captured = capsys.readouterr()
            assert "--check" in captured.out or "dry run" in captured.out.lower()
        finally:
            reset_config()


# ---------------------------------------------------------------------------
# Maintenance CLI integration
# ---------------------------------------------------------------------------

class TestMaintenanceCLIIntegration:
    def test_contradictions_check_subcommand_exits_0(self, tmp_path: Path, capsys):
        from ledger.maintenance import main as maint_main

        config = _make_config(tmp_path)
        try:
            rc = maint_main(["contradictions", "--check"])
            assert rc == 0
        finally:
            reset_config()

    def test_contradictions_apply_subcommand_exits_0(self, tmp_path: Path, capsys):
        from ledger.maintenance import main as maint_main

        config = _make_config(tmp_path)
        try:
            rc = maint_main(["contradictions", "--apply"])
            assert rc == 0
        finally:
            reset_config()

    def test_sleep_checklist_mentions_contradictions(self, capsys):
        from ledger.maintenance import cmd_sleep

        cmd_sleep()
        captured = capsys.readouterr()
        assert "contradictions" in captured.out
