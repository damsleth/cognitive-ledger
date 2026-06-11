"""Tests for grounded answer synthesis (plan 45)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.synthesize.answer import (
    AnswerResult,
    build_synthesis_prompt,
    parse_citation_ids,
    parse_structured_answer,
    synthesize_answer,
)
from ledger.synthesize.llm import DummyAdapter, LLMResponse


def _results():
    return [
        {"rel_path": "notes/02_facts/a.md", "type": "fact", "updated": "2026-06-01",
         "statement": "The deploy runs nightly.", "body": "The deploy runs nightly via cron."},
        {"rel_path": "notes/06_concepts/b.md", "type": "concept", "updated": "2026-05-01",
         "statement": "CI uses GitHub Actions.", "body": "CI uses GitHub Actions."},
    ]


class _StubAdapter:
    backend_name = "stub"
    model_name = "m1"

    def __init__(self, text):
        self._text = text

    def complete(self, prompt, *, max_tokens=1024, temperature=0.0):
        return LLMResponse(text=self._text, backend=self.backend_name, model=self.model_name)


class TestPrompt:
    def test_sources_are_numbered_with_paths(self):
        prompt = build_synthesis_prompt("how do deploys work?", _results())
        assert "[1] fact notes/02_facts/a.md" in prompt
        assert "[2] concept notes/06_concepts/b.md" in prompt
        assert "how do deploys work?" in prompt

    def test_private_fence_stripped_before_prompt(self):
        results = [{"rel_path": "notes/02_facts/secret.md", "type": "fact",
                    "statement": "public part", "body": "public part <private>SECRET</private> tail"}]
        prompt = build_synthesis_prompt("q", results)
        assert "SECRET" not in prompt
        assert "public part" in prompt

    def test_voice_dna_injected_and_omitted(self):
        voice = {"voice": "terse, dry, lowercase"}
        with_voice = build_synthesis_prompt("q", _results(), voice_dna=voice)
        assert "terse, dry, lowercase" in with_voice
        without = build_synthesis_prompt("q", _results(), voice_dna=None)
        assert "Write in this voice" not in without

    def test_empty_results_renders_placeholder(self):
        assert "(no sources retrieved)" in build_synthesis_prompt("q", [])


class TestParsing:
    def test_citation_ids_map_to_note_paths(self):
        ranks, paths = parse_citation_ids("Deploys are nightly [1]. CI is GHA [2].", _results())
        assert ranks == [1, 2]
        assert paths == ["notes/02_facts/a.md", "notes/06_concepts/b.md"]

    def test_citation_ids_dedupe_and_bounds(self):
        ranks, paths = parse_citation_ids("[1] [1] [9]", _results())
        assert ranks == [1]  # dup collapsed, out-of-range 9 dropped
        assert paths == ["notes/02_facts/a.md"]

    def test_parse_structured_answer_full(self):
        text = ("ANSWER:\nDeploys run nightly [1].\n\n"
                "CONFIDENCE: high\nstated directly\n\nGAPS:\n- none")
        body, conf, reason, gaps = parse_structured_answer(text)
        assert body == "Deploys run nightly [1]."
        assert conf == "high"
        assert reason == "stated directly"
        assert gaps == []

    def test_parse_structured_answer_tolerant_missing_gaps(self):
        text = "ANSWER:\nSome answer.\n\nCONFIDENCE: medium\nmaybe"
        body, conf, reason, gaps = parse_structured_answer(text)
        assert body == "Some answer."
        assert conf == "medium"
        assert gaps == []

    def test_parse_structured_answer_missing_answer_header(self):
        body, conf, reason, gaps = parse_structured_answer("Just prose, no headers.")
        assert body == "Just prose, no headers."
        assert conf == "unknown"

    def test_parse_gaps_bullets(self):
        text = "ANSWER:\nx\n\nGAPS:\n- missing cost data\n- no timeline"
        _, _, _, gaps = parse_structured_answer(text)
        assert gaps == ["missing cost data", "no timeline"]


class TestSynthesize:
    def test_dummy_backend_offline_deterministic(self):
        result = synthesize_answer("q", _results(), DummyAdapter())
        assert isinstance(result, AnswerResult)
        assert result.backend == "dummy"
        assert result.confidence == "low"
        # deterministic: same inputs → same output
        again = synthesize_answer("q", _results(), DummyAdapter())
        assert again.answer == result.answer

    def test_end_to_end_with_stub(self):
        text = ("ANSWER:\nDeploys run nightly [1]; CI is GHA [2].\n\n"
                "CONFIDENCE: high\nboth stated\n\nGAPS:\n- none")
        result = synthesize_answer("q", _results(), _StubAdapter(text))
        assert result.answer_body.startswith("Deploys run nightly")
        assert result.cited_paths == ["notes/02_facts/a.md", "notes/06_concepts/b.md"]
        assert result.confidence == "high"
        assert result.to_dict()["cited_paths"] == result.cited_paths
