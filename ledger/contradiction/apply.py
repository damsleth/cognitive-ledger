from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from ledger.config import get_config
from ledger.io import safe_write_text
from ledger.layout import INBOX_SUBDIR
from ledger.layout import INDICES_SUBDIR
from ledger.parsing.frontmatter import parse_frontmatter_text
from ledger.parsing.frontmatter import serialize_frontmatter
from ledger.parsing.privacy import strip_private_tags
from pathlib import Path
from typing import Any
from typing import Callable
import datetime as dt
import json
import re
from .policy import Decision, PairFacts, _TIMESTAMP_FMT, _confidence, _is_identity_path, _is_lang_no, _is_superseded, _temporal_anchor, decide
from .state import _pair_key, load_state, save_state
from .discovery import _get_semantic_neighbors, _note_type_from_path, _rel_path_to_abs, collect_candidates


def _now_utc() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _to_iso(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime(_TIMESTAMP_FMT)


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
    reason: str | None = None,
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
        f"{reason or f'NLI contradiction score: **{score:.3f}**'}\n\n"
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
    indices_dir = ledger_notes_dir / INDICES_SUBDIR
    inbox_dir = ledger_notes_dir / INBOX_SUBDIR

    # Load scan state
    state = load_state(indices_dir)
    # In-run pair guard. Pair keys are symmetric, so when both notes of a pair
    # are candidates the reverse direction comes up again later in this scan.
    # resolved_pairs is only written under --apply, so without this a --check
    # run infers every such pair twice and double-counts it in the report.
    seen_pairs: set[str] = set()

    candidates, slots = collect_candidates(ledger_notes_dir, state)

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

    from ledger.nli import contradiction_score as nli_score

    neighbor_fn = _neighbor_fn or _get_semantic_neighbors

    # Pre-scan conflict notes once; avoids O(pairs × conflict_files) I/O.
    # Key: frozenset of the two rel_paths embedded in the pair_key; value: path.
    _conflict_note_texts: dict[str, str] = {}
    if inbox_dir.is_dir():
        for _cp in inbox_dir.glob("conflict__*.md"):
            try:
                _conflict_note_texts[str(_cp)] = _cp.read_text(encoding="utf-8")
            except OSError:
                pass

    def _cached_existing_conflict_note(pair_key: str) -> Path | None:
        parts = pair_key.split("|")
        for _cp_str, _ct in _conflict_note_texts.items():
            if all(p in _ct for p in parts):
                return Path(_cp_str)
        return None

    # Attribute-slot pass (plan 09). Two live notes claiming the same slot
    # ("residence", "kim.employer") cannot both be current, whatever NLI makes
    # of their wording - this catches the implicit conflicts NLI misses (a new
    # lease vs "lives in"). No model needed; filed for review like any other
    # conflict, never auto-resolved: the slot says *that* they clash, not
    # which one is stale.
    for slot, members in sorted(slots.items()):
        for i, (rel_a, body_a) in enumerate(members):
            for rel_b, body_b in members[i + 1:]:
                pk = _pair_key(rel_a, rel_b)
                if pk in state.resolved_pairs or pk in seen_pairs:
                    continue
                seen_pairs.add(pk)
                scan_result.conflict_notes += 1
                if not apply:
                    scan_result.pair_results.append(PairResult(
                        candidate_ref=rel_a, neighbor_ref=rel_b, contradiction_score=1.0,
                        decision=Decision.REVIEW, action_taken="dry_run_slot_conflict",
                    ))
                    continue
                if _cached_existing_conflict_note(pk) is None:
                    now = _now_utc()
                    filename = _make_conflict_note_filename(rel_a, rel_b, now)
                    inbox_dir.mkdir(parents=True, exist_ok=True)
                    content = _build_conflict_note(
                        rel_a, rel_b, 1.0, body_a, body_b, now,
                        reason=f"Both notes claim the attribute slot `{slot}`.",
                    )
                    safe_write_text(inbox_dir / filename, content)
                    _conflict_note_texts[str(inbox_dir / filename)] = content
                    _append_conflict_timeline(f"notes/{INBOX_SUBDIR}/{filename}", rel_a, rel_b, 1.0)
                scan_result.pair_results.append(PairResult(
                    candidate_ref=rel_a, neighbor_ref=rel_b, contradiction_score=1.0,
                    decision=Decision.REVIEW, action_taken="slot_conflict_noted",
                ))
                state.resolved_pairs[pk] = Decision.REVIEW.value

    if missing_embed_index:
        # Report that embedding build is needed; do not crash. The slot pass
        # above needs no index, so its state is kept.
        if apply and scan_result.conflict_notes:
            save_state(indices_dir, state)
        return scan_result

    for candidate_ref, candidate_abs, content_h, cand_fm, cand_body in candidates:
        # `candidates` was snapshotted before this loop. Under --apply an earlier
        # iteration may have superseded this very note, which stamps superseded_by
        # and moves the file into 09_archive. Scoring the stale in-memory copy
        # would then call supersede() with a path that no longer exists, and that
        # failure degrades into a spurious conflict note in the inbox.
        if apply:
            if not candidate_abs.exists():
                continue
            try:
                fresh_fm, _ = parse_frontmatter_text(candidate_abs.read_text(encoding="utf-8"))
            except OSError:
                continue
            if _is_superseded(fresh_fm):
                continue

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
            if pk in state.resolved_pairs or pk in seen_pairs:
                continue
            seen_pairs.add(pk)

            # Resolve neighbor abs path
            neighbor_abs = _rel_path_to_abs(neighbor_rel, ledger_notes_dir)
            if neighbor_abs is None or not neighbor_abs.exists():
                continue

            try:
                neighbor_text = neighbor_abs.read_text(encoding="utf-8")
            except OSError:
                continue
            neighbor_fm, neighbor_body = parse_frontmatter_text(neighbor_text)
            neighbor_body = strip_private_tags(neighbor_body)

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
                # Only persist when this run already changed the filesystem.
                # If NLI was unavailable from the first pair nothing was written,
                # and the next run should still see the full candidate set --
                # but once supersessions or conflict notes exist on disk, losing
                # the state that records them means redoing the whole scan.
                if apply and (scan_result.supersessions or scan_result.conflict_notes):
                    save_state(indices_dir, state)
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
                protect_higher_confidence=config.contradiction_protect_higher_confidence,
                auto_supersede=config.contradiction_auto_supersede,
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
                    existing_fb = _cached_existing_conflict_note(pk)
                    if existing_fb is None:
                        now_fb = _now_utc()
                        filename_fb = _make_conflict_note_filename(
                            candidate_ref, neighbor_rel, now_fb
                        )
                        inbox_dir.mkdir(parents=True, exist_ok=True)
                        conflict_path_fb = inbox_dir / filename_fb
                        content_fb = _build_conflict_note(
                            candidate_ref,
                            neighbor_rel,
                            score,
                            cand_body,
                            neighbor_body,
                            now_fb,
                        )
                        safe_write_text(conflict_path_fb, content_fb)
                        # Mirror the REVIEW path: seed the in-memory cache so the
                        # reverse direction of this pair, evaluated later in the
                        # same run, finds it instead of writing a duplicate.
                        _conflict_note_texts[str(conflict_path_fb)] = content_fb
                        _append_conflict_timeline(
                            f"notes/{INBOX_SUBDIR}/{filename_fb}",
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
            existing = _cached_existing_conflict_note(pk)
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
            # Update in-memory cache so subsequent pairs in this run see it.
            _conflict_note_texts[str(conflict_path)] = conflict_content
            _append_conflict_timeline(
                f"notes/{INBOX_SUBDIR}/{filename}",
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
                        "inbox": f"notes/{INBOX_SUBDIR}/{filename}",
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
