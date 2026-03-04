from __future__ import annotations

import re
from pathlib import Path

from .models import Candidate
from .utils import count_words


STRATEGIC_VERB_EN = re.compile(r"(decide|design|investigate|figure out|revisit|plan|define|choose|evaluate|implement|prototype)", re.I)
STRATEGIC_VERB_NO = re.compile(r"(bestemme|designe|undersøke|finne ut|ta opp igjen|planlegge|definere|velge|evaluere|implementere|prototype)", re.I)

DECISION_SECTION_RE = re.compile(r"^\s*#+\s*(decision|decisions|avgjørelse|avgjørelser|outcome|conclusion)\b", re.I)
ACTION_SECTION_RE = re.compile(r"^\s*#+\s*(next steps|next actions|action items|actions|oppfølging|oppfolging|tiltak)\b", re.I)
CONCEPT_HEADING_RE_EN = re.compile(r"^#+\s*(?:Definition|Concept|Framework|Principle|Rule|Guideline|Pattern|Heuristic|Policy):\s*(.+)$", re.I | re.M)
CONCEPT_HEADING_RE_NO = re.compile(r"^#+\s*(?:Definisjon|Konsept|Rammeverk|Prinsipp|Regel|Retningslinje|Mønster|Heuristikk|Policy):\s*(.+)$", re.I | re.M)

MIN_DECISION_WORDS = 4
MIN_ACTION_WORDS = 4


def is_prompt_file(path_abs: Path) -> bool:
    p = str(path_abs).replace("\\", "/").lower()
    return "/prompts/" in p or "/copilot-custom-prompts/" in p


def is_journal_archive(path_abs: Path) -> bool:
    p = str(path_abs).replace("\\", "/").lower()
    return "/90-journal/archive/" in p


def is_journal_file(path_abs: Path) -> bool:
    p = str(path_abs).replace("\\", "/").lower()
    return "/90-journal/" in p


def is_meeting_like(path_abs: Path, content: str) -> bool:
    p = str(path_abs).replace("\\", "/").lower()
    if re.search(
        r"\\b(meeting|meetings|mom|minutes-of-meeting|minutes|møte|mote|samtale|call|check-?in|1-1|1:1|one-on-one|standup|sync|retro|retrospective|status)\\b",
        p,
        re.I,
    ):
        return True
    return bool(re.search(r"^(#|##)\s*(meeting|mom|minutes of meeting|minutes-of-meeting|minutes|møte|1:1|one-on-one|standup|sync|retro|retrospective|agenda|participants|attendees|deltakere)\b", content, re.I | re.M))


def loop_has_strong_marker(stmt: str) -> bool:
    return bool(
        re.search(r"(#openloop|#loop|@loop|@openloop)", stmt, re.I)
        or re.search(r"\b(20\d{2}-\d{2}-\d{2}|q[1-4]|uke\s?\d{1,2}|week\s?\d{1,2})\b", stmt, re.I)
    )


def loop_has_ownership_signal(stmt: str) -> bool:
    return bool(
        re.search(r"\b(i will|i'll|i should|i need to|we will|we should|we need to|owner|assignee|responsible)\b", stmt, re.I)
        or re.search(r"\b(jeg skal|jeg vil|jeg må|vi skal|vi må|eier|ansvar|ansvarlig)\b", stmt, re.I)
    )


def loop_has_decision_pending_signal(stmt: str) -> bool:
    return bool(
        re.search(r"\b(decide|decision|need to decide|open question|pending|tbd|unsure|not sure|to be decided)\b", stmt, re.I)
        or re.search(r"\b(må bestemme|avgjørelse|åpent spørsmål|uavklart|usikker|ikke sikker|må avklare)\b", stmt, re.I)
    )


def _count_matches(pattern: str, content: str) -> int:
    return len(re.findall(pattern, content, re.I | re.M))


def score_signal(content: str) -> tuple[int, int, float]:
    headings = len(re.findall(r"^#+\s+", content, re.M))
    tasks = len(re.findall(r"^\s*-\s*\[[ xX]\]\s+", content, re.M))

    strong_patterns = [
        r"\b(i prefer|i want you to|going forward)\b",
        r"\b(jeg foretrekker|jeg vil|fremover|fra nå av)\b",
        r"\b(decided|decision|we will|commit to|rule:)\b",
        r"\b(bestemt|besluttet|avgjort|avjørelse:|vi skal|forplikte|forpliktelse|regel:)\b",
        r"\b(open question|revisit|not sure yet|need to decide)\b",
        r"\b(åpent spørsmål|ta opp igjen|ikke sikker|må bestemme)\b",
        r"\b(definition|means:|framework|concept)\b",
        r"\b(definisjon|betyr:|rammeverk|konsept)\b",
    ]

    score = headings * 0.2 + tasks * 0.1
    for pattern in strong_patterns:
        if re.search(pattern, content, re.I):
            score += 3.0

    words = count_words(content)
    if words < 40:
        score -= 1.0

    return headings, tasks, score


def yield_hint(content: str) -> float:
    pref = _count_matches(r"^(?:-\s*)?(?:I prefer|I want you to|Going forward|From now on)\b", content)
    pref += _count_matches(r"^(?:-\s*)?(?:Jeg foretrekker|Jeg vil|Fremover|Fra nå av)\b", content)
    decision = _count_matches(r"^(?:-\s*)?(?:Decision:|Decided:|We will|We should|We must|We aim to|I decided)\b", content)
    decision += _count_matches(r"^(?:-\s*)?(?:Avgjørelse:|Bestemt:|Vi skal|Vi bør|Vi må|Vi har som mål|Jeg bestemte)\b", content)
    definition = _count_matches(r"^#+\s*(?:Definition|Concept|Framework|Principle|Rule|Guideline|Pattern|Heuristic|Policy):", content)
    definition += _count_matches(r"^#+\s*(?:Definisjon|Konsept|Rammeverk|Prinsipp|Regel|Retningslinje|Mønster|Heuristikk|Policy):", content)
    decision_heads = _count_matches(r"^\s*#+\s*(decision|decisions|avgjørelse|avgjørelser|outcome|conclusion)\b", content)
    action_heads = _count_matches(r"^\s*#+\s*(next steps|next actions|action items|actions|oppfølging|oppfolging|tiltak)\b", content)

    strategic_tasks = 0
    for match in re.finditer(r"^(?:-\s*)?\[\s*\]\s+(.+)$", content, re.I | re.M):
        stmt = match.group(1).strip()
        if STRATEGIC_VERB_EN.search(stmt) or STRATEGIC_VERB_NO.search(stmt):
            strategic_tasks += 1

    score = 0.0
    score += min(3, pref) * 2.0
    score += min(3, decision) * 2.0
    score += min(3, definition) * 2.0
    score += min(2, decision_heads) * 1.5
    score += min(2, action_heads) * 1.0
    score += min(5, strategic_tasks) * 1.5
    return score


def _extract_section_items(content: str, heading_re: re.Pattern[str]) -> list[str]:
    lines = content.splitlines()
    out: list[str] = []
    active = False
    for raw in lines:
        line = raw.rstrip("\n")
        if heading_re.search(line):
            active = True
            continue
        if re.search(r"^\s*#+\s+", line):
            active = False
            continue
        if not active:
            continue
        task = re.match(r"^\s*[-*+]\s*\[\s*\]\s+(.+)$", line)
        bullet = re.match(r"^\s*[-*+]\s+(.+)$", line)
        numbered = re.match(r"^\s*\d+\.\s+(.+)$", line)
        match = task or bullet or numbered
        if match:
            stmt = match.group(1).strip()
            if stmt:
                out.append(stmt)
    return out


def _modal_line_looks_actionable(stmt: str) -> bool:
    return bool(
        re.search(r"^(?:We should|We must|We aim to)\s+\w+\b(?:\s+\w+){1,}", stmt, re.I)
        or re.search(r"^(?:Vi bør|Vi må|Vi har som mål)\s+\w+\b(?:\s+\w+){1,}", stmt, re.I)
    )


def extract_candidates(content: str) -> list[Candidate]:
    candidates: list[Candidate] = []

    for match in re.finditer(r"^(?:-\s*)?(?:I prefer|I want you to|Going forward|From now on)\s+(.+)$", content, re.I | re.M):
        stmt = match.group(0).lstrip("- ").strip()
        candidates.append(Candidate(kind="pref", statement=stmt, confidence=0.92, tags=["imported", "preference"]))

    for match in re.finditer(r"^(?:-\s*)?(?:Jeg foretrekker|Jeg vil|Fremover|Fra nå av)\s+(.+)$", content, re.I | re.M):
        stmt = match.group(0).lstrip("- ").strip()
        candidates.append(Candidate(kind="pref", statement=stmt, confidence=0.92, tags=["imported", "preference"]))

    for match in re.finditer(r"^(?:-\s*)?(?:Decision:|Decided:|We will|I decided)\s+(.+)$", content, re.I | re.M):
        stmt = match.group(0).lstrip("- ").strip()
        if count_words(stmt) >= MIN_DECISION_WORDS:
            candidates.append(Candidate(kind="fact", statement=stmt, confidence=0.90, tags=["imported", "decision"]))

    for match in re.finditer(r"^(?:-\s*)?(?:We should|We must|We aim to)\s+(.+)$", content, re.I | re.M):
        stmt = match.group(0).lstrip("- ").strip()
        if count_words(stmt) >= MIN_DECISION_WORDS and _modal_line_looks_actionable(stmt):
            candidates.append(Candidate(kind="fact", statement=stmt, confidence=0.78, tags=["imported", "decision"]))

    for match in re.finditer(r"^(?:-\s*)?(?:Avgjørelse:|Bestemt:|Vi skal|Jeg bestemte)\s+(.+)$", content, re.I | re.M):
        stmt = match.group(0).lstrip("- ").strip()
        if count_words(stmt) >= MIN_DECISION_WORDS:
            candidates.append(Candidate(kind="fact", statement=stmt, confidence=0.90, tags=["imported", "decision"]))

    for match in re.finditer(r"^(?:-\s*)?(?:Vi bør|Vi må|Vi har som mål)\s+(.+)$", content, re.I | re.M):
        stmt = match.group(0).lstrip("- ").strip()
        if count_words(stmt) >= MIN_DECISION_WORDS and _modal_line_looks_actionable(stmt):
            candidates.append(Candidate(kind="fact", statement=stmt, confidence=0.78, tags=["imported", "decision"]))

    for match in CONCEPT_HEADING_RE_EN.finditer(content):
        title = match.group(1).strip()
        if len(title) >= 3:
            candidates.append(Candidate(kind="concept", statement=f"Definition: {title}", confidence=0.82, tags=["imported", "concept"]))

    for match in CONCEPT_HEADING_RE_NO.finditer(content):
        title = match.group(1).strip()
        if len(title) >= 3:
            candidates.append(Candidate(kind="concept", statement=f"Definisjon: {title}", confidence=0.82, tags=["imported", "concept"]))

    for stmt in _extract_section_items(content, DECISION_SECTION_RE):
        if count_words(stmt) >= MIN_DECISION_WORDS:
            candidates.append(Candidate(kind="fact", statement=stmt, confidence=0.80, tags=["imported", "decision"]))

    for stmt in _extract_section_items(content, ACTION_SECTION_RE):
        if count_words(stmt) >= MIN_ACTION_WORDS or STRATEGIC_VERB_EN.search(stmt) or STRATEGIC_VERB_NO.search(stmt):
            candidates.append(Candidate(kind="loop", statement=stmt, confidence=0.75, tags=["imported", "open_loop"]))

    for match in re.finditer(r"^(?:-\s*)?\[\s*\]\s+(.+)$", content, re.I | re.M):
        stmt = match.group(1).strip()
        if len(stmt) < 18:
            continue
        if not (STRATEGIC_VERB_EN.search(stmt) or STRATEGIC_VERB_NO.search(stmt)):
            continue
        candidates.append(Candidate(kind="loop", statement=stmt, confidence=0.75, tags=["imported", "open_loop"]))

    deduped: list[Candidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = f"{candidate.kind}::{candidate.statement.lower()}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped
