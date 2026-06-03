"""Tests for Gap C3: LLM-judged signal seeding (Mechanism 3).

Covers:
- llm_judge.py: dummy backend, subprocess backend (stub), judge()
- signals.py: synthetic fields, down-weighting, real_signal gate,
              purge_synthetic_signals, append_signal_raw
- CLI: ledger signal seed --queries-file, ledger signal purge --synthetic
- Activation gate: synthetic events do not count toward signal_min_entries
- summarize: old JSONL (no synthetic field) parses byte-identically to before
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ledger.config import LedgerConfig, set_config, reset_config
from ledger.signals import (
    append_signal,
    append_signal_raw,
    purge_synthetic_signals,
    read_signals,
    signal_stats,
    summarize_signals,
)
from ledger.llm_judge import judge, Verdict, seed_from_queries, _dummy_judge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_ledger(tmp_path):
    """Minimal temp ledger with config isolation."""
    config = LedgerConfig(ledger_root=tmp_path)
    (tmp_path / "notes" / "08_indices").mkdir(parents=True, exist_ok=True)
    set_config(config)
    yield config
    reset_config()


@pytest.fixture
def signals_path(tmp_ledger):
    return tmp_ledger.signals_path


# ---------------------------------------------------------------------------
# llm_judge: dummy backend
# ---------------------------------------------------------------------------

class TestDummyJudge:
    def test_relevant_when_high_overlap(self):
        # Query terms "machine learning model" appear in note body
        verdict = judge(
            query="machine learning model",
            note_path="notes/02_facts/fact__ml.md",
            note_body="# ML\n\nMachine learning model architecture.\n",
            backend="dummy",
        )
        assert verdict.relevant is True
        assert verdict.rating is not None
        assert 1 <= verdict.rating <= 10

    def test_not_relevant_when_low_overlap(self):
        verdict = judge(
            query="quantum chromodynamics",
            note_path="notes/02_facts/fact__cooking.md",
            note_body="# Cooking\n\nSauté onions until translucent.\n",
            backend="dummy",
        )
        assert verdict.relevant is False
        assert verdict.rating is None

    def test_empty_query_returns_non_relevant(self):
        verdict = judge(
            query="",
            note_path="notes/foo.md",
            note_body="anything",
            backend="dummy",
        )
        assert verdict.relevant is False

    def test_verdict_repr(self):
        v = Verdict(relevant=True, rating=7, reason="test")
        assert "Verdict" in repr(v)
        assert "True" in repr(v)

    def test_rating_range(self):
        # Exact match of all query tokens → maximum overlap → rating near 10
        verdict = judge(
            query="apple orange banana",
            note_path="notes/foo.md",
            note_body="apple orange banana mango",
            backend="dummy",
        )
        assert verdict.relevant is True
        assert verdict.rating is not None
        assert 1 <= verdict.rating <= 10

    def test_deterministic(self):
        """Same inputs produce same verdict every time."""
        v1 = judge("test query", "notes/foo.md", "test query present here", backend="dummy")
        v2 = judge("test query", "notes/foo.md", "test query present here", backend="dummy")
        assert v1.relevant == v2.relevant
        assert v1.rating == v2.rating
        assert v1.reason == v2.reason

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown judge backend"):
            judge("q", "p", "body", backend="nonexistent")


# ---------------------------------------------------------------------------
# llm_judge: subprocess backend (stub script)
# ---------------------------------------------------------------------------

class TestSubprocessBackend:
    def _make_stub_script(self, tmp_path: Path, response: dict) -> str:
        """Create a stub shell script that echoes a canned JSON verdict."""
        script = tmp_path / "stub_judge.sh"
        json_resp = json.dumps(response)
        script.write_text(
            f"#!/bin/sh\n"
            f"# Read and discard stdin\n"
            f"cat > /dev/null\n"
            f"echo '{json_resp}'\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return str(script)

    def test_relevant_verdict(self, tmp_path):
        stub = self._make_stub_script(
            tmp_path, {"relevant": True, "rating": 8, "reason": "good match"}
        )
        verdict = judge(
            query="deploy pipeline",
            note_path="notes/foo.md",
            note_body="deploy CI pipeline",
            backend="subprocess",
            subprocess_command=stub,
        )
        assert verdict.relevant is True
        assert verdict.rating == 8
        assert verdict.reason == "good match"

    def test_not_relevant_verdict(self, tmp_path):
        stub = self._make_stub_script(
            tmp_path, {"relevant": False, "reason": "unrelated"}
        )
        verdict = judge(
            query="quantum physics",
            note_path="notes/bar.md",
            note_body="cooking recipe",
            backend="subprocess",
            subprocess_command=stub,
        )
        assert verdict.relevant is False
        assert verdict.rating is None

    def test_subprocess_no_command_raises(self):
        with pytest.raises(ValueError, match="subprocess_command must be set"):
            judge("q", "p", "body", backend="subprocess", subprocess_command="")

    def test_subprocess_bad_command_raises(self):
        with pytest.raises(RuntimeError):
            judge("q", "p", "body", backend="subprocess",
                  subprocess_command="/nonexistent/cmd_that_does_not_exist_xyz")

    def test_subprocess_non_json_stdout_raises(self, tmp_path):
        """Non-JSON stdout should raise RuntimeError."""
        script = tmp_path / "bad_judge.sh"
        script.write_text("#!/bin/sh\ncat > /dev/null\necho 'not valid json at all'\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        with pytest.raises(RuntimeError, match="non-JSON"):
            judge("q", "p", "body", backend="subprocess",
                  subprocess_command=str(script))

    def test_subprocess_extracts_json_from_prose(self, tmp_path):
        """JSON embedded in prose (e.g. LLM preamble) is extracted correctly."""
        script = tmp_path / "prose_judge.sh"
        response = json.dumps({"relevant": True, "rating": 6, "reason": "ok"})
        script.write_text(
            f"#!/bin/sh\ncat > /dev/null\n"
            f"echo 'Sure, here is my answer: {response}'\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        verdict = judge("q", "p", "body", backend="subprocess",
                        subprocess_command=str(script))
        assert verdict.relevant is True
        assert verdict.rating == 6


# ---------------------------------------------------------------------------
# signals.py: synthetic fields in append_signal
# ---------------------------------------------------------------------------

class TestAppendSignalSynthetic:
    def test_synthetic_flag_persisted(self, tmp_ledger, signals_path):
        entry = append_signal(
            "retrieval_hit",
            query="q",
            note="notes/foo.md",
            synthetic=True,
            source="llm_judge",
        )
        assert entry["synthetic"] is True
        assert entry["source"] == "llm_judge"

        # Verify persisted to disk
        written = json.loads(signals_path.read_text().strip())
        assert written["synthetic"] is True
        assert written["source"] == "llm_judge"

    def test_non_synthetic_signal_has_no_synthetic_field(self, tmp_ledger, signals_path):
        entry = append_signal("affirmation", note="notes/bar.md")
        assert "synthetic" not in entry
        assert "source" not in entry

    def test_append_signal_raw_writes_dict(self, tmp_ledger, signals_path):
        event = {
            "ts": "2026-06-01T10:00:00Z",
            "type": "retrieval_hit",
            "query": "test",
            "note": "notes/foo.md",
            "synthetic": True,
            "source": "llm_judge",
        }
        append_signal_raw(event, signals_path)
        lines = [l for l in signals_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["synthetic"] is True


# ---------------------------------------------------------------------------
# signals.py: summarize_signals down-weighting
# ---------------------------------------------------------------------------

class TestSummarizeSyntheticDownweight:
    def test_synthetic_hits_counted_at_half_weight(self):
        """A synthetic retrieval_hit contributes 0.5 to hit_count."""
        signals = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q", "synthetic": True, "source": "llm_judge"},
        ]
        summary = summarize_signals(signals=signals)
        stats = summary["notes"]["notes/a.md"]
        assert stats["hit_count"] == pytest.approx(0.5)
        assert stats["synthetic_hits"] == 1

    def test_real_hit_counted_at_full_weight(self):
        """A real retrieval_hit contributes 1.0 to hit_count."""
        signals = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q"},
        ]
        summary = summarize_signals(signals=signals)
        stats = summary["notes"]["notes/a.md"]
        assert stats["hit_count"] == pytest.approx(1.0)

    def test_real_outranks_synthetic_at_equal_volume(self):
        """1 real affirmation + 0 negatives vs 2 synthetic affirmations + 0 negatives.

        With default synthetic_weight=0.5, 2 synthetic aff = 1.0 effective aff.
        But 1 real aff = 1.0 effective aff too — so scores are equal.

        Instead verify: 1 real aff beats 1 synthetic aff.
        """
        real_signals = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q"},
            {"ts": "2026-06-01T10:01:00Z", "type": "affirmation", "note": "notes/a.md"},
        ]
        synth_signals = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/b.md", "query": "q", "synthetic": True, "source": "llm_judge"},
            {"ts": "2026-06-01T10:01:00Z", "type": "affirmation", "note": "notes/b.md",
             "synthetic": True, "source": "llm_judge"},
        ]
        summary = summarize_signals(signals=real_signals + synth_signals)
        score_real = summary["notes"]["notes/a.md"]["signal_score"]
        score_synth = summary["notes"]["notes/b.md"]["signal_score"]
        assert score_real > score_synth, (
            f"Real score {score_real} should exceed synthetic score {score_synth}"
        )

    def test_old_jsonl_without_synthetic_field_unchanged(self):
        """Legacy entries (no 'synthetic' key) parse exactly as before."""
        legacy_signals = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q"},
            {"ts": "2026-06-01T10:01:00Z", "type": "affirmation", "note": "notes/a.md"},
            {"ts": "2026-06-01T10:02:00Z", "type": "correction", "note": "notes/a.md"},
        ]
        summary = summarize_signals(signals=legacy_signals)
        stats = summary["notes"]["notes/a.md"]
        # hit_count, corrections, affirmations must be unchanged integers
        assert stats["hit_count"] == 1
        assert stats["affirmations"] == 1
        assert stats["corrections"] == 1

    def test_real_signal_count_in_meta(self):
        """_meta.real_signals counts only non-synthetic entries."""
        signals = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q"},
            {"ts": "2026-06-01T10:01:00Z", "type": "retrieval_hit",
             "note": "notes/b.md", "query": "q", "synthetic": True, "source": "llm_judge"},
        ]
        summary = summarize_signals(signals=signals)
        assert summary["_meta"]["real_signals"] == 1
        assert summary["_meta"]["total_signals"] == 2

    def test_synthetic_corrections_downweighted(self):
        """Synthetic correction contributes 0.5 to corrections count."""
        signals = [
            {"ts": "2026-06-01T10:00:00Z", "type": "correction",
             "note": "notes/a.md", "synthetic": True, "source": "llm_judge"},
        ]
        summary = summarize_signals(signals=signals)
        stats = summary["notes"]["notes/a.md"]
        assert stats["corrections"] == pytest.approx(0.5)
        assert stats["synthetic_corrections"] == 1


# ---------------------------------------------------------------------------
# signals.py: real-signal gate (20-gate ignores synthetic)
# ---------------------------------------------------------------------------

class TestActivationGate:
    def test_gate_ignores_synthetic_for_threshold(self, tmp_ledger, signals_path):
        """19 real + 100 synthetic should NOT trigger the activation gate."""
        from ledger.review import activation_status

        # 19 real signals
        real_sigs = [
            {"ts": f"2026-06-01T10:{i:02d}:00Z", "type": "retrieval_hit",
             "note": f"notes/n{i}.md", "query": f"q{i}"}
            for i in range(19)
        ]
        # 100 synthetic signals
        synth_sigs = [
            {"ts": f"2026-06-02T10:{i:02d}:00Z", "type": "retrieval_hit",
             "note": f"notes/s{i}.md", "query": f"sq{i}",
             "synthetic": True, "source": "llm_judge"}
            for i in range(100)
        ]
        for s in real_sigs + synth_sigs:
            append_signal_raw(s, signals_path)

        stats = signal_stats(signals_path=signals_path)
        assert stats["real_total"] == 19
        assert stats["total"] == 119

        status = activation_status(
            stats["total"],
            real_signals=stats["real_total"],
        )
        assert status["state"] == "accruing"

    def test_gate_activates_with_20_real_signals(self, tmp_ledger, signals_path):
        """Exactly 20 real signals → 'ready' state (weight is 0 by default)."""
        from ledger.review import activation_status

        real_sigs = [
            {"ts": f"2026-06-01T10:{i:02d}:00Z", "type": "retrieval_hit",
             "note": f"notes/n{i}.md", "query": f"q{i}"}
            for i in range(20)
        ]
        for s in real_sigs:
            append_signal_raw(s, signals_path)

        stats = signal_stats(signals_path=signals_path)
        status = activation_status(stats["total"], real_signals=stats["real_total"])
        assert status["state"] == "ready"


# ---------------------------------------------------------------------------
# signals.py: purge_synthetic_signals
# ---------------------------------------------------------------------------

class TestPurgeSyntheticSignals:
    def test_purge_removes_synthetic_only(self, tmp_ledger, signals_path):
        """purge_synthetic_signals removes synthetic events, keeps real ones."""
        events = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q"},
            {"ts": "2026-06-01T10:01:00Z", "type": "affirmation",
             "note": "notes/a.md", "synthetic": True, "source": "llm_judge"},
            {"ts": "2026-06-01T10:02:00Z", "type": "correction",
             "note": "notes/b.md"},
        ]
        for e in events:
            append_signal_raw(e, signals_path)

        removed = purge_synthetic_signals(signals_path=signals_path)
        assert removed == 1

        remaining = read_signals(signals_path=signals_path)
        assert len(remaining) == 2
        for s in remaining:
            assert not s.get("synthetic", False)

    def test_purge_empty_file_returns_zero(self, tmp_ledger, signals_path):
        signals_path.touch()
        removed = purge_synthetic_signals(signals_path=signals_path)
        assert removed == 0

    def test_purge_nonexistent_file_returns_zero(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        removed = purge_synthetic_signals(signals_path=path)
        assert removed == 0

    def test_purge_all_synthetic_leaves_empty_file(self, tmp_ledger, signals_path):
        events = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q",
             "synthetic": True, "source": "llm_judge"},
            {"ts": "2026-06-01T10:01:00Z", "type": "affirmation",
             "note": "notes/b.md",
             "synthetic": True, "source": "llm_judge"},
        ]
        for e in events:
            append_signal_raw(e, signals_path)

        removed = purge_synthetic_signals(signals_path=signals_path)
        assert removed == 2

        remaining = read_signals(signals_path=signals_path)
        assert remaining == []

    def test_purge_preserves_malformed_lines(self, tmp_ledger, signals_path):
        """Non-JSON lines are preserved as-is during purge."""
        signals_path.write_text(
            '{"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit", "note": "a"}\n'
            'not valid json\n'
            '{"ts": "2026-06-01T10:01:00Z", "type": "affirmation", "note": "b", '
            '"synthetic": true}\n'
        )
        removed = purge_synthetic_signals(signals_path=signals_path)
        assert removed == 1

        lines = [l for l in signals_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_summarize_recovers_after_purge(self, tmp_ledger, signals_path):
        """After purge, summarize produces scores from real signals only."""
        events = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q"},
            {"ts": "2026-06-01T10:01:00Z", "type": "affirmation", "note": "notes/a.md"},
            # Synthetic correction that would lower score
            {"ts": "2026-06-01T10:02:00Z", "type": "correction", "note": "notes/a.md",
             "synthetic": True, "source": "llm_judge"},
        ]
        for e in events:
            append_signal_raw(e, signals_path)

        score_before = summarize_signals(
            signals_path=signals_path
        )["notes"]["notes/a.md"]["signal_score"]

        purge_synthetic_signals(signals_path=signals_path)

        score_after = summarize_signals(
            signals_path=signals_path
        )["notes"]["notes/a.md"]["signal_score"]

        # After removing the synthetic correction, the score should be >= before
        assert score_after >= score_before


# ---------------------------------------------------------------------------
# seed_from_queries: integration
# ---------------------------------------------------------------------------

class TestSeedFromQueries:
    def _make_notes(self, notes_dir: Path) -> list[str]:
        """Create a few test notes and return their relative paths."""
        note_data = {
            "deploy.md": "# Deployment\n\nHow to deploy the application pipeline.\n",
            "auth.md": "# Authentication\n\nOAuth token and login flow.\n",
            "cooking.md": "# Cooking\n\nRecipe for pasta carbonara.\n",
        }
        for name, content in note_data.items():
            (notes_dir / name).write_text(content, encoding="utf-8")
        return list(note_data.keys())

    def test_seed_produces_events(self, tmp_path):
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        self._make_notes(notes_dir)

        events = seed_from_queries(
            queries=["deploy pipeline", "authentication"],
            notes_dir=notes_dir,
            top_k=2,
            backend="dummy",
        )
        assert len(events) > 0
        for e in events:
            assert e["synthetic"] is True
            assert e["source"] == "llm_judge"
            assert e["type"] in ("retrieval_hit", "retrieval_miss")
            assert "ts" in e
            assert "note" in e

    def test_seed_relevant_note_gets_hit(self, tmp_path):
        """Note mentioning 'deploy' should be classified relevant for deploy query."""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        (notes_dir / "deploy.md").write_text(
            "# Deploy\n\nHow to deploy the pipeline to production.\n"
        )

        events = seed_from_queries(
            queries=["how to deploy pipeline"],
            notes_dir=notes_dir,
            top_k=1,
            backend="dummy",
        )
        hits = [e for e in events if e["type"] == "retrieval_hit"]
        assert len(hits) >= 1

    def test_seed_with_custom_retrieve_fn(self, tmp_path):
        """Custom retrieve_fn is called and its results are judged."""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        (notes_dir / "custom.md").write_text(
            "# Custom\n\nThis is a custom retrieve test note.\n"
        )

        retrieve_calls: list = []

        def my_retrieve(query, nd, top_k):
            retrieve_calls.append(query)
            return ["custom.md"]

        events = seed_from_queries(
            queries=["custom retrieve test"],
            notes_dir=notes_dir,
            top_k=1,
            backend="dummy",
            retrieve_fn=my_retrieve,
        )
        assert len(retrieve_calls) == 1
        assert len(events) == 1
        assert events[0]["note"] == "custom.md"

    def test_seed_subprocess_backend(self, tmp_path):
        """Subprocess backend is invoked and events are written correctly."""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        (notes_dir / "note.md").write_text("# Note\n\nSome content here.\n")

        # Create a stub script
        stub = tmp_path / "judge.sh"
        stub.write_text(
            '#!/bin/sh\ncat > /dev/null\n'
            'echo \'{"relevant": true, "rating": 7, "reason": "test"}\'\n'
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

        events = seed_from_queries(
            queries=["some query"],
            notes_dir=notes_dir,
            top_k=1,
            backend="subprocess",
            subprocess_command=str(stub),
        )
        assert len(events) == 1
        assert events[0]["type"] == "retrieval_hit"
        assert events[0]["synthetic"] is True
        assert events[0].get("rating_hint") == 7


# ---------------------------------------------------------------------------
# CLI: ledger signal seed / purge
# ---------------------------------------------------------------------------

class TestCLISignalSeed:
    def test_seed_from_queries_file(self, tmp_ledger, signals_path, tmp_path):
        """ledger signal seed --queries-file writes synthetic events."""
        from ledger.cli import main as cli_main

        notes_dir = tmp_ledger.ledger_notes_dir
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "deploy.md").write_text(
            "# Deploy\n\nHow to deploy the application.\n"
        )

        queries_file = tmp_path / "queries.txt"
        queries_file.write_text("deploy application\n# this is a comment\n\n")

        exit_code = cli_main(
            ["signal", "seed", "--queries-file", str(queries_file),
             "--backend", "dummy", "--top-k", "1"]
        )
        assert exit_code in (None, 0)

        written = read_signals(signals_path=signals_path)
        assert len(written) > 0
        for s in written:
            assert s.get("synthetic") is True

    def test_seed_no_source_fails(self, tmp_ledger):
        """seed with no --from-history and no --queries-file should fail."""
        from ledger.cli import main as cli_main

        with pytest.raises(SystemExit) as exc_info:
            cli_main(["signal", "seed", "--backend", "dummy"])
        assert exc_info.value.code != 0

    def test_seed_missing_queries_file_fails(self, tmp_ledger):
        """--queries-file pointing to non-existent file exits with error."""
        from ledger.cli import main as cli_main

        with pytest.raises(SystemExit) as exc_info:
            cli_main(["signal", "seed", "--queries-file", "/nonexistent/queries.txt"])
        assert exc_info.value.code != 0


class TestCLISignalPurge:
    def test_purge_synthetic_flag(self, tmp_ledger, signals_path):
        """ledger signal purge --synthetic removes synthetic events."""
        from ledger.cli import main as cli_main

        events = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q"},
            {"ts": "2026-06-01T10:01:00Z", "type": "affirmation",
             "note": "notes/b.md", "synthetic": True, "source": "llm_judge"},
        ]
        for e in events:
            append_signal_raw(e, signals_path)

        exit_code = cli_main(["signal", "purge", "--synthetic"])
        assert exit_code in (None, 0)

        remaining = read_signals(signals_path=signals_path)
        assert len(remaining) == 1
        assert not remaining[0].get("synthetic", False)

    def test_purge_without_flag_fails(self, tmp_ledger):
        """purge without --synthetic exits with error."""
        from ledger.cli import main as cli_main

        with pytest.raises(SystemExit) as exc_info:
            cli_main(["signal", "purge"])
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# signal_stats real_total
# ---------------------------------------------------------------------------

class TestSignalStatsRealTotal:
    def test_real_total_excludes_synthetic(self, tmp_ledger, signals_path):
        events = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q"},
            {"ts": "2026-06-01T10:01:00Z", "type": "retrieval_hit",
             "note": "notes/b.md", "query": "q",
             "synthetic": True, "source": "llm_judge"},
            {"ts": "2026-06-01T10:02:00Z", "type": "affirmation", "note": "notes/a.md"},
        ]
        for e in events:
            append_signal_raw(e, signals_path)

        stats = signal_stats(signals_path=signals_path)
        assert stats["total"] == 3
        assert stats["real_total"] == 2

    def test_real_total_all_real(self, tmp_ledger, signals_path):
        events = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "query": "q"},
            {"ts": "2026-06-01T10:01:00Z", "type": "affirmation", "note": "notes/a.md"},
        ]
        for e in events:
            append_signal_raw(e, signals_path)

        stats = signal_stats(signals_path=signals_path)
        assert stats["total"] == stats["real_total"] == 2

    def test_real_total_all_synthetic(self, tmp_ledger, signals_path):
        events = [
            {"ts": "2026-06-01T10:00:00Z", "type": "retrieval_hit",
             "note": "notes/a.md", "synthetic": True, "source": "llm_judge"},
        ]
        for e in events:
            append_signal_raw(e, signals_path)

        stats = signal_stats(signals_path=signals_path)
        assert stats["total"] == 1
        assert stats["real_total"] == 0
