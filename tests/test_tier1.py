"""Tests for ledger/tier1.py — Tier-1 YAAMS fetch, RRF fusion, and CLI integration."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.tier1 import Tier1Result, fetch_yaams_results, fuse_results


# ---------------------------------------------------------------------------
# Pinned JSON shape from the plan / contract doc
# ---------------------------------------------------------------------------

_YAAMS_RESPONSE = {
    "query_id": "q_20260610T033047_929d71d1",
    "question": "akershus",
    "retrieval_ms": 191.8,
    "synthesis_ms": 0.0,
    "results": [
        {
            "id": "cons:987897b41f32abcd1234567890abcdef",
            "kind": "consolidation",
            "source": "imessage",
            "timestamp": "2025-10-20T07:07:03.164862+02:00",
            "sender": "AkershusRK",
            "subject": "Akershus 01",
            "content_preview": "imessage 2025-10-20 AkershusRK: call came in at 07:05",
            "score": 0.0355,
            "item_count": 4,
            "metadata": {"extra": "stuff"},
        },
        {
            "id": "abc0b01eec67aabbccddee00112233445566",
            "kind": "event",
            "source": "calendar_crayon",
            "timestamp": "2025-04-01T09:00:00.000000+02:00",
            "sender": "",
            "subject": "Akershus RK møte",
            "content_preview": "Røde Kors møte om akershus-distriktet",
            "score": 0.0210,
            "item_count": 1,
            "metadata": {},
        },
    ],
}


def _make_proc(stdout: str, returncode: int = 0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.returncode = returncode
    return proc


def _make_t2_payload(n: int = 2) -> dict:
    results = []
    for i in range(n):
        results.append({
            "rel_path": f"02_facts/fact__item_{i}.md",
            "path": f"02_facts/fact__item_{i}.md",
            "type": "fact",
            "title": f"Item {i}",
            "score": 0.9 - i * 0.1,
            "word_count": 100,
            "reasons": ["lexical"],
        })
    return {
        "query": "akershus",
        "scope": "all",
        "retrieval_mode": "legacy",
        "results": results,
    }


# ---------------------------------------------------------------------------
# FetchYaamsResultsTests
# ---------------------------------------------------------------------------

class FetchYaamsResultsTests(unittest.TestCase):

    def test_passes_tier_raw_no_parse_no_log_json_flags(self):
        """fetch_yaams_results must pass --tier raw --no-parse --no-log --json."""
        with patch("shutil.which", return_value="/usr/local/bin/yaams"), \
             patch("subprocess.run", return_value=_make_proc(json.dumps(_YAAMS_RESPONSE))) as mock_run:
            fetch_yaams_results("akershus", limit=5)
            cmd = mock_run.call_args[0][0]
        self.assertIn("--tier", cmd)
        self.assertIn("raw", cmd)
        self.assertIn("--no-parse", cmd)
        self.assertIn("--no-log", cmd)
        self.assertIn("--json", cmd)

    def test_passes_top_k_limit(self):
        """--top-k must be passed as str(limit)."""
        with patch("shutil.which", return_value="/usr/local/bin/yaams"), \
             patch("subprocess.run", return_value=_make_proc(json.dumps(_YAAMS_RESPONSE))) as mock_run:
            fetch_yaams_results("akershus", limit=7)
            cmd = mock_run.call_args[0][0]
        idx = cmd.index("--top-k")
        self.assertEqual(cmd[idx + 1], "7")

    def test_returns_empty_when_yaams_missing(self):
        """Binary not on PATH → ([], 'yaams_not_found')."""
        with patch("shutil.which", return_value=None):
            results, reason = fetch_yaams_results("x", limit=5)
        self.assertEqual(results, [])
        self.assertEqual(reason, "yaams_not_found")

    def test_returns_empty_on_timeout(self):
        """Subprocess timeout → ([], 'timeout')."""
        import subprocess as _subprocess
        with patch("shutil.which", return_value="/usr/local/bin/yaams"), \
             patch("subprocess.run", side_effect=_subprocess.TimeoutExpired(cmd="yaams", timeout=10)):
            results, reason = fetch_yaams_results("x", limit=5)
        self.assertEqual(results, [])
        self.assertEqual(reason, "timeout")

    def test_returns_empty_on_nonzero_exit(self):
        """Non-zero exit code N → ([], 'yaams_exit_N')."""
        with patch("shutil.which", return_value="/usr/local/bin/yaams"), \
             patch("subprocess.run", return_value=_make_proc("", returncode=3)):
            results, reason = fetch_yaams_results("x", limit=5)
        self.assertEqual(results, [])
        self.assertEqual(reason, "yaams_exit_3")

    def test_returns_empty_on_invalid_json(self):
        """Non-JSON stdout → ([], 'invalid_json')."""
        with patch("shutil.which", return_value="/usr/local/bin/yaams"), \
             patch("subprocess.run", return_value=_make_proc("not-json")):
            results, reason = fetch_yaams_results("x", limit=5)
        self.assertEqual(results, [])
        self.assertEqual(reason, "invalid_json")

    def test_min_score_filters_weak_hits(self):
        """Results below min_score threshold are dropped."""
        with patch("shutil.which", return_value="/usr/local/bin/yaams"), \
             patch("subprocess.run", return_value=_make_proc(json.dumps(_YAAMS_RESPONSE))):
            results, reason = fetch_yaams_results("akershus", limit=10, min_score=0.03)
        self.assertIsNone(reason)
        # score 0.0355 passes; 0.0210 is below 0.03
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].score, 0.0355)

    def test_tolerates_missing_fields(self):
        """Items with missing optional fields should degrade gracefully."""
        sparse = {
            "query_id": "q_x",
            "question": "test",
            "retrieval_ms": 10.0,
            "synthesis_ms": 0.0,
            "results": [
                {"id": "sparse_id_001", "score": 0.05},
            ],
        }
        with patch("shutil.which", return_value="/usr/local/bin/yaams"), \
             patch("subprocess.run", return_value=_make_proc(json.dumps(sparse))):
            results, reason = fetch_yaams_results("test", limit=5)
        self.assertIsNone(reason)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.id, "sparse_id_001")
        self.assertEqual(r.kind, "")
        self.assertEqual(r.source, "")
        self.assertEqual(r.content, "")
        self.assertAlmostEqual(r.score, 0.05)

    def test_display_id_format(self):
        """display_id must be 'yaams:' + first 24 chars of id."""
        r = Tier1Result(
            id="cons:987897b41f32abcd1234567890abcdef",
            kind="consolidation",
            source="imessage",
            timestamp="2025-10-20T07:07:03Z",
            content="test content",
            subject="",
            sender="",
            score=0.5,
        )
        self.assertEqual(r.display_id, "yaams:cons:987897b41f32abcd123")

    def test_content_preview_mapped_to_content(self):
        """content_preview field must be mapped to Tier1Result.content."""
        with patch("shutil.which", return_value="/usr/local/bin/yaams"), \
             patch("subprocess.run", return_value=_make_proc(json.dumps(_YAAMS_RESPONSE))):
            results, _ = fetch_yaams_results("akershus", limit=10)
        self.assertIn("imessage 2025-10-20", results[0].content)


# ---------------------------------------------------------------------------
# FuseResultsTests
# ---------------------------------------------------------------------------

class FuseResultsTests(unittest.TestCase):

    def _make_tier1(self, n: int = 2) -> list[Tier1Result]:
        items = []
        for i in range(n):
            items.append(Tier1Result(
                id=f"tier1_id_{i:04d}",
                kind="imessage",
                source="imessage",
                timestamp="2025-10-20T07:00:00Z",
                content=f"message content {i}",
                subject=f"Subject {i}",
                sender="Alice",
                score=0.05 - i * 0.01,
            ))
        return items

    def test_combines_both_tiers_sorted_by_rrf(self):
        """Merged results must be sorted by RRF score descending."""
        t2 = _make_t2_payload(3)
        t1 = self._make_tier1(2)
        result = fuse_results(t2, t1)
        rffs = [r["rrf"] for r in result["results"]]
        self.assertEqual(rffs, sorted(rffs, reverse=True))

    def test_tier2_boost_reranks_tier2_above_tier1(self):
        """A large tier2_boost must push all tier-2 results above tier-1."""
        t2 = _make_t2_payload(2)
        t1 = self._make_tier1(2)
        result = fuse_results(t2, t1, tier2_boost=1.0)
        tiers = [r["_tier"] for r in result["results"]]
        # All tier-2 entries should precede tier-1 entries.
        first_t1 = next(i for i, t in enumerate(tiers) if t == 1)
        for j in range(first_t1):
            self.assertEqual(tiers[j], 2)

    def test_preserves_tier2_metadata(self):
        """Tier-2 result dicts should retain their existing fields."""
        t2 = _make_t2_payload(1)
        t2["results"][0]["title"] = "My Important Fact"
        result = fuse_results(t2, [])
        t2_out = [r for r in result["results"] if r["_tier"] == 2]
        self.assertEqual(t2_out[0]["title"], "My Important Fact")

    def test_attaches_fusion_block_with_unavailable_reason(self):
        """fusion block must contain counts, rrf_k, boost, and unavailable_reason."""
        t2 = _make_t2_payload(2)
        t1 = self._make_tier1(1)
        result = fuse_results(t2, t1, tier2_boost=0.5, rrf_k=30, unavailable_reason="timeout")
        fusion = result["fusion"]
        self.assertEqual(fusion["tier2_count"], 2)
        self.assertEqual(fusion["tier1_count"], 1)
        self.assertEqual(fusion["rrf_k"], 30)
        self.assertAlmostEqual(fusion["tier2_boost"], 0.5)
        self.assertEqual(fusion["unavailable_reason"], "timeout")

    def test_accepts_empty_tier1(self):
        """fuse_results with no tier-1 results must return tier-2-only output."""
        t2 = _make_t2_payload(2)
        result = fuse_results(t2, [])
        tiers = {r["_tier"] for r in result["results"]}
        self.assertEqual(tiers, {2})
        self.assertEqual(result["fusion"]["tier1_count"], 0)


# ---------------------------------------------------------------------------
# CrossTierCliTests
# ---------------------------------------------------------------------------

def _capture(fn, *args, **kwargs):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rv = fn(*args, **kwargs)
    return rv, out.getvalue(), err.getvalue()


class CrossTierCliTests(unittest.TestCase):

    def _make_args(self, **overrides):
        defaults = dict(
            text="akershus",
            scope="all",
            limit=5,
            retrieval_mode="legacy",
            embed_backend="local",
            embed_model=None,
            view="context",
            json=False,
            bundle=False,
            as_of=None,
            prf=False,
            pick=False,
            include_tier1=False,
            tier1_limit=10,
            tier1_boost=0.0,
            tier1_min_score=None,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _mock_rank_query(self, n: int = 2):
        """Return a minimal payload dict that handle_query_command can consume."""
        results = []
        for i in range(n):
            results.append({
                "rel_path": f"02_facts/fact__{i}.md",
                "path": f"02_facts/fact__{i}.md",
                "type": "fact",
                "title": f"Note {i}",
                "score": 0.8 - i * 0.1,
                "word_count": 50,
                "reasons": ["lexical"],
                "statement": "",
                "snippet": "",
                "tags": [],
                "source": "user",
                "updated": "2026-01-01",
                "confidence": 0.9,
                "scope": "all",
                "disclosure_level": "",
            })
        from ledger.retrieval_types import RetrievalResult, TimingInfo
        return RetrievalResult(
            query="akershus",
            scope="all",
            retrieval_mode="legacy",
            effective_retrieval_mode="legacy",
            results=results,
            timing=TimingInfo(),
            candidate_pool_size=n,
            indexed_pool_size=n,
            prefilter_size=n,
            shortlist_size=n,
            progressive_top_n=0,
            expanded_tokens=[],
            expansion_events=[],
            semantic=None,
        )

    def test_plain_query_unaffected_without_flag(self):
        """ledger query without --include-tier1 must work exactly as before."""
        import ledger.cli as cli

        mock_payload = self._mock_rank_query(2)

        with patch.object(cli, "rank_query", return_value=mock_payload), \
             patch.object(cli, "_capture_retrieval_miss"):
            _, out, err = _capture(cli.handle_query_command, self._make_args())

        # No tier-1 warnings, no fusion block in output.
        self.assertNotIn("tier-1", err.lower())
        self.assertNotIn("fusion", out)
        self.assertNotIn("Tier 1", out)
        # Normal output present.
        self.assertIn("query:", out)

    def test_include_tier1_fuses_and_prints_groups(self):
        """--include-tier1 must print Tier 2 / Tier 1 result groups."""
        import ledger.cli as cli

        mock_payload = self._mock_rank_query(2)

        with patch.object(cli, "rank_query", return_value=mock_payload), \
             patch.object(cli, "_capture_retrieval_miss"), \
             patch("shutil.which", return_value="/usr/local/bin/yaams"), \
             patch("subprocess.run", return_value=_make_proc(json.dumps(_YAAMS_RESPONSE))):
            _, out, err = _capture(
                cli.handle_query_command,
                self._make_args(include_tier1=True),
            )

        self.assertIn("Tier 2 results", out)
        self.assertIn("Tier 1 results", out)
        self.assertIn("fusion:", out)

    def test_include_tier1_json_has_tier_field_on_every_result(self):
        """--include-tier1 --json must emit _tier on every result."""
        import ledger.cli as cli

        mock_payload = self._mock_rank_query(2)

        with patch.object(cli, "rank_query", return_value=mock_payload), \
             patch.object(cli, "_capture_retrieval_miss"), \
             patch("shutil.which", return_value="/usr/local/bin/yaams"), \
             patch("subprocess.run", return_value=_make_proc(json.dumps(_YAAMS_RESPONSE))):
            _, out, _ = _capture(
                cli.handle_query_command,
                self._make_args(include_tier1=True, json=True),
            )

        data = json.loads(out)
        self.assertIn("fusion", data)
        for r in data["results"]:
            self.assertIn("_tier", r, f"_tier missing on result: {r}")

    def test_warning_on_stderr_when_yaams_unavailable(self):
        """When yaams is not found, a warning must go to stderr."""
        import ledger.cli as cli

        mock_payload = self._mock_rank_query(1)

        with patch.object(cli, "rank_query", return_value=mock_payload), \
             patch.object(cli, "_capture_retrieval_miss"), \
             patch("shutil.which", return_value=None):
            _, out, err = _capture(
                cli.handle_query_command,
                self._make_args(include_tier1=True),
            )

        self.assertIn("tier-1 unavailable", err)
        self.assertIn("yaams_not_found", err)
        # Should still show tier-2 results.
        self.assertIn("query:", out)


if __name__ == "__main__":
    unittest.main()
