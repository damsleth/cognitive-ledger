"""Contradiction scan engine for Cognitive Ledger (Gap B).

Detects when a note contradicts an existing note using a local NLI classifier
and resolves the contradiction via one of three actions:

  auto-supersede  — contradiction above auto_threshold + unambiguous temporal
                    ordering + no confidence inversion → supersede(old, new)
  review          — contradiction in [review_threshold, auto_threshold), or
                    high score with ambiguous ordering / confidence inversion,
                    or lang:no note below 0.95 → conflict note in 00_inbox
  ignore          — score below review_threshold → no action

NORWEGIAN CAVEAT
----------------
The NLI model (MoritzLaurer/mDeBERTa-v3-base-mnli-xnli) is trained on MNLI +
XNLI covering 15 languages.  Norwegian is *not* one of them.  NLI accuracy on
Norwegian notes is therefore unvalidated.  A stricter auto threshold
(contradiction_auto_threshold_lang_no, default 0.95) is applied when either
note has lang:no.  This caveat appears in nli.py as well.

HARD RULES (not config-overridable)
------------------------------------
1. Identity notes (01_identity/) are NEVER auto-superseded; always review.
2. Re-running on an already-resolved pair is a no-op (idempotency via state
   file + superseded_by check).
3. Duplicate inbox conflict records for the same pair are not created.

STATE FILE
----------
notes/08_indices/contradiction_state.json tracks which (note, content_hash)
pairs have been scanned and which (note_a, note_b) pairs have been resolved.
The file has a version field; version mismatch causes a clean re-scan.
"""

from __future__ import annotations

import datetime as dt
import enum
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ledger.config import get_config
from ledger.layout import NOTE_LAYOUTS
from ledger.parsing.frontmatter import parse_frontmatter_text, serialize_frontmatter
from ledger.io import safe_write_text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_FILE_VERSION = 1
STATE_FILENAME = "contradiction_state.json"

# NLI-compatible note types: only types that carry factual claims.
# loops / preferences are excluded because they are intentionally evolving.
_SCAN_NOTE_TYPES = frozenset({"facts", "identity", "goals", "concepts"})

# Folder subdir names for note types that may be auto-superseded.
# identity is excluded here: it goes to review only (hard rule 1).
_AUTO_SUPERSEDE_SUBTYPES = frozenset({"facts", "goals", "concepts"})

_IDENTITY_SUBDIR = "01_identity"

_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now_utc() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _to_iso(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime(_TIMESTAMP_FMT)


def _from_iso(s: str) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.strptime(s, _TIMESTAMP_FMT).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Decision enum
# ---------------------------------------------------------------------------

class Decision(enum.Enum):
    """Action produced by decide() for a contradiction pair."""
    IGNORE = "ignore"
    REVIEW = "review"
    SUPERSEDE = "supersede"  # old_note superseded by new_note


# ---------------------------------------------------------------------------
# Pair facts dataclass (pure, IO-free)
# ---------------------------------------------------------------------------

@dataclass
class PairFacts:
    """All facts about a (candidate_note, neighbor_note) pair needed to decide.

    Designed so that decide() is a pure function with no IO.

    Attributes:
        contradiction_score: Bidirectional NLI contradiction probability in [0,1].
        is_candidate_newer: True when candidate's temporal anchor is strictly
            later than neighbor's (different timestamps); None when ambiguous
            (equal or both absent).
        candidate_confidence: Confidence of the candidate note (0-1).
        neighbor_confidence: Confidence of the neighbor note (0-1).
        neighbor_is_identity: True when neighbor lives in 01_identity/.
        candidate_is_identity: True when candidate lives in 01_identity/.
        either_lang_no: True when either note has lang:no (or mixed lang:no).
        neighbor_already_superseded: True when neighbor.superseded_by is set
            (stale note that was already replaced — should be skipped).
    """
    contradiction_score: float
    is_candidate_newer: bool | None  # None = ambiguous
    candidate_confidence: float
    neighbor_confidence: float
    neighbor_is_identity: bool
    candidate_is_identity: bool
    either_lang_no: bool
    neighbor_already_superseded: bool = False


# ---------------------------------------------------------------------------
# Pure decision function
# ---------------------------------------------------------------------------

def decide(
    pair: PairFacts,
    *,
    auto_threshold: float,
    review_threshold: float,
    auto_threshold_lang_no: float,
    protect_higher_confidence: bool,
) -> Decision:
    """Decide what action to take for a contradiction pair.

    Pure function (no IO, no config access) — all thresholds/flags are explicit
    parameters so the full decision matrix can be tested without side effects.

    Decision rules (in priority order):
    1. Already superseded neighbor → IGNORE.
    2. Score < review_threshold → IGNORE.
    3. Either note is identity → REVIEW (hard rule 1).
    4. Choose effective auto threshold: lang:no notes use the stricter value.
    5. Score >= effective auto threshold AND unambiguous ordering:
       a. If protect_higher_confidence AND neighbor is OLDER AND has strictly
          higher confidence than candidate → downgrade to REVIEW.
       b. Otherwise → SUPERSEDE.
    6. Score in [review_threshold, effective_auto_threshold) OR ambiguous
       ordering → REVIEW.
    """
    if pair.neighbor_already_superseded:
        return Decision.IGNORE

    score = pair.contradiction_score

    if score < review_threshold:
        return Decision.IGNORE

    # Hard rule: identity notes always go to review
    if pair.neighbor_is_identity or pair.candidate_is_identity:
        return Decision.REVIEW

    effective_auto = auto_threshold_lang_no if pair.either_lang_no else auto_threshold

    if score >= effective_auto and pair.is_candidate_newer is True:
        # Candidate is strictly newer than neighbor. Check confidence guard.
        if (
            protect_higher_confidence
            and pair.neighbor_confidence > pair.candidate_confidence
        ):
            return Decision.REVIEW
        return Decision.SUPERSEDE

    return Decision.REVIEW


# ---------------------------------------------------------------------------
# Note body extraction (strip frontmatter)
# ---------------------------------------------------------------------------

def _extract_body(text: str) -> str:
    """Return the note body with frontmatter stripped.

    Falls back to the full text if no frontmatter boundary is found.
    """
    _, body = parse_frontmatter_text(text)
    return body.strip()


# ---------------------------------------------------------------------------
# Temporal ordering
# ---------------------------------------------------------------------------

def _temporal_anchor(fm: dict[str, Any]) -> dt.datetime | None:
    """Return the note's temporal anchor for ordering.

    Priority: valid_from > created > None.
    """
    for key in ("valid_from", "created"):
        raw = str(fm.get(key, "")).strip()
        if raw and raw not in ("null", "~", "None", ""):
            ts = _from_iso(raw)
            if ts is not None:
                return ts
            # Try date-only
            try:
                d = dt.date.fromisoformat(raw.split("T")[0])
                return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
            except ValueError:
                pass
    return None


def _is_lang_no(fm: dict[str, Any]) -> bool:
    """Return True if the note's lang field is 'no' or starts with 'no'."""
    lang = str(fm.get("lang", "")).strip().lower()
    return lang == "no" or lang.startswith("no-")


def _is_superseded(fm: dict[str, Any]) -> bool:
    """Return True if superseded_by is set to a non-null value."""
    val = str(fm.get("superseded_by", "")).strip()
    return val not in ("", "null", "~", "None")


# ---------------------------------------------------------------------------
# Note type detection from path
# ---------------------------------------------------------------------------

def _note_type_from_path(abs_path: Path) -> str | None:
    """Return the ledger note type name for the given path, or None."""
    subdir = abs_path.parent.name
    for name, layout in NOTE_LAYOUTS.items():
        if layout.subdir == subdir:
            return name
    return None


def _is_identity_path(abs_path: Path) -> bool:
    return abs_path.parent.name == _IDENTITY_SUBDIR


def _type_compatible(type_a: str | None, type_b: str | None) -> bool:
    """True when two note types can meaningfully contradict each other.

    Same-type pairs are always compatible.  Cross-type pairs are not (a
    preference cannot contradict a goal in a meaningful NLI sense).
    """
    return type_a is not None and type_a == type_b


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

@dataclass
class ContradictionState:
    """In-memory view of the scan state file.

    scanned_hashes: {rel_path -> content_hash}
        Tracks which version of each note has been scanned. When a note's
        hash changes, it is re-scanned as a candidate.
    resolved_pairs: {canonical_pair_key -> action}
        Tracks already-resolved (note_a, note_b) pairs to prevent re-processing.
        canonical_pair_key = "notes/a.md|notes/b.md" (alphabetical order).
    """
    scanned_hashes: dict[str, str] = field(default_factory=dict)
    resolved_pairs: dict[str, str] = field(default_factory=dict)


def _pair_key(ref_a: str, ref_b: str) -> str:
    """Canonical sorted pair key for idempotency tracking."""
    a, b = sorted([ref_a, ref_b])
    return f"{a}|{b}"


def load_state(indices_dir: Path) -> ContradictionState:
    """Load the contradiction state file; return empty state on any error."""
    state_path = indices_dir / STATE_FILENAME
    if not state_path.is_file():
        return ContradictionState()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if data.get("version") != STATE_FILE_VERSION:
            return ContradictionState()
        return ContradictionState(
            scanned_hashes=data.get("scanned_hashes", {}),
            resolved_pairs=data.get("resolved_pairs", {}),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return ContradictionState()


def save_state(indices_dir: Path, state: ContradictionState) -> None:
    """Persist the contradiction state file atomically."""
    indices_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "version": STATE_FILE_VERSION,
        "scanned_hashes": state.scanned_hashes,
        "resolved_pairs": state.resolved_pairs,
    }
    state_path = indices_dir / STATE_FILENAME
    safe_write_text(state_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Semantic neighbor retrieval
# ---------------------------------------------------------------------------

def _get_semantic_neighbors(
    candidate_text: str,
    candidate_rel_path: str,
    candidate_type: str | None,
    k: int,
    ledger_notes_dir: Path,
    candidate_scope: str | None = None,
) -> list[dict[str, Any]]:
    """Return up to k semantic neighbors of the candidate note.

    Uses the existing semantic index (embeddings.semantic_score_map).
    Returns a list of item dicts with at least rel_path, abs_path, scope,
    type, cosine_similarity.

    Only notes from the SAME scope as the candidate are returned (or notes
    with scope "all", which is the cross-scope sentinel).  When
    candidate_scope is None or "all", no scope filtering is applied.

    Returns [] when the semantic index is unavailable (callers handle this).
    """
    try:
        from ledger.embeddings import semantic_score_map
        from ledger.config import get_config
    except ImportError:
        return []

    config = get_config()
    backend = getattr(config, "embed_backend", "local")
    result = semantic_score_map(
        candidate_text,
        target="ledger",
        backend=backend,
        model=None,
    )
    if not result.get("available", False):
        return []

    items = result.get("results", [])
    neighbors: list[dict[str, Any]] = []

    # Normalise candidate scope for comparison.
    cand_scope_norm = (candidate_scope or "").strip().lower() or None

    for item in items:
        rel_path = str(item.get("rel_path", ""))
        if not rel_path:
            continue
        # Skip self
        if rel_path == candidate_rel_path:
            continue
        # Type compatibility filter
        item_type = item.get("type")
        # item "type" in the embeddings index is the label (e.g. "fact"),
        # not the name (e.g. "facts") — normalise
        item_name = _label_to_name(item_type)
        if not _type_compatible(candidate_type, item_name):
            continue

        # Scope filter: only match notes in the same scope.
        # Scope "all" is a cross-scope sentinel — never filtered out.
        # When the candidate has no scope (or scope "all"), skip filtering.
        if cand_scope_norm and cand_scope_norm != "all":
            item_scope = str(item.get("scope", "")).strip().lower()
            if item_scope and item_scope != "all" and item_scope != cand_scope_norm:
                continue

        neighbors.append(item)
        if len(neighbors) >= k:
            break

    return neighbors


_LABEL_TO_NAME: dict[str, str] = {
    layout.label: name for name, layout in NOTE_LAYOUTS.items()
}


def _label_to_name(label: str | None) -> str | None:
    if label is None:
        return None
    return _LABEL_TO_NAME.get(str(label).lower())


# ---------------------------------------------------------------------------
# Conflict note writer (00_inbox)
# ---------------------------------------------------------------------------

_CONFLICT_TAG = "conflict"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", text.lower()).strip("_")[:40]


def _make_conflict_note_filename(rel_a: str, rel_b: str, now: dt.datetime) -> str:
    """Generate a unique filename for a conflict inbox note."""
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    stem_a = Path(rel_a).stem[:20]
    stem_b = Path(rel_b).stem[:20]
    return f"conflict__{ts}__{_slug(stem_a)}__{_slug(stem_b)}.md"


def _build_conflict_note(
    rel_a: str,
    rel_b: str,
    score: float,
    body_a: str,
    body_b: str,
    now: dt.datetime,
) -> str:
    """Produce a lint-clean 00_inbox note describing the contradiction."""
    ts = _to_iso(now)
    tags_str = f"[{_CONFLICT_TAG}, nli, review]"
    fm_dict = {
        "created": ts,
        "updated": ts,
        "tags": [_CONFLICT_TAG, "nli", "review"],
        "confidence": round(score, 3),
        "source": "inferred",
        "scope": "all",
        "lang": "en",
    }
    fm_text = serialize_frontmatter(fm_dict)

    # Truncate bodies to keep the inbox note readable
    _max_body = 600
    body_a_short = body_a[:_max_body] + ("…" if len(body_a) > _max_body else "")
    body_b_short = body_b[:_max_body] + ("…" if len(body_b) > _max_body else "")

    content = (
        f"{fm_text}\n"
        f"# Contradiction review: {Path(rel_a).stem} vs {Path(rel_b).stem}\n\n"
        f"NLI contradiction score: **{score:.3f}**\n\n"
        f"Human review required — auto-supersession was not triggered.\n\n"
        f"## Note A\n\n"
        f"`{rel_a}`\n\n"
        f"```\n{body_a_short}\n```\n\n"
        f"## Note B\n\n"
        f"`{rel_b}`\n\n"
        f"```\n{body_b_short}\n```\n\n"
        f"## Decision\n\n"
        f"- [ ] A supersedes B\n"
        f"- [ ] B supersedes A\n"
        f"- [ ] Both are correct (different contexts)\n"
        f"- [ ] Both are wrong / outdated\n"
    )
    return content


def _existing_conflict_note(
    pair_key: str,
    inbox_dir: Path,
) -> Path | None:
    """Return the path of an already-written conflict note for this pair, or None."""
    if not inbox_dir.is_dir():
        return None
    for path in inbox_dir.glob("conflict__*.md"):
        text = path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter_text(text)
        # Body contains the pair's rel_paths; check membership
        parts = pair_key.split("|")
        if all(p in text for p in parts):
            return path
    return None


# ---------------------------------------------------------------------------
# Scan result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PairResult:
    """Result for a single (candidate, neighbor) pair."""
    candidate_ref: str
    neighbor_ref: str
    contradiction_score: float
    decision: Decision
    action_taken: str  # "superseded", "conflict_noted", "skipped", "dry_run_*"


@dataclass
class ScanResult:
    """Aggregate result of a full contradiction scan run."""
    enabled: bool
    semantic_index_available: bool
    nli_available: bool
    candidates_scanned: int
    pairs_evaluated: int
    supersessions: int
    conflict_notes: int
    ignored: int
    dry_run: bool
    pair_results: list[PairResult] = field(default_factory=list)
    missing_embed_index: bool = False
    nli_unavailable_reason: str = ""


# ---------------------------------------------------------------------------
# Core scan function
# ---------------------------------------------------------------------------

def run_contradiction_scan(
    *,
    apply: bool = False,
    _pipeline_fn: Callable[[str, str], list[dict[str, Any]]] | None = None,
    _neighbor_fn: (
        Callable[..., list[dict[str, Any]]] | None
    ) = None,
) -> ScanResult:
    """Run the NLI contradiction scan over all new/updated notes.

    Parameters
    ----------
    apply:
        False (--check): score pairs, report intended actions, write nothing.
        True  (--apply): execute supersessions and write conflict inbox notes.
    _pipeline_fn:
        Optional injection seam for tests — forwarded to nli.contradiction_score.
        When set, no NLI model is loaded.
    _neighbor_fn:
        Optional injection seam for tests — replaces _get_semantic_neighbors.
        Signature: (candidate_text, candidate_rel_path, candidate_type, k,
                    ledger_notes_dir, candidate_scope=None) -> list[item_dict].

    Returns
    -------
    ScanResult with per-pair details and aggregate counts.
    """
    config = get_config()

    if not config.contradiction_enabled:
        return ScanResult(
            enabled=False,
            semantic_index_available=False,
            nli_available=False,
            candidates_scanned=0,
            pairs_evaluated=0,
            supersessions=0,
            conflict_notes=0,
            ignored=0,
            dry_run=not apply,
        )

    ledger_notes_dir = config.ledger_notes_dir
    indices_dir = ledger_notes_dir / "08_indices"
    inbox_dir = ledger_notes_dir / "00_inbox"

    # Load scan state
    state = load_state(indices_dir)

    # Collect candidates: notes that are new or have changed since last scan
    candidates: list[tuple[str, Path, str, dict[str, Any], str]] = []
    # (rel_path, abs_path, content_hash, frontmatter_dict, body_text)

    for note_type in sorted(_SCAN_NOTE_TYPES):
        layout = NOTE_LAYOUTS.get(note_type)
        if layout is None:
            continue
        folder = ledger_notes_dir / layout.subdir
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name == ".gitkeep":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            h = _content_hash(text)
            rel = f"notes/{layout.subdir}/{path.name}"
            fm, body = parse_frontmatter_text(text)
            if _is_superseded(fm):
                # Already superseded notes are skipped as candidates
                continue
            if state.scanned_hashes.get(rel) == h:
                # Unchanged since last scan — skip
                continue
            candidates.append((rel, path, h, fm, body))

    # Check semantic index availability (only when using real neighbor fn).
    # Tests inject _neighbor_fn to bypass the semantic index entirely.
    semantic_available = True
    missing_embed_index = False
    if _neighbor_fn is None and candidates:
        try:
            from ledger.embeddings import semantic_score_map
            backend = getattr(config, "embed_backend", "local")
            probe = semantic_score_map("probe", target="ledger", backend=backend, model=None)
            if not probe.get("available", False):
                semantic_available = False
                missing_embed_index = True
        except Exception:
            semantic_available = False
            missing_embed_index = True

    scan_result = ScanResult(
        enabled=True,
        semantic_index_available=semantic_available,
        nli_available=True,
        candidates_scanned=len(candidates),
        pairs_evaluated=0,
        supersessions=0,
        conflict_notes=0,
        ignored=0,
        dry_run=not apply,
        missing_embed_index=missing_embed_index,
    )

    if missing_embed_index:
        # Report that embedding build is needed; do not crash
        return scan_result

    from ledger.nli import contradiction_score as nli_score

    neighbor_fn = _neighbor_fn or _get_semantic_neighbors

    for candidate_ref, candidate_abs, content_h, cand_fm, cand_body in candidates:
        cand_type = _note_type_from_path(candidate_abs)
        cand_is_identity = _is_identity_path(candidate_abs)
        cand_lang_no = _is_lang_no(cand_fm)
        cand_anchor = _temporal_anchor(cand_fm)
        cand_confidence = _confidence(cand_fm)
        cand_scope = str(cand_fm.get("scope", "")).strip().lower() or None

        neighbors = neighbor_fn(
            cand_body,
            candidate_ref,
            cand_type,
            config.contradiction_neighbors_k,
            ledger_notes_dir,
            cand_scope,
        )

        for neighbor_item in neighbors:
            neighbor_rel = str(neighbor_item.get("rel_path", ""))
            if not neighbor_rel:
                continue

            # Pair idempotency check
            pk = _pair_key(candidate_ref, neighbor_rel)
            if pk in state.resolved_pairs:
                continue

            # Resolve neighbor abs path
            neighbor_abs = _rel_path_to_abs(neighbor_rel, ledger_notes_dir)
            if neighbor_abs is None or not neighbor_abs.exists():
                continue

            try:
                neighbor_text = neighbor_abs.read_text(encoding="utf-8")
            except OSError:
                continue
            neighbor_fm, neighbor_body = parse_frontmatter_text(neighbor_text)

            # Skip already-superseded neighbors
            neighbor_superseded = _is_superseded(neighbor_fm)

            neighbor_is_identity = _is_identity_path(neighbor_abs)
            neighbor_lang_no = _is_lang_no(neighbor_fm)
            neighbor_anchor = _temporal_anchor(neighbor_fm)
            neighbor_confidence = _confidence(neighbor_fm)

            # Bidirectional NLI score
            nli_kwargs: dict[str, Any] = {
                "model_name": config.contradiction_model,
                "device": getattr(config, "embed_device", "auto"),
            }
            if _pipeline_fn is not None:
                nli_kwargs["_pipeline_fn"] = _pipeline_fn
            try:
                score = nli_score(cand_body, neighbor_body, **nli_kwargs)
            except Exception as exc:
                scan_result.nli_available = False
                scan_result.nli_unavailable_reason = str(exc)
                return scan_result

            scan_result.pairs_evaluated += 1

            # Temporal ordering
            is_cand_newer: bool | None
            if cand_anchor is not None and neighbor_anchor is not None:
                if cand_anchor > neighbor_anchor:
                    is_cand_newer = True
                elif cand_anchor < neighbor_anchor:
                    is_cand_newer = False
                else:
                    is_cand_newer = None  # same timestamp → ambiguous
            else:
                is_cand_newer = None  # one or both absent → ambiguous

            pair_facts = PairFacts(
                contradiction_score=score,
                is_candidate_newer=is_cand_newer,
                candidate_confidence=cand_confidence,
                neighbor_confidence=neighbor_confidence,
                neighbor_is_identity=neighbor_is_identity,
                candidate_is_identity=cand_is_identity,
                either_lang_no=cand_lang_no or neighbor_lang_no,
                neighbor_already_superseded=neighbor_superseded,
            )

            decision = decide(
                pair_facts,
                auto_threshold=config.contradiction_auto_threshold,
                review_threshold=config.contradiction_review_threshold,
                auto_threshold_lang_no=config.contradiction_auto_threshold_lang_no,
                protect_higher_confidence=config.contradiction_protect_higher_confidence,
            )

            if decision == Decision.IGNORE:
                scan_result.ignored += 1
                scan_result.pair_results.append(PairResult(
                    candidate_ref=candidate_ref,
                    neighbor_ref=neighbor_rel,
                    contradiction_score=score,
                    decision=decision,
                    action_taken="skipped",
                ))
                if apply:
                    # Mark IGNORE pairs as seen so we don't re-evaluate on later
                    # --apply runs either.  Not written during --check so that a
                    # --check run never permanently marks pairs as processed.
                    state.resolved_pairs[pk] = Decision.IGNORE.value
                continue

            if decision == Decision.SUPERSEDE:
                # Determine old/new: candidate is newer, neighbor is older
                # is_candidate_newer is True when we reach SUPERSEDE
                old_ref = neighbor_rel
                new_ref = candidate_ref

                if not apply:
                    scan_result.supersessions += 1
                    scan_result.pair_results.append(PairResult(
                        candidate_ref=candidate_ref,
                        neighbor_ref=neighbor_rel,
                        contradiction_score=score,
                        decision=decision,
                        action_taken="dry_run_supersede",
                    ))
                    # Do NOT write to state during --check; would prevent --apply
                    # from acting on the pair.
                    continue

                # Execute supersession via the canonical helper
                from ledger.bitemporal import supersede

                try:
                    # Pass as_of=cand_anchor so valid_to on the old note is set
                    # to the newer note's valid_from rather than today's date.
                    # This preserves the historically correct bitemporal boundary.
                    supersede(
                        old_ref,
                        new_ref,
                        as_of=cand_anchor,
                        reason=f"nli_contradiction:{score:.3f}",
                    )
                    scan_result.supersessions += 1
                    scan_result.pair_results.append(PairResult(
                        candidate_ref=candidate_ref,
                        neighbor_ref=neighbor_rel,
                        contradiction_score=score,
                        decision=decision,
                        action_taken="superseded",
                    ))
                    state.resolved_pairs[pk] = Decision.SUPERSEDE.value

                    # Emit contradiction_flagged signal
                    _emit_signal_safe(
                        "contradiction_flagged",
                        note=old_ref,
                        detail=json.dumps(
                            {"new": new_ref, "score": round(score, 3), "action": "superseded"},
                            ensure_ascii=False,
                        ),
                    )
                except Exception:
                    # Supersession failed (e.g. file moved) — degrade to review.
                    # Write a conflict note so the pair is not silently lost, and
                    # do NOT add the pair to resolved_pairs so it can be retried
                    # if the underlying file issue is fixed.
                    scan_result.pair_results.append(PairResult(
                        candidate_ref=candidate_ref,
                        neighbor_ref=neighbor_rel,
                        contradiction_score=score,
                        decision=decision,
                        action_taken="supersede_failed",
                    ))
                    # Degrade: write a conflict note to 00_inbox so the pair
                    # surfaces for human review rather than being silently dropped.
                    existing_fb = _existing_conflict_note(pk, inbox_dir)
                    if existing_fb is None:
                        now_fb = _now_utc()
                        filename_fb = _make_conflict_note_filename(
                            candidate_ref, neighbor_rel, now_fb
                        )
                        inbox_dir.mkdir(parents=True, exist_ok=True)
                        safe_write_text(
                            inbox_dir / filename_fb,
                            _build_conflict_note(
                                candidate_ref,
                                neighbor_rel,
                                score,
                                cand_body,
                                neighbor_body,
                                now_fb,
                            ),
                        )
                        _append_conflict_timeline(
                            f"notes/00_inbox/{filename_fb}",
                            candidate_ref,
                            neighbor_rel,
                            score,
                        )
                        scan_result.conflict_notes += 1
                continue

            # REVIEW path
            if not apply:
                scan_result.conflict_notes += 1
                scan_result.pair_results.append(PairResult(
                    candidate_ref=candidate_ref,
                    neighbor_ref=neighbor_rel,
                    contradiction_score=score,
                    decision=decision,
                    action_taken="dry_run_conflict",
                ))
                # Do NOT write to state during --check; would prevent --apply
                # from acting on the pair.
                continue

            # Check idempotency: is there already a conflict note for this pair?
            existing = _existing_conflict_note(pk, inbox_dir)
            if existing is not None:
                state.resolved_pairs[pk] = Decision.REVIEW.value
                scan_result.pair_results.append(PairResult(
                    candidate_ref=candidate_ref,
                    neighbor_ref=neighbor_rel,
                    contradiction_score=score,
                    decision=decision,
                    action_taken="conflict_already_exists",
                ))
                continue

            # Write conflict note to inbox
            now = _now_utc()
            filename = _make_conflict_note_filename(candidate_ref, neighbor_rel, now)
            inbox_dir.mkdir(parents=True, exist_ok=True)
            conflict_path = inbox_dir / filename
            conflict_content = _build_conflict_note(
                candidate_ref,
                neighbor_rel,
                score,
                cand_body,
                neighbor_body,
                now,
            )
            safe_write_text(conflict_path, conflict_content)
            _append_conflict_timeline(
                f"notes/00_inbox/{filename}",
                candidate_ref,
                neighbor_rel,
                score,
            )
            scan_result.conflict_notes += 1
            scan_result.pair_results.append(PairResult(
                candidate_ref=candidate_ref,
                neighbor_ref=neighbor_rel,
                contradiction_score=score,
                decision=decision,
                action_taken="conflict_noted",
            ))
            state.resolved_pairs[pk] = Decision.REVIEW.value

            # Emit signal
            _emit_signal_safe(
                "contradiction_flagged",
                note=candidate_ref,
                detail=json.dumps(
                    {
                        "neighbor": neighbor_rel,
                        "score": round(score, 3),
                        "action": "conflict_noted",
                        "inbox": f"notes/00_inbox/{filename}",
                    },
                    ensure_ascii=False,
                ),
            )

        # After processing all neighbors, update scanned hash (--apply only).
        # During --check we must not persist any state so that a subsequent
        # --apply run sees the full unfiltered candidate set.
        if apply:
            state.scanned_hashes[candidate_ref] = content_h

    # Persist state only when actually applying changes.
    if apply:
        save_state(indices_dir, state)

    return scan_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _confidence(fm: dict[str, Any]) -> float:
    """Return a lint-tolerant confidence value for decision guards."""
    try:
        return _clamp(float(fm.get("confidence", 0.5)))
    except (TypeError, ValueError):
        return 0.5


def _rel_path_to_abs(rel_path: str, ledger_notes_dir: Path) -> Path | None:
    """Convert a notes/... logical path to an absolute path."""
    if rel_path.startswith("notes/"):
        return ledger_notes_dir / rel_path[len("notes/"):]
    return ledger_notes_dir / rel_path


def _emit_signal_safe(signal_type: str, *, note: str, detail: str) -> None:
    """Emit a signal without raising — best-effort only."""
    try:
        from ledger.signals import append_signal
        append_signal(signal_type, note=note, detail=detail)
    except Exception:
        pass


def _append_conflict_timeline(
    conflict_ref: str,
    candidate_ref: str,
    neighbor_ref: str,
    score: float,
) -> None:
    """Append a best-effort timeline entry for generated conflict notes."""
    try:
        from ledger.embeddings import append_timeline_entry

        append_timeline_entry(
            action="created",
            rel_path=conflict_ref,
            description=(
                f"nli contradiction review for {candidate_ref} vs "
                f"{neighbor_ref} (score={score:.3f})"
            ),
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def cmd_sleep_contradictions(*, apply: bool = False) -> int:
    """CLI entry point for `ledger sleep contradictions --check | --apply`.

    When contradiction_enabled=false, prints a notice and returns 0.
    When the semantic index is unavailable, reports clearly and returns 0.
    """
    config = get_config()

    if not config.contradiction_enabled:
        print("=== ledger sleep contradictions ===")
        print("")
        print("contradiction_enabled=false — scan skipped.")
        print("Set contradiction_enabled: true in ~/.config/ledger/config.yaml")
        print("to activate NLI-based contradiction detection.")
        return 0

    mode_label = "--apply" if apply else "--check (dry run — use --apply to write)"
    print(f"=== ledger sleep contradictions {mode_label} ===")
    print("")

    result = run_contradiction_scan(apply=apply)

    if result.missing_embed_index:
        print("ERROR: semantic index unavailable.")
        print("Run `ledger embed build` first, then retry the contradiction scan.")
        return 0  # Exit 0 per spec: report cleanly, no crash

    if not result.nli_available:
        print("ERROR: NLI classifier unavailable.")
        if result.nli_unavailable_reason:
            print(result.nli_unavailable_reason)
        print("Install embeddings dependencies and download the configured model,")
        print("or set contradiction_enabled: false until the model is available.")
        return 0  # Maintenance should report cleanly, not crash.

    print(f"Candidates scanned: {result.candidates_scanned}")
    print(f"Pairs evaluated:    {result.pairs_evaluated}")
    print(f"Supersessions:      {result.supersessions} ({'written' if apply else 'would write'})")
    print(f"Conflict notes:     {result.conflict_notes} ({'written to 00_inbox' if apply else 'would write'})")
    print(f"Ignored:            {result.ignored}")
    print("")

    if result.pair_results:
        print("Per-pair results:")
        for pr in result.pair_results:
            cand = Path(pr.candidate_ref).name
            neigh = Path(pr.neighbor_ref).name
            print(
                f"  {cand} vs {neigh}: "
                f"score={pr.contradiction_score:.3f} "
                f"decision={pr.decision.value} "
                f"action={pr.action_taken}"
            )

    if not apply:
        print("")
        print("Dry run — no files written. Re-run with --apply to execute.")

    return 0
