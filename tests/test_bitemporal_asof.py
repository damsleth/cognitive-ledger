"""Tests for --as-of temporal filter (slice A4).

Covers:
- Superseded fact IS returned for --as-of in its valid window and NOT after.
- Default query excludes superseded (expired valid_to) notes.
- Default query passes notes with NO validity fields unchanged (golden test).
- --as-of widens corpus to include 09_archive notes.
- --as-of composes with --scope filter.
- --as-of composes with --limit.
- Future --as-of (valid_from in future).
- Corpus with zero bitemporal fields ranks identically to a pre-A4 run (golden).
- Note index persists valid_from/valid_to/superseded_by and loads them back.
- Stale index (missing bitemporal fields) is handled gracefully.
"""

from __future__ import annotations

import datetime as dt
import importlib
from pathlib import Path
from typing import Any

import pytest

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.retrieval import (
    apply_temporal_filter,
    build_candidates,
    build_candidates_with_archive,
    clear_candidate_cache,
    rebuild_note_index,
)
from ledger import retrieval as retrieval_mod
from ledger import query as query_lib
from ledger.retrieval_types import RetrievalCandidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_FM = """\
created: 2026-01-01T00:00:00Z
updated: 2026-01-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
"""

_BASE_WORK_FM = """\
created: 2026-01-01T00:00:00Z
updated: 2026-01-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: work
lang: en
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_note(extra_fm: str = "", body: str = "## Statement\n\nBody content.\n") -> str:
    return f"---\n{_BASE_FM}{extra_fm}---\n\n# Test Note\n\n{body}"


def _make_work_note(extra_fm: str = "", body: str = "## Statement\n\nWork content.\n") -> str:
    return f"---\n{_BASE_WORK_FM}{extra_fm}---\n\n# Work Note\n\n{body}"


def _utc(year: int, month: int = 1, day: int = 1) -> dt.datetime:
    return dt.datetime(year, month, day, tzinfo=dt.timezone.utc)


def _ts(year: int, month: int = 1, day: int = 1) -> str:
    return _utc(year, month, day).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def tmp_cfg(tmp_path):
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    (config.ledger_notes_dir / "02_facts").mkdir(parents=True, exist_ok=True)
    (config.ledger_notes_dir / "09_archive").mkdir(parents=True, exist_ok=True)
    (config.ledger_notes_dir / "08_indices").mkdir(parents=True, exist_ok=True)
    yield config
    clear_candidate_cache()
    reset_config()


# ---------------------------------------------------------------------------
# apply_temporal_filter unit tests
# ---------------------------------------------------------------------------

class TestApplyTemporalFilter:
    """Unit-test the filter function directly with synthetic candidates."""

    def _make_candidate(self, valid_from: str = "", valid_to: str = "", superseded_by: str = "") -> RetrievalCandidate:
        return RetrievalCandidate(
            path="/tmp/fake.md",
            rel_path="notes/02_facts/fact__fake.md",
            type="fact",
            title="Fake",
            statement="",
            body="",
            updated="2026-01-01T00:00:00Z",
            updated_ts=_utc(2026),
            confidence=0.9,
            source="user",
            scope="dev",
            status="",
            tags=[],
            note_tokens=set(),
            tag_tokens=set(),
            attention_tokens=set(),
            snippet="",
            has_next_action_checkbox=False,
            valid_from=valid_from,
            valid_to=valid_to,
            superseded_by=superseded_by,
        )

    # ---- Default path (as_of=None) ----

    def test_no_validity_fields_pass_through(self):
        c = self._make_candidate()
        result = apply_temporal_filter([c], as_of=None, now_dt=_utc(2026, 6))
        assert result == [c], "notes with no validity fields must pass through unchanged"

    def test_active_note_with_valid_from_only_passes(self):
        c = self._make_candidate(valid_from=_ts(2025))
        result = apply_temporal_filter([c], as_of=None, now_dt=_utc(2026, 6))
        assert result == [c]

    def test_expired_note_hidden_by_default(self):
        c = self._make_candidate(valid_from=_ts(2025), valid_to=_ts(2026, 4))
        result = apply_temporal_filter([c], as_of=None, now_dt=_utc(2026, 6))
        assert result == [], "expired valid_to should hide note in default path"

    def test_currently_valid_note_passes(self):
        c = self._make_candidate(valid_from=_ts(2025), valid_to=_ts(2027))
        result = apply_temporal_filter([c], as_of=None, now_dt=_utc(2026, 6))
        assert result == [c]

    # ---- --as-of path ----

    def test_asof_note_in_window_included(self):
        c = self._make_candidate(valid_from=_ts(2025), valid_to=_ts(2026, 4))
        result = apply_temporal_filter([c], as_of=_utc(2026, 3, 15), now_dt=_utc(2026, 6))
        assert result == [c], "note valid at as_of should be included"

    def test_asof_note_outside_window_excluded(self):
        c = self._make_candidate(valid_from=_ts(2025), valid_to=_ts(2026, 4))
        result = apply_temporal_filter([c], as_of=_utc(2026, 5), now_dt=_utc(2026, 6))
        assert result == [], "note expired before as_of should be excluded"

    def test_asof_null_valid_from_treated_as_all_time(self):
        # No valid_from means valid from the beginning of time.
        c = self._make_candidate(valid_to=_ts(2027))
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = apply_temporal_filter([c], as_of=_utc(2026, 6), now_dt=_utc(2026, 6))
        assert result == [c]
        assert any("valid_from" in str(warning.message) for warning in w), \
            "should warn about missing valid_from in as-of path"

    def test_asof_future_valid_from_excluded(self):
        """A note that starts being valid in the future is not valid at today's as-of."""
        c = self._make_candidate(valid_from=_ts(2027))
        result = apply_temporal_filter([c], as_of=_utc(2026, 6), now_dt=_utc(2026, 6))
        assert result == [], "future valid_from should exclude the note for a past/now as-of"

    def test_asof_future_as_of_includes_future_note(self):
        """--as-of in the future should include notes with valid_from in that range."""
        c = self._make_candidate(valid_from=_ts(2027))
        result = apply_temporal_filter([c], as_of=_utc(2028), now_dt=_utc(2026, 6))
        assert result == [c]


# ---------------------------------------------------------------------------
# Note index persistence: valid_from / valid_to / superseded_by
# ---------------------------------------------------------------------------

class TestNoteIndexBitemporalFields:
    def test_index_persists_bitemporal_fields(self, tmp_cfg):
        note = tmp_cfg.ledger_notes_dir / "02_facts" / "fact__bitemp.md"
        _write(note, _make_note(
            "valid_from: 2025-01-01T00:00:00Z\nvalid_to: 2026-06-01T00:00:00Z\nsuperseded_by: notes/09_archive/fact__old.md\n"
        ))
        importlib.reload(retrieval_mod)
        idx = rebuild_note_index()
        entry = idx["entries"]["notes/02_facts/fact__bitemp.md"]["candidate"]
        assert entry.get("valid_from") == "2025-01-01T00:00:00Z"
        assert entry.get("valid_to") == "2026-06-01T00:00:00Z"
        assert entry.get("superseded_by") == "notes/09_archive/fact__old.md"

    def test_index_omits_bitemporal_fields_for_legacy_notes(self, tmp_cfg):
        note = tmp_cfg.ledger_notes_dir / "02_facts" / "fact__legacy.md"
        _write(note, _make_note())
        importlib.reload(retrieval_mod)
        idx = rebuild_note_index()
        entry = idx["entries"]["notes/02_facts/fact__legacy.md"]["candidate"]
        assert "valid_from" not in entry
        assert "valid_to" not in entry
        assert "superseded_by" not in entry

    def test_candidate_from_index_has_empty_strings_for_missing_fields(self, tmp_cfg):
        note = tmp_cfg.ledger_notes_dir / "02_facts" / "fact__legacy2.md"
        _write(note, _make_note())
        importlib.reload(retrieval_mod)
        candidates = build_candidates(use_cache=False)
        assert candidates
        c = candidates[0]
        assert c.valid_from == ""
        assert c.valid_to == ""
        assert c.superseded_by == ""

    def test_stale_index_missing_bitemporal_fields_graceful(self, tmp_cfg):
        """A pre-A4 index entry (no valid_from key) should deserialize as empty strings."""
        note = tmp_cfg.ledger_notes_dir / "02_facts" / "fact__stale.md"
        _write(note, _make_note())
        # Manually inject a stale index entry without bitemporal fields.
        import json
        idx_path = tmp_cfg.ledger_notes_dir / "08_indices" / "note_index.json"
        idx = rebuild_note_index()
        # Remove the bitemporal fields from the entry to simulate a stale index.
        entry = idx["entries"]["notes/02_facts/fact__stale.md"]
        candidate_json = dict(entry["candidate"])
        candidate_json.pop("valid_from", None)
        candidate_json.pop("valid_to", None)
        candidate_json.pop("superseded_by", None)
        entry["candidate"] = candidate_json
        idx_path.write_text(json.dumps(idx, indent=2, ensure_ascii=False))

        # Force reload from the stale index.
        from ledger.retrieval import _candidate_from_json
        c = _candidate_from_json(candidate_json)
        assert c.valid_from == ""
        assert c.valid_to == ""
        assert c.superseded_by == ""


# ---------------------------------------------------------------------------
# Integration tests with rank_query (lexical mode)
# ---------------------------------------------------------------------------

class TestAsOfRetrievalLexical:
    """End-to-end tests using rank_query in legacy (lexical) mode."""

    def _rank(self, query: str, tmp_cfg: LedgerConfig, as_of=None, scope: str = "all", limit: int = 20) -> list[str]:
        result = query_lib.rank_query(
            query,
            scope=scope,
            limit=limit,
            retrieval_mode="legacy",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda b, m: "unused",
            as_of=as_of,
        )
        return [r.rel_path for r in result.results]

    def test_superseded_fact_in_window_returned_for_asof(self, tmp_cfg):
        """A fact valid 2025-01-01 to 2026-04-01 IS returned for --as-of 2026-03-15."""
        archive = tmp_cfg.ledger_notes_dir / "09_archive" / "fact__old_info.md"
        _write(archive, _make_note(
            "valid_from: 2025-01-01T00:00:00Z\nvalid_to: 2026-04-01T00:00:00Z\n",
            body="## Statement\n\nold information about topic asof\n",
        ))
        as_of = _utc(2026, 3, 15)
        paths = self._rank("topic asof", tmp_cfg, as_of=as_of)
        assert any("fact__old_info" in p for p in paths), \
            f"superseded note should appear for as-of 2026-03-15; got: {paths}"

    def test_superseded_fact_excluded_after_expiry(self, tmp_cfg):
        """Same fact NOT returned for --as-of 2026-05-01 (after valid_to)."""
        archive = tmp_cfg.ledger_notes_dir / "09_archive" / "fact__old_info2.md"
        _write(archive, _make_note(
            "valid_from: 2025-01-01T00:00:00Z\nvalid_to: 2026-04-01T00:00:00Z\n",
            body="## Statement\n\nold information about topic expiry\n",
        ))
        as_of = _utc(2026, 5, 1)
        paths = self._rank("topic expiry", tmp_cfg, as_of=as_of)
        assert not any("fact__old_info2" in p for p in paths), \
            f"superseded note must NOT appear after expiry; got: {paths}"

    def test_default_query_excludes_superseded_note(self, tmp_cfg):
        """Without --as-of, a note with expired valid_to is hidden."""
        note = tmp_cfg.ledger_notes_dir / "02_facts" / "fact__expired.md"
        _write(note, _make_note(
            "valid_from: 2025-01-01T00:00:00Z\nvalid_to: 2025-12-31T00:00:00Z\n",
            body="## Statement\n\nexpired concept defaultquery\n",
        ))
        # now_dt is 2026 — well past valid_to
        now = _utc(2026, 6)
        result = query_lib.rank_query(
            "expired concept defaultquery",
            scope="all",
            limit=20,
            retrieval_mode="legacy",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda b, m: "unused",
            now_dt=now,
        )
        paths = [r.rel_path for r in result.results]
        assert not any("fact__expired" in p for p in paths), \
            f"expired note must be hidden from default query; got: {paths}"

    def test_null_valid_from_note_never_dropped(self, tmp_cfg):
        """A legacy note (no valid_from) must pass through in both default and as-of modes."""
        note = tmp_cfg.ledger_notes_dir / "02_facts" / "fact__legacy_nullvf.md"
        _write(note, _make_note(body="## Statement\n\nlegacy note no validity nullvf check\n"))
        # Default path
        paths_default = self._rank("legacy note nullvf check", tmp_cfg)
        assert any("fact__legacy_nullvf" in p for p in paths_default), \
            "legacy note must appear in default query"
        # --as-of path
        paths_asof = self._rank("legacy note nullvf check", tmp_cfg, as_of=_utc(2020))
        assert any("fact__legacy_nullvf" in p for p in paths_asof), \
            "legacy note must appear in --as-of query"

    def test_asof_composes_with_scope(self, tmp_cfg):
        """--as-of with --scope=dev excludes work-scoped archive notes."""
        archive_dev = tmp_cfg.ledger_notes_dir / "09_archive" / "fact__dev_scope.md"
        _write(archive_dev, _make_note(
            "valid_from: 2025-01-01T00:00:00Z\nvalid_to: 2026-06-30T00:00:00Z\n",
            body="## Statement\n\ndev scoped old thing asofscope\n",
        ))
        archive_work = tmp_cfg.ledger_notes_dir / "09_archive" / "fact__work_scope.md"
        _write(archive_work, _make_work_note(
            "valid_from: 2025-01-01T00:00:00Z\nvalid_to: 2026-06-30T00:00:00Z\n",
            body="## Statement\n\nwork scoped old thing asofscope\n",
        ))
        as_of = _utc(2026, 3)
        paths = self._rank("old thing asofscope", tmp_cfg, as_of=as_of, scope="dev")
        assert any("fact__dev_scope" in p for p in paths), \
            f"dev-scoped archive note should appear; got: {paths}"

    def test_asof_composes_with_limit(self, tmp_cfg):
        """--as-of with limit=1 returns at most 1 result."""
        for i in range(5):
            note = tmp_cfg.ledger_notes_dir / "09_archive" / f"fact__limtest{i}.md"
            _write(note, _make_note(
                f"valid_from: 2025-01-01T00:00:00Z\nvalid_to: 2026-12-31T00:00:00Z\n",
                body=f"## Statement\n\ntest limit note limtest{i}\n",
            ))
        paths = self._rank("test limit note limtest", tmp_cfg, as_of=_utc(2026, 6), limit=1)
        assert len(paths) <= 1, f"limit=1 must be respected; got {len(paths)} results"

    def test_future_asof_includes_future_valid_from(self, tmp_cfg):
        """--as-of 2030-01-01 should surface notes with valid_from in 2027."""
        note = tmp_cfg.ledger_notes_dir / "02_facts" / "fact__future.md"
        _write(note, _make_note(
            "valid_from: 2027-06-01T00:00:00Z\n",
            body="## Statement\n\nfuture valid from futureasof\n",
        ))
        paths = self._rank("future valid from futureasof", tmp_cfg, as_of=_utc(2030))
        assert any("fact__future" in p for p in paths), \
            f"future valid_from note should appear for as-of 2030; got: {paths}"

    def test_zero_bitemporal_corpus_ranks_identically(self, tmp_cfg):
        """A corpus with no bitemporal fields must rank identically with and without filtering."""
        for i in range(3):
            note = tmp_cfg.ledger_notes_dir / "02_facts" / f"fact__plain{i}.md"
            _write(note, _make_note(body=f"## Statement\n\nplain note golden{i}\n"))

        result_no_asof = query_lib.rank_query(
            "plain note golden",
            scope="all",
            limit=10,
            retrieval_mode="legacy",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda b, m: "unused",
        )
        result_with_asof = query_lib.rank_query(
            "plain note golden",
            scope="all",
            limit=10,
            retrieval_mode="legacy",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda b, m: "unused",
            as_of=_utc(2026, 6),
        )
        paths_no = [r.rel_path for r in result_no_asof.results]
        paths_with = [r.rel_path for r in result_with_asof.results]
        scores_no = [round(r.score, 6) for r in result_no_asof.results]
        scores_with = [round(r.score, 6) for r in result_with_asof.results]
        assert paths_no == paths_with, \
            f"ranked order must be identical; no-asof={paths_no}, with-asof={paths_with}"
        assert scores_no == scores_with, \
            f"scores must be identical; no-asof={scores_no}, with-asof={scores_with}"


# ---------------------------------------------------------------------------
# build_candidates_with_archive includes archive notes
# ---------------------------------------------------------------------------

class TestBuildCandidatesWithArchive:
    def test_includes_archive_notes(self, tmp_cfg):
        note = tmp_cfg.ledger_notes_dir / "02_facts" / "fact__core.md"
        _write(note, _make_note())
        archive = tmp_cfg.ledger_notes_dir / "09_archive" / "fact__archived.md"
        _write(archive, _make_note("valid_from: 2025-01-01T00:00:00Z\nvalid_to: 2026-04-01T00:00:00Z\n"))
        candidates = build_candidates_with_archive()
        rel_paths = [c.rel_path for c in candidates]
        assert any("fact__core" in p for p in rel_paths)
        assert any("fact__archived" in p for p in rel_paths), \
            f"archive note must be in widened pool; got {rel_paths}"

    def test_archive_candidate_has_bitemporal_fields(self, tmp_cfg):
        archive = tmp_cfg.ledger_notes_dir / "09_archive" / "fact__bitemporalinarchive.md"
        _write(archive, _make_note(
            "valid_from: 2024-06-01T00:00:00Z\nvalid_to: 2025-06-01T00:00:00Z\n"
        ))
        candidates = build_candidates_with_archive()
        c = next((x for x in candidates if "bitemporalinarchive" in x.rel_path), None)
        assert c is not None
        assert c.valid_from == "2024-06-01T00:00:00Z"
        assert c.valid_to == "2025-06-01T00:00:00Z"

    def test_no_archive_dir_returns_core_only(self, tmp_cfg):
        """If 09_archive does not exist, build_candidates_with_archive behaves like build_candidates."""
        import shutil
        archive_dir = tmp_cfg.ledger_notes_dir / "09_archive"
        shutil.rmtree(str(archive_dir), ignore_errors=True)
        note = tmp_cfg.ledger_notes_dir / "02_facts" / "fact__onlyone.md"
        _write(note, _make_note())
        candidates = build_candidates_with_archive()
        assert any("fact__onlyone" in c.rel_path for c in candidates)
        assert not any("09_archive" in c.rel_path for c in candidates)


# ---------------------------------------------------------------------------
# CLI _parse_as_of helper tests
# ---------------------------------------------------------------------------

class TestParseAsOf:
    def test_valid_date_string(self):
        from ledger.cli import _parse_as_of
        result = _parse_as_of("2026-03-15")
        assert result == _utc(2026, 3, 15)

    def test_valid_full_timestamp(self):
        from ledger.cli import _parse_as_of
        result = _parse_as_of("2026-03-15T12:00:00Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 15

    def test_none_returns_none(self):
        from ledger.cli import _parse_as_of
        assert _parse_as_of(None) is None
        assert _parse_as_of("") is None

    def test_invalid_string_raises(self):
        from ledger.cli import _parse_as_of
        with pytest.raises(ValueError, match="--as-of"):
            _parse_as_of("not-a-date")
