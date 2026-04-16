from __future__ import annotations

from pathlib import Path

from ledger.io import safe_write_text

from .extraction import (
    extract_candidates,
    is_journal_archive,
    is_journal_file,
    is_meeting_like,
    is_prompt_file,
    loop_has_decision_pending_signal,
    loop_has_ownership_signal,
    loop_has_strong_marker,
    score_signal,
    yield_hint,
)
from .models import Candidate, ImportResult, ImportState, ObsidianLedgerConfig, ScanRow, NOTE_FOLDERS, NOTE_PREFIX
from .state import load_state, save_state
from .utils import (
    append_log,
    append_timeline,
    count_words,
    infer_lang,
    infer_scope_from_relpath,
    normalize_statement,
    now_iso,
    sha1_file,
    sha1_text,
    should_skip_markdown,
    slugify,
    write_markdown,
)


def _relative_to_vault(config: ObsidianLedgerConfig, path: Path) -> str:
    return path.resolve().relative_to(config.vault_root.resolve()).as_posix()


def _candidate_key(kind: str, scope: str, statement: str) -> str:
    return sha1_text(f"{kind}|{scope}|{normalize_statement(statement)}")


def scan_vault(config: ObsidianLedgerConfig, changed_paths: set[Path] | None = None) -> list[ScanRow]:
    rows: list[ScanRow] = []
    if changed_paths is not None:
        candidates = sorted({Path(p).resolve() for p in changed_paths})
    else:
        candidates = sorted(config.vault_root.rglob("*.md"))

    for path in candidates:
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        if should_skip_markdown(path, config.vault_root, config.exclude_dirs):
            continue

        try:
            stat = path.stat()
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        headings, tasks, signal = score_signal(content)
        rel = _relative_to_vault(config, path)
        rows.append(
            ScanRow(
                path_abs=path,
                path_rel=rel,
                mtime_ms=int(getattr(stat, "st_mtime", 0) * 1000),
                size=int(stat.st_size),
                words=count_words(content),
                headings=headings,
                tasks=tasks,
                signal_score=signal,
                yield_hint=yield_hint(content),
                scope=infer_scope_from_relpath(rel),
            )
        )

    rows.sort(key=lambda row: (row.yield_hint, row.signal_score), reverse=True)
    return rows


def render_scan_report(rows: list[ScanRow], config: ObsidianLedgerConfig) -> str:
    by_scope: dict[str, int] = {"home": 0, "work": 0, "dev": 0, "personal": 0, "meta": 0}
    for row in rows:
        by_scope[row.scope] = by_scope.get(row.scope, 0) + 1

    lines = [
        "# Obsidian Scan Report",
        "",
        f"- Scanned: {len(rows)} files",
        f"- Vault: {config.vault_root}",
        f"- Generated: {now_iso()}",
        "",
        "## Files by scope",
    ]
    for scope in ("home", "work", "dev", "personal", "meta"):
        lines.append(f"- {scope}: {by_scope.get(scope, 0)}")

    lines.append("")
    lines.append("## Top candidates (by yield hint, then signal score)")
    lines.append("Format: yield | score | scope | path | words | tasks | headings")
    for row in rows[:50]:
        lines.append(
            f"- {row.yield_hint:.2f} | {row.signal_score:.2f} | {row.scope} | {row.path_rel} | {row.words} | {row.tasks} | {row.headings}"
        )

    lines.extend(["", "## Notes", "- This report does not write to source notes."])
    return "\n".join(lines) + "\n"


def _kind_to_folder(kind: str) -> str:
    return NOTE_FOLDERS[kind]


def _kind_to_prefix(kind: str) -> str:
    return NOTE_PREFIX[kind]


def _canonical_note_body(kind: str, statement: str, origin_rel: str, imported_ts: str) -> str:
    if kind == "loop":
        return "\n".join(
            [
                f"# Loop: {statement}",
                "",
                "## Question or Task",
                statement,
                "",
                "## Context",
                "Imported from Obsidian source note.",
                "",
                "## Next Action",
                "- [ ] Clarify and execute this loop",
                "",
                "## Exit Condition",
                "- [ ] Outcome captured in cognitive ledger",
                "",
                "## Provenance",
                f"Origin: {origin_rel}",
                f"Imported: {imported_ts}",
                "",
                "## Links",
                "- ",
            ]
        )

    return "\n".join(
        [
            f"# {statement}",
            "",
            "## Statement",
            statement,
            "",
            "## Context",
            "Imported from Obsidian source note.",
            "",
            "## Provenance",
            f"Origin: {origin_rel}",
            f"Imported: {imported_ts}",
            "",
            "## Links",
            "- ",
        ]
    )


def _candidate_note_body(statement: str, origin_rel: str, imported_ts: str) -> str:
    return "\n".join(
        [
            f"# Candidate: {statement}",
            "",
            "## Statement",
            statement,
            "",
            "## Context",
            "Auto-extracted candidate. Review before promotion.",
            "",
            "## Review Instructions",
            "Set `review_status` to `approved` or `rejected`.",
            "",
            "## Provenance",
            f"Origin: {origin_rel}",
            f"Imported: {imported_ts}",
            "",
            "## Links",
            "- ",
        ]
    )


def _write_canonical_note(
    config: ObsidianLedgerConfig,
    candidate: Candidate,
    scope: str,
    lang: str,
    origin_rel: str,
    imported_ts: str,
) -> str:
    folder = _kind_to_folder(candidate.kind)
    prefix = _kind_to_prefix(candidate.kind)
    slug = slugify(candidate.statement)
    file_name = f"{prefix}__{slug}.md"
    target_dir = config.notes_root / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / file_name

    # Use a hash suffix to avoid TOCTOU race when concurrent imports
    # check exists() and then write — always include a unique suffix
    # if the base name already exists.
    while target.exists():
        suffix = sha1_text(f"{candidate.kind}|{candidate.statement}|{imported_ts}|{file_name}")[:6]
        file_name = f"{prefix}__{slug}__{suffix}.md"
        target = target_dir / file_name

    frontmatter: dict[str, object] = {
        "created": imported_ts,
        "updated": imported_ts,
        "tags": candidate.tags,
        "confidence": round(float(candidate.confidence), 2),
        "source": "inferred",
        "scope": scope,
        "lang": lang,
    }
    if candidate.kind == "loop":
        frontmatter["status"] = "open"

    body = _canonical_note_body(candidate.kind, candidate.statement, origin_rel, imported_ts)
    write_markdown(target, frontmatter, body)
    return target.resolve().relative_to(config.vault_root.resolve()).as_posix()


def _preview_canonical_relpath(config: ObsidianLedgerConfig, candidate: Candidate) -> str:
    folder = _kind_to_folder(candidate.kind)
    prefix = _kind_to_prefix(candidate.kind)
    slug = slugify(candidate.statement)
    file_name = f"{prefix}__{slug}.md"
    return f"cognitive-ledger/notes/{folder}/{file_name}"


def _write_candidate_note(
    config: ObsidianLedgerConfig,
    candidate: Candidate,
    scope: str,
    lang: str,
    origin_rel: str,
    origin_hash: str,
    imported_ts: str,
    key: str,
    candidate_score: float,
) -> str:
    folder = config.notes_root / "00_inbox"
    folder.mkdir(parents=True, exist_ok=True)
    slug = slugify(candidate.statement)
    file_name = f"candidate__{slug}.md"
    target = folder / file_name

    while target.exists():
        suffix = sha1_text(f"{candidate.kind}|{candidate.statement}|{origin_rel}|{file_name}")[:6]
        file_name = f"candidate__{slug}__{suffix}.md"
        target = folder / file_name

    frontmatter = {
        "created": imported_ts,
        "updated": imported_ts,
        "tags": ["candidate", "imported", candidate.kind],
        "confidence": round(float(candidate.confidence), 2),
        "source": "inferred",
        "scope": scope,
        "lang": lang,
        "ledger_kind": candidate.kind,
        "ledger_confidence": round(float(candidate.confidence), 2),
        "origin_path": origin_rel,
        "origin_hash": origin_hash,
        "review_status": "pending",
        "candidate_score": round(float(candidate_score), 3),
        "ledger_key": key,
    }
    body = _candidate_note_body(candidate.statement, origin_rel, imported_ts)
    write_markdown(target, frontmatter, body)
    return target.resolve().relative_to(config.vault_root.resolve()).as_posix()


def _preview_candidate_relpath(candidate: Candidate) -> str:
    slug = slugify(candidate.statement)
    return f"cognitive-ledger/notes/00_inbox/candidate__{slug}.md"


def _filter_file_candidates(path: Path, content: str, file_candidates: list[Candidate]) -> list[Candidate]:
    candidates = list(file_candidates)
    if is_prompt_file(path):
        candidates = [candidate for candidate in candidates if candidate.kind == "concept"]

    if is_journal_archive(path):
        candidates = [
            candidate
            for candidate in candidates
            if candidate.kind != "loop" or loop_has_strong_marker(candidate.statement)
        ]

    if is_journal_file(path) or is_meeting_like(path, content):
        candidates = [
            candidate
            for candidate in candidates
            if candidate.kind != "loop"
            or loop_has_ownership_signal(candidate.statement)
            or loop_has_decision_pending_signal(candidate.statement)
        ]

    return candidates


def _bucket_for_rel(path_rel: str) -> str:
    normalized = path_rel.replace("\\", "/")
    if normalized.startswith("90-journal/archive/"):
        return "journal-archive"
    if normalized.startswith("90-journal/"):
        return "journal"
    if normalized.startswith("02-work/"):
        return "work"
    if normalized.startswith("04-dev/"):
        return "dev"
    if normalized.startswith("01-home/"):
        return "home"
    if normalized.startswith("03-community/"):
        return "community"
    if normalized.startswith("92-archive/"):
        return "archive"
    return "other"


def run_import(
    config: ObsidianLedgerConfig,
    *,
    dry_run: bool = False,
    max_files: int | None = None,
    max_notes: int | None = None,
    changed_paths: set[Path] | None = None,
) -> ImportResult:
    state = load_state(config)
    rows = scan_vault(config, changed_paths=changed_paths)

    if not dry_run:
        safe_write_text(config.scan_path, render_scan_report(rows, config))

    result = ImportResult(dry_run=dry_run)
    files_limit = max_files if max_files is not None else config.max_files_per_cycle
    notes_limit = max_notes if max_notes is not None else config.max_notes_per_cycle

    filtered = [row for row in rows if row.signal_score >= config.file_signal_min]
    filtered.sort(key=lambda row: (row.yield_hint, row.mtime_ms, row.signal_score), reverse=True)

    selected: list[ScanRow] = []
    bucket_count: dict[str, int] = {}
    bucket_caps: dict[str, int] = {
        "journal-archive": 5,
        "journal": 20,
    }

    for row in filtered:
        if len(selected) >= files_limit:
            break

        path = row.path_abs
        try:
            file_hash = sha1_file(path)
        except OSError:
            continue

        prev = state.processed_files.get(str(path))
        if prev and str(prev.get("hash", "")) == file_hash and int(prev.get("size", -1)) == row.size:
            continue

        bucket = _bucket_for_rel(row.path_rel)
        count = bucket_count.get(bucket, 0)
        cap = bucket_caps.get(bucket)
        if cap is not None and count >= cap:
            continue

        selected.append(row)
        bucket_count[bucket] = count + 1

    result.selected_files = len(selected)

    created_entries: list[tuple[str, str]] = []
    log_lines: list[str] = [
        f"- Vault: {config.vault_root}",
        f"- Selected files: {len(selected)}",
    ]

    notes_written = 0
    for row in selected:
        if notes_written >= notes_limit:
            break
        path = row.path_abs
        try:
            content = path.read_text(encoding="utf-8")
            from ledger.parsing import strip_private_tags
            content = strip_private_tags(content)
            file_hash = sha1_file(path)
        except (OSError, UnicodeDecodeError):
            continue

        candidates = [
            candidate
            for candidate in extract_candidates(content)
            if candidate.confidence >= config.queue_confidence_min
        ]
        candidates = _filter_file_candidates(path, content, candidates)

        processed_meta = {
            "mtimeMs": row.mtime_ms,
            "size": row.size,
            "hash": file_hash,
        }

        if not candidates:
            state.processed_files[str(path)] = processed_meta
            continue

        scope = infer_scope_from_relpath(row.path_rel)
        lang = infer_lang(content)

        for candidate in candidates:
            if notes_written >= notes_limit:
                break

            key = _candidate_key(candidate.kind, scope, candidate.statement)
            if key in state.imported_keys:
                result.skipped_deduped += 1
                continue

            candidate_score = min(1.0, float(candidate.confidence) + min(row.signal_score / 30.0, 0.2))
            imported_ts = now_iso()
            origin_rel = row.path_rel

            if candidate.confidence >= config.auto_write_confidence_min:
                if dry_run:
                    rel_path = _preview_canonical_relpath(config, candidate)
                else:
                    rel_path = _write_canonical_note(config, candidate, scope, lang, origin_rel, imported_ts)
                result.notes_created += 1
                result.created_note_paths.append(rel_path)
                created_entries.append((rel_path, "created"))
                notes_written += 1
                state.imported_keys[key] = {
                    "ts": imported_ts,
                    "note_path": rel_path,
                    "origin": origin_rel,
                    "kind": "canonical",
                }
            elif candidate.confidence >= config.queue_confidence_min:
                if dry_run:
                    rel_path = _preview_candidate_relpath(candidate)
                else:
                    rel_path = _write_candidate_note(
                        config,
                        candidate,
                        scope,
                        lang,
                        origin_rel,
                        file_hash,
                        imported_ts,
                        key,
                        candidate_score,
                    )
                result.queue_created += 1
                result.created_queue_paths.append(rel_path)
                created_entries.append((rel_path, "created"))
                notes_written += 1
                state.imported_keys[key] = {
                    "ts": imported_ts,
                    "note_path": rel_path,
                    "origin": origin_rel,
                    "kind": "queued",
                }
            else:
                result.skipped_low_confidence += 1

        state.processed_files[str(path)] = processed_meta

    state.vault_root = str(config.vault_root)
    state.ledger_root = str(config.ledger_root)
    state.last_run = now_iso()

    log_lines.append(f"- Notes created: {result.notes_created}")
    log_lines.append(f"- Queue candidates created: {result.queue_created}")
    if dry_run:
        log_lines.append("- Mode: dry-run (no files written)")

    if result.created_note_paths:
        log_lines.append("\n### Created canonical notes")
        for path in result.created_note_paths:
            log_lines.append(f"- {path}")
    if result.created_queue_paths:
        log_lines.append("\n### Created queue candidates")
        for path in result.created_queue_paths:
            log_lines.append(f"- {path}")

    if not dry_run:
        save_state(config, state)
        append_log(config.log_path, log_lines)
        for rel_path, action in created_entries:
            append_timeline(config.timeline_path, action, rel_path, "imported from Obsidian")

    return result
