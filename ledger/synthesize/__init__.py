"""Grounded answer synthesis for Cognitive Ledger (plan 45).

Ports the proven design from YAAMS: a pluggable LLM adapter, a structured
ANSWER/CONFIDENCE/GAPS prompt, and ``[n]`` citation extraction mapped back to
the cited note paths. Default backend is ``dummy`` (deterministic, offline,
test-safe); ``claude``/``ollama``/``subprocess`` send the prompt out, so source
bodies are private-scrubbed before they enter the prompt.
"""

from __future__ import annotations

from ledger.synthesize.answer import (
    AnswerResult,
    answer,
    build_synthesis_prompt,
    parse_citation_ids,
    parse_structured_answer,
)
from ledger.synthesize.llm import (
    LLMAdapter,
    LLMResponse,
    adapter_from_ledger_config,
)

__all__ = [
    "AnswerResult",
    "answer",
    "build_synthesis_prompt",
    "parse_citation_ids",
    "parse_structured_answer",
    "LLMAdapter",
    "LLMResponse",
    "adapter_from_ledger_config",
]
