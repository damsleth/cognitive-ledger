from __future__ import annotations

from pathlib import Path

from ledger.parsing import extract_title, parse_frontmatter_text, parse_sections
from ledger.io import safe_write_text

from .importer import _candidate_key, _write_canonical_note
from .models import Candidate, ObsidianLedgerConfig
from .state import load_state, save_state
from .utils import append_timeline, frontmatter_to_text, now_iso


def _read_candidate(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter_text(text)
    return frontmatter, body


def _write_candidate(path: Path, frontmatter: dict[str, object], body: str) -> None:
    payload = frontmatter_to_text(frontmatter) + "\n\n" + body.rstrip() + "\n"
    safe_write_text(path, payload)


def _candidate_statement(frontmatter: dict[str, object], body: str) -> str:
    sections = parse_sections(body)
    statement = "\n".join(sections.get("statement", [])).strip()
    if statement:
        return statement

    title = extract_title(body)
    if title.lower().startswith("candidate:"):
        return title.split(":", 1)[1].strip()
    return title.strip()


def sync_queue(config: ObsidianLedgerConfig) -> dict[str, int]:
    inbox_dir = config.notes_root / "00_inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(config)
    promoted = 0
    rejected = 0
    pending = 0

    for path in sorted(inbox_dir.glob("candidate__*.md")):
        frontmatter, body = _read_candidate(path)
        review_status = str(frontmatter.get("review_status", "pending")).strip().lower()

        if review_status == "pending":
            pending += 1
            continue
        if review_status == "rejected":
            rejected += 1
            continue
        if review_status not in {"approved", "promoted"}:
            pending += 1
            continue

        if review_status == "promoted":
            continue

        kind = str(frontmatter.get("ledger_kind", "")).strip().lower()
        if kind not in {"fact", "pref", "goal", "loop", "concept"}:
            pending += 1
            continue

        statement = _candidate_statement(frontmatter, body)
        if not statement:
            pending += 1
            continue

        scope = str(frontmatter.get("scope", "personal")).strip() or "personal"
        lang = str(frontmatter.get("lang", "mixed")).strip() or "mixed"
        origin_rel = str(frontmatter.get("origin_path", "unknown"))
        imported_ts = now_iso()

        ledger_confidence = frontmatter.get("ledger_confidence", frontmatter.get("confidence", 0.75))
        try:
            confidence = float(ledger_confidence)
        except (TypeError, ValueError):
            confidence = 0.75

        candidate = Candidate(kind=kind, statement=statement, confidence=confidence, tags=["imported", "promoted"])
        promoted_path = str(frontmatter.get("promoted_path", "")).strip()

        if promoted_path:
            canonical_abs = config.vault_root / promoted_path
            # Ensure the resolved path stays within the vault root
            try:
                canonical_abs.resolve().relative_to(config.vault_root.resolve())
            except ValueError:
                pending += 1
                continue
            if not canonical_abs.exists():
                promoted_path = _write_canonical_note(config, candidate, scope, lang, origin_rel, imported_ts)
        else:
            promoted_path = _write_canonical_note(config, candidate, scope, lang, origin_rel, imported_ts)

        frontmatter["review_status"] = "promoted"
        frontmatter["promoted_path"] = promoted_path
        frontmatter["updated"] = imported_ts
        _write_candidate(path, frontmatter, body)

        rel_candidate = path.resolve().relative_to(config.vault_root.resolve()).as_posix()
        append_timeline(config.timeline_path, "created", promoted_path, "promoted from candidate queue")
        append_timeline(config.timeline_path, "updated", rel_candidate, "candidate promoted")

        key = str(frontmatter.get("ledger_key", "")).strip()
        if not key:
            key = _candidate_key(kind, scope, statement)
        state.imported_keys[key] = {
            "ts": imported_ts,
            "note_path": promoted_path,
            "origin": origin_rel,
            "kind": "promoted",
        }
        promoted += 1

    save_state(config, state)
    return {
        "promoted": promoted,
        "rejected": rejected,
        "pending": pending,
    }
