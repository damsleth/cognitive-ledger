"""Candidates operations for retrieval."""

from __future__ import annotations

from typing import Any
from ledger.layout import NOTE_LAYOUTS
from pathlib import Path
from ledger.retrieval_types import RetrievalCandidate
from ledger.scoring import canonical_scope
from ledger.io.hashing import digest
from ledger.parsing import extract_link_tokens
from ledger.parsing import extract_title
from ledger.parsing import first_checkbox
from ledger.parsing import first_content_line
from ledger.layout import logical_path
from ledger.layout import note_type_dir
from ledger.parsing import parse_frontmatter_text
from ledger.parsing import parse_sections
from ledger.layout import resolve_path
from ledger.parsing import tokenize
from ledger.hubs import unreviewed_count
from ledger.ranking.prior import confidence_value
from ledger.ranking.settings import _cfg, parse_ts


def _note_types() -> dict[str, dict[str, Any]]:
    config = _cfg()
    return {
        name: {
            "dir": note_type_dir(config.ledger_notes_dir, name),
            "label": info["label"],
        }
        for name, info in config.note_types.items()
    }



def _candidate_label(note_type: str) -> str:
    return str(_note_types()[note_type]["label"])



def read_note_for_retrieval(path: Path) -> tuple[dict[str, Any], str]:
    """Read a note file for retrieval."""
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter_text(text)



def _sha256_text(text: str) -> str:
    return f"sha256:{digest(text.encode('utf-8'))}"



def build_attention_tokens(
    title: str,
    statement: str,
    snippet_source: str,
    tags: list[str],
    scope: str,
    status: str,
    next_action: str,
    body: str,
    slug: str,
) -> set[str]:
    """Build a richer token capsule used by compressed_attention retrieval mode."""
    capsule_parts = [
        title,
        statement,
        snippet_source,
        " ".join(tags),
        scope,
        status,
        next_action,
        slug,
    ]
    attention_tokens = tokenize(" ".join(part for part in capsule_parts if part))
    attention_tokens |= extract_link_tokens(body)
    return attention_tokens



def _candidate_from_parts(
    path: Path,
    note_type: str,
    frontmatter: dict[str, Any],
    body: str,
) -> RetrievalCandidate:
    """Build retrieval candidate from parsed note content."""
    from ledger.parsing import strip_private_tags
    body = strip_private_tags(body)
    sections = parse_sections(body)
    title = extract_title(body) or path.stem.replace("_", " ")

    if note_type == "loops":
        statement = "\n".join(sections.get("question or task", [])).strip()
        if not statement:
            statement = first_content_line(body)
        next_action_text = "\n".join(sections.get("next action", [])).strip()
        if not next_action_text:
            next_action_text = body
        next_action = first_checkbox(next_action_text)
        status = str(frontmatter.get("status", "open")).strip().lower() or "open"
    else:
        statement = "\n".join(sections.get("statement", [])).strip() or first_content_line(body)
        next_action = ""
        status = ""

    tags_raw = frontmatter.get("tags", [])
    if isinstance(tags_raw, list):
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    elif isinstance(tags_raw, str) and tags_raw.strip():
        tags = [tags_raw.strip()]
    else:
        tags = []

    scope = canonical_scope(frontmatter.get("scope", ""))
    updated_str = str(frontmatter.get("updated", "")).strip()
    updated_ts = parse_ts(updated_str)
    created_str = str(frontmatter.get("created", "")).strip()
    created_ts = parse_ts(created_str) if created_str else None
    confidence = confidence_value(frontmatter)
    source = str(frontmatter.get("source", "")).strip().lower()
    provenance = str(frontmatter.get("provenance", "")).strip().lower()
    via = str(frontmatter.get("via", "")).strip().lower()

    cfg = _cfg()
    resolved = path.resolve()
    rel_path = logical_path(
        resolved,
        ledger_root=cfg.ledger_root,
        ledger_notes_dir=cfg.ledger_notes_dir,
    )
    slug = path.stem

    searchable_text = " ".join([title, statement, body, " ".join(tags), slug])
    note_tokens = tokenize(searchable_text)

    tag_tokens: set[str] = set()
    for tag in tags:
        tag_tokens |= tokenize(tag.replace("-", " ").replace("_", " "))

    snippet_source = statement or first_content_line(body)
    if note_type == "loops" and next_action:
        snippet_source = f"{statement} Next: {next_action}".strip()
    attention_tokens = build_attention_tokens(
        title=title,
        statement=statement,
        snippet_source=snippet_source,
        tags=tags,
        scope=scope,
        status=status,
        next_action=next_action,
        body=body,
        slug=slug,
    )

    # Capture bitemporal validity fields (optional; absent on legacy notes).
    def _str_or_empty(val: Any) -> str:
        s = str(val or "").strip()
        return "" if s in ("null", "~", "None", "[]") else s

    unreviewed = unreviewed_count(frontmatter, body)
    if unreviewed:
        # Unreviewed agent output is a model's reading, not a stated fact:
        # cap it at the lowest trust class whatever the note claims.
        provenance = "inferred"

    valid_from = _str_or_empty(frontmatter.get("valid_from", ""))
    valid_to = _str_or_empty(frontmatter.get("valid_to", ""))
    superseded_by = _str_or_empty(frontmatter.get("superseded_by", ""))

    return RetrievalCandidate(
        path=str(path.resolve()),
        rel_path=str(rel_path),
        type=_candidate_label(note_type),
        title=title,
        statement=statement,
        body=body,
        updated=updated_str,
        updated_ts=updated_ts,
        confidence=confidence,
        source=source,
        scope=scope,
        status=status,
        tags=tags,
        note_tokens=note_tokens,
        tag_tokens=tag_tokens,
        attention_tokens=attention_tokens,
        snippet=snippet_source,
        has_next_action_checkbox=bool(next_action),
        word_count=len(body.split()),
        valid_from=valid_from,
        valid_to=valid_to,
        superseded_by=superseded_by,
        created_ts=created_ts,
        provenance=provenance,
        via=via,
        unreviewed=unreviewed,
    )



def candidate_from_note(path: Path, note_type: str) -> RetrievalCandidate:
    """Build retrieval candidate from a note path."""
    frontmatter, body = read_note_for_retrieval(path)
    return _candidate_from_parts(path, note_type, frontmatter, body)



def _candidate_to_json(candidate: RetrievalCandidate) -> dict[str, Any]:
    """Serialize candidate to JSON-friendly payload."""
    persisted_path = candidate.rel_path or logical_path(
        candidate.path,
        ledger_root=_cfg().ledger_root,
        ledger_notes_dir=_cfg().ledger_notes_dir,
    ).as_posix()
    payload: dict[str, Any] = {
        "path": persisted_path,
        "rel_path": candidate.rel_path,
        "type": candidate.type,
        "title": candidate.title,
        "statement": candidate.statement,
        "body": candidate.body,
        "updated": candidate.updated,
        "confidence": candidate.confidence,
        "source": candidate.source,
        "scope": candidate.scope,
        "status": candidate.status,
        "tags": list(candidate.tags),
        "note_tokens": sorted(candidate.note_tokens),
        "tag_tokens": sorted(candidate.tag_tokens),
        "attention_tokens": sorted(candidate.attention_tokens),
        "snippet": candidate.snippet,
        "has_next_action_checkbox": candidate.has_next_action_checkbox,
        "word_count": candidate.word_count,
    }
    # Persist bitemporal fields only when set (keeps index lean for legacy notes).
    if candidate.valid_from:
        payload["valid_from"] = candidate.valid_from
    if candidate.valid_to:
        payload["valid_to"] = candidate.valid_to
    if candidate.superseded_by:
        payload["superseded_by"] = candidate.superseded_by
    # Provenance inputs — persist only when set (keeps index lean for legacy notes).
    if candidate.provenance:
        payload["provenance"] = candidate.provenance
    if candidate.via:
        payload["via"] = candidate.via
    if candidate.unreviewed:
        payload["unreviewed"] = candidate.unreviewed
    # Persist created ISO string when available (created_ts is derived from it,
    # mirroring the pattern used for updated / updated_ts).
    if candidate.created_ts is not None:
        payload["created"] = candidate.created_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    return payload



def _candidate_from_json(candidate_json: dict[str, Any]) -> RetrievalCandidate:
    """Deserialize candidate from JSON-friendly payload."""
    updated = str(candidate_json.get("updated", ""))
    created = str(candidate_json.get("created", ""))
    raw_path = str(candidate_json.get("path", "") or "")
    rel_path = str(candidate_json.get("rel_path", "") or "")
    resolved_candidate_path = raw_path
    if raw_path and not Path(raw_path).is_absolute():
        resolved_candidate_path = str(
            resolve_path(
                raw_path,
                ledger_root=_cfg().ledger_root,
                ledger_notes_dir=_cfg().ledger_notes_dir,
            )
        )
    elif not raw_path and rel_path:
        resolved_candidate_path = str(
            resolve_path(
                rel_path,
                ledger_root=_cfg().ledger_root,
                ledger_notes_dir=_cfg().ledger_notes_dir,
            )
        )
    try:
        confidence = float(candidate_json.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return RetrievalCandidate(
        path=resolved_candidate_path,
        rel_path=rel_path,
        type=str(candidate_json.get("type", "")),
        title=str(candidate_json.get("title", "")),
        statement=str(candidate_json.get("statement", "")),
        body=str(candidate_json.get("body", "")),
        updated=updated,
        updated_ts=parse_ts(updated),
        confidence=confidence,
        source=str(candidate_json.get("source", "")),
        scope=str(candidate_json.get("scope", "")),
        status=str(candidate_json.get("status", "")),
        tags=list(candidate_json.get("tags", []) or []),
        note_tokens=set(candidate_json.get("note_tokens", [])),
        tag_tokens=set(candidate_json.get("tag_tokens", [])),
        attention_tokens=set(candidate_json.get("attention_tokens", [])),
        snippet=str(candidate_json.get("snippet", "")),
        has_next_action_checkbox=bool(candidate_json.get("has_next_action_checkbox", False)),
        word_count=int(candidate_json.get("word_count", 0) or 0),
        # Bitemporal fields — absent in older indexes; default to empty string.
        valid_from=str(candidate_json.get("valid_from", "") or ""),
        valid_to=str(candidate_json.get("valid_to", "") or ""),
        superseded_by=str(candidate_json.get("superseded_by", "") or ""),
        # created_ts is recomputed from the persisted "created" ISO string,
        # mirroring the updated / updated_ts pattern.
        created_ts=parse_ts(created) if created else None,
        # Provenance inputs — absent in older indexes; default to empty string.
        provenance=str(candidate_json.get("provenance", "") or ""),
        via=str(candidate_json.get("via", "") or ""),
        unreviewed=int(candidate_json.get("unreviewed", 0) or 0),
    )



def _build_archive_candidates() -> list[RetrievalCandidate]:
    """Build retrieval candidates from notes/09_archive for --as-of queries.

    Archive notes are excluded from the normal corpus (not in CORE_NOTE_TYPES)
    so they need to be loaded separately when --as-of widens the candidate pool.
    Returns an empty list if the archive directory does not exist.
    """
    from ledger.layout import typed_archive_notes

    config = _cfg()
    candidates: list[RetrievalCandidate] = []
    for note_path in typed_archive_notes(config.ledger_notes_dir):
        try:
            frontmatter, body = read_note_for_retrieval(note_path)
        except Exception:
            continue
        # Determine note type from filename prefix.
        stem = note_path.stem
        note_type = "facts"  # default / fallback type for archived notes
        for nt in _note_types():
            nl = NOTE_LAYOUTS.get(nt)
            if nl and stem.startswith(nl.prefix):
                note_type = nt
                break
        candidate = _candidate_from_parts(note_path, note_type, frontmatter, body)
        candidates.append(candidate)
    return candidates

