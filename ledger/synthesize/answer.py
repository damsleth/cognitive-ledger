"""Grounded answer synthesis over ledger notes (plan 45).

Ported from YAAMS and adapted: sources are ledger notes, citations map to note
paths (``rel_path``), and the configured Voice DNA is injected so the prose
matches Kim's tone. Source bodies are private-scrubbed before entering the
prompt (defense in depth — candidate build already strips fences).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ledger.parsing import strip_private_tags
from ledger.synthesize.llm import LLMAdapter, LLMResponse

CITATION_RE = re.compile(r"\[(\d+)\]")
_ANSWER_HEADER_RE = re.compile(r"(?im)^\s*ANSWER\s*:\s*")
_CONFIDENCE_HEADER_RE = re.compile(r"(?im)^\s*CONFIDENCE\s*:\s*")
_GAPS_HEADER_RE = re.compile(r"(?im)^\s*GAPS\s*:\s*")
_VALID_CONFIDENCE = {"high", "medium", "low"}


SYNTH_PROMPT_TEMPLATE = """You are answering a question using ONLY the SOURCES below. Each source is a numbered ledger note.

Rules:
- Cite the source numbers you used inline as [n]. Cite at most the relevant ones.
- Do NOT use facts that are not in the SOURCES.
- If the SOURCES do not contain enough information, say so explicitly. Do not invent.
- Keep the answer brief - 1-3 short paragraphs unless the question demands more.
- Quote selectively. Do not echo whole sources.
- Match the language of the question.{voice_lead}
- Output exactly the three sections below, in order, using the headers verbatim.

Output format:
ANSWER:
<answer with [n] citations>

CONFIDENCE: <high | medium | low>
<one short sentence on why>

GAPS:
- <bullet of what the sources did not cover>
- (or "none" on a single line if nothing is missing)

QUESTION:
{question}

SOURCES:
{sources}

ANSWER:"""


@dataclass
class AnswerResult:
    question: str
    answer: str
    answer_body: str = ""
    cited_ranks: list[int] = field(default_factory=list)
    cited_paths: list[str] = field(default_factory=list)
    confidence: str = "unknown"
    confidence_reason: str = ""
    gaps: list[str] = field(default_factory=list)
    backend: str = ""
    model: str | None = None
    raw_response: LLMResponse | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer_body": self.answer_body,
            "cited_paths": list(self.cited_paths),
            "cited_ranks": list(self.cited_ranks),
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
            "gaps": list(self.gaps),
            "backend": self.backend,
            "model": self.model,
        }


def _get(result: Any, key: str, default: Any = "") -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def _render_source(rank: int, result: Any) -> str:
    rel_path = str(_get(result, "rel_path", "") or _get(result, "path", ""))
    note_type = str(_get(result, "type", "") or "")
    updated = str(_get(result, "updated", "") or "")
    header = f"[{rank}] {note_type} {rel_path}"
    if updated:
        header += f" ({updated})"
    body = str(_get(result, "statement", "") or "").strip()
    full_body = str(_get(result, "body", "") or "").strip()
    if full_body and full_body != body:
        body = f"{body}\n{full_body}" if body else full_body
    body = strip_private_tags(body).strip()
    if len(body) > 1500:
        body = body[:1500] + " ..."
    return f"{header}\n{body}"


def _render_voice_lead(voice_dna: dict[str, Any] | None) -> str:
    if not voice_dna:
        return ""
    # Prefer an explicit short descriptor; fall back to a trimmed summary.
    for key in ("voice", "summary", "tone", "style"):
        val = voice_dna.get(key)
        if isinstance(val, str) and val.strip():
            return f"\n- Write in this voice: {val.strip()[:400]}"
    return ""


def build_synthesis_prompt(
    question: str,
    results: Sequence[Any],
    *,
    voice_dna: dict[str, Any] | None = None,
) -> str:
    blocks = [_render_source(rank, r) for rank, r in enumerate(results, 1)]
    return SYNTH_PROMPT_TEMPLATE.format(
        question=question.strip(),
        sources="\n\n".join(blocks) if blocks else "(no sources retrieved)",
        voice_lead=_render_voice_lead(voice_dna),
    )


def parse_citation_ids(answer_text: str, results: Sequence[Any]) -> tuple[list[int], list[str]]:
    ranks: list[int] = []
    paths: list[str] = []
    seen: set[int] = set()
    for match in CITATION_RE.finditer(answer_text or ""):
        n = int(match.group(1))
        if n in seen:
            continue
        seen.add(n)
        if 1 <= n <= len(results):
            ranks.append(n)
            paths.append(str(_get(results[n - 1], "rel_path", "") or _get(results[n - 1], "path", "")))
    return ranks, paths


def parse_structured_answer(text: str) -> tuple[str, str, str, list[str]]:
    """Split LLM output into (answer_body, confidence, confidence_reason, gaps).

    Tolerant of missing sections, missing ANSWER marker, and surrounding
    whitespace.
    """
    if not text or not text.strip():
        return "", "unknown", "", []
    body = text.strip()

    conf_match = _CONFIDENCE_HEADER_RE.search(body)
    gaps_match = _GAPS_HEADER_RE.search(body)

    end_of_answer = len(body)
    if conf_match:
        end_of_answer = min(end_of_answer, conf_match.start())
    if gaps_match:
        end_of_answer = min(end_of_answer, gaps_match.start())

    answer_section = body[:end_of_answer]
    ans_header = _ANSWER_HEADER_RE.search(answer_section)
    answer_body = answer_section[ans_header.end():].strip() if ans_header else answer_section.strip()

    if conf_match:
        end = len(body)
        if gaps_match and gaps_match.start() > conf_match.end():
            end = gaps_match.start()
        confidence, confidence_reason = _split_confidence(body[conf_match.end():end].strip())
    else:
        confidence, confidence_reason = "unknown", ""

    gaps: list[str] = []
    if gaps_match:
        gaps = _parse_gaps(body[gaps_match.end():].strip())

    return answer_body, confidence, confidence_reason, gaps


def _split_confidence(block: str) -> tuple[str, str]:
    if not block:
        return "unknown", ""
    first_line, _, rest = block.partition("\n")
    level = first_line.strip().lower().rstrip(".")
    reason = rest.strip()
    if level not in _VALID_CONFIDENCE:
        parts = first_line.strip().split(None, 1)
        if parts and parts[0].lower().rstrip(".") in _VALID_CONFIDENCE:
            level = parts[0].lower().rstrip(".")
            reason_inline = parts[1] if len(parts) > 1 else ""
            reason = (reason_inline + ("\n" + reason if reason else "")).strip()
        else:
            level = "unknown"
            reason = (first_line + ("\n" + reason if reason else "")).strip()
    return level, reason


def _parse_gaps(block: str) -> list[str]:
    if not block:
        return []
    if block.strip().lower() in ("none", "(none)", "- none", "no gaps"):
        return []
    cleaned: list[str] = []
    for line in block.splitlines():
        stripped = line.strip().lstrip("-*").strip()
        if stripped and stripped.lower() != "none":
            cleaned.append(stripped)
    return cleaned


def synthesize_answer(
    question: str,
    results: Sequence[Any],
    adapter: LLMAdapter,
    *,
    voice_dna: dict[str, Any] | None = None,
    max_tokens: int = 600,
    temperature: float = 0.0,
) -> AnswerResult:
    prompt = build_synthesis_prompt(question, results, voice_dna=voice_dna)
    response = adapter.complete(prompt, max_tokens=max_tokens, temperature=temperature)
    answer_body, confidence, confidence_reason, gaps = parse_structured_answer(response.text)
    ranks, paths = parse_citation_ids(response.text, results)
    return AnswerResult(
        question=question,
        answer=response.text,
        answer_body=answer_body,
        cited_ranks=ranks,
        cited_paths=paths,
        confidence=confidence,
        confidence_reason=confidence_reason,
        gaps=gaps,
        backend=response.backend,
        model=response.model,
        raw_response=response,
    )


def answer(
    question: str,
    *,
    scope: str = "all",
    limit: int = 5,
    as_of=None,
    backend: str | None = None,
    use_voice: bool = True,
    now_dt=None,
    max_tokens: int = 600,
) -> AnswerResult:
    """Retrieve sources for *question* and synthesize a grounded, cited answer."""
    from ledger.config import get_config
    from ledger.query import rank_query
    from ledger.synthesize.llm import adapter_from_ledger_config

    cfg = get_config()
    # Inject the working embedding resolvers (the query.py defaults reference a
    # stale ledger.semantic.resolve_model symbol; the CLI injects these too).
    from ledger import semantic as semantic_lib

    def _load_embeddings_module():
        return semantic_lib.load_embeddings_module()

    def _resolve_embed_model(backend, embed_model):
        return semantic_lib.resolve_embed_model(
            backend, embed_model, load_embeddings_module_fn=_load_embeddings_module
        )

    payload = rank_query(
        query=question,
        scope=scope,
        limit=limit,
        aliases_path=cfg.aliases_path,
        retrieval_mode=cfg.retrieval_mode,
        now_dt=now_dt,
        as_of=as_of,
        load_embeddings_module=_load_embeddings_module,
        resolve_embed_model=_resolve_embed_model,
    )
    results = list(getattr(payload, "results", []))

    voice_dna = None
    if use_voice:
        try:
            from ledger.voice import get_voice_profile
            voice_dna = get_voice_profile(cfg.ledger_notes_dir)
        except Exception:
            voice_dna = None

    adapter = adapter_from_ledger_config(cfg, backend=backend)
    return synthesize_answer(
        question, results, adapter, voice_dna=voice_dna, max_tokens=max_tokens
    )
