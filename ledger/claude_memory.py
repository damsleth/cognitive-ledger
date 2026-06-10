"""Import Claude Code "memory" files into the cognitive ledger.

Claude Code keeps a per-project memory store under
``~/.claude/projects/<encoded-cwd>/memory/`` — one atomic markdown note
per fact, plus a ``MEMORY.md`` index. Each note carries a small YAML
frontmatter::

    ---
    name: video-vision-setup
    description: one-line summary used for recall
    metadata:
      node_type: memory
      type: reference          # user | feedback | project | reference
      originSessionId: <uuid>
    ---
    body, with [[wikilinks]] to sibling memories

These are already Tier-2-shaped: curated, one-idea-per-file, deliberately
distilled. They are not raw exhaust, so they belong in the ledger rather
than the YAAMS firehose. This module maps them onto the ledger note
schema and writes them (to ``00_inbox/`` by default, or straight to the
typed folders with ``mode="direct"``).

The mapping is intentionally transparent: the dry-run report shows the
ledger type, scope, and the heuristic reason for every file so the
classification can be reviewed before anything lands.

Design mirrors :mod:`ledger.importers.backends.obsidian.importer` (scan → classify → write,
with a JSON state file for idempotent re-import) but is self-contained
because the source frontmatter is a different shape.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.config import LedgerConfig, get_config
from ledger.io import safe_write_text
from ledger.io.safe_write import append_timeline_entry
from ledger.importers.state import backend_state_dir, relocate_legacy_file
from ledger.layout import (
    NOTE_LAYOUTS,
    indices_dir,
    inbox_dir,
    note_type_dir,
)
from ledger.parsing import strip_private_tags
from ledger.text import infer_lang, sha1_file, sha1_text, slugify, write_markdown


DEFAULT_MEMORY_ROOT = Path.home() / ".claude" / "projects"
_BACKEND = "claude_memory"
# Legacy filename kept for backward-compat migration (see _state_path).
_LEGACY_STATE_FILENAME = "claude_memory_import_state.json"

# Provenance channel recorded in frontmatter (see schema proposal in
# docs/claude-memory-import.md). Distinct from the epistemic ``source``
# field: ``via`` says which pipeline carried the note in, ``source`` says
# who the fact came from.
VIA_CHANNEL = "claude-memory"

# Body/name markers used to refine the heterogeneous "project" and
# "reference" Claude types into ledger types.
_LOOP_MARKERS = re.compile(
    r"\b(todo|roadmap|open question|unresolved|pending|need(?:s)? to|"
    r"should (?:add|fix|implement|migrate|wire)|fix\b|bug\b|broken|blocked|"
    r"next step|follow[- ]?up|wip|in progress|backlog)\b",
    re.IGNORECASE,
)
_CONCEPT_MARKERS = re.compile(
    r"\b(architecture|philosophy|axiom|principle|convention|framework|"
    r"mental model|design pattern|invariant|taxonomy)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Source parsing
# ---------------------------------------------------------------------------


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_claude_note(text: str) -> tuple[dict[str, str], str]:
    """Split a Claude memory file into (flat frontmatter, body).

    The nested ``metadata:`` block is flattened with a ``metadata.``
    prefix (e.g. ``metadata.type``). Written explicitly rather than
    reusing the ledger frontmatter parser so the nested mapping is
    handled deterministically regardless of upstream parser changes.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return {}, text

    fm: dict[str, str] = {}
    in_metadata = False
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = re.match(r"^(\s*)([A-Za-z0-9_.-]+):\s*(.*)$", raw)
        if not match:
            continue
        indent, key, value = match.group(1), match.group(2), match.group(3)
        if key == "metadata" and not value.strip():
            in_metadata = True
            continue
        if indent and in_metadata:
            fm[f"metadata.{key}"] = _unquote(value)
        else:
            in_metadata = False
            fm[key] = _unquote(value)

    body = "\n".join(lines[end + 1 :])
    return fm, body


# ---------------------------------------------------------------------------
# Project-folder decoding (scope + project name)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectContext:
    project: str  # tag-safe project slug, or "" for global/home
    scope: str    # ledger scope enum


def decode_project_context(memory_dir: Path) -> ProjectContext:
    """Infer (project, scope) from the encoded ``<cwd>/memory`` folder name.

    Claude encodes the session cwd by replacing ``/`` with ``-`` (path
    separators and literal dashes collide, so this is best-effort, not a
    lossless decode). We only need a scope hint and a tag-safe project
    name, both of which survive the ambiguity.

    Examples (folder name -> project, scope):
      -Users-damsleth-code-YAAMS            -> ("yaams", "dev")
      -Users-damsleth-code-cognitive-ledger -> ("cognitive-ledger", "dev")
      -Users-damsleth-brain                 -> ("brain", "personal")
      -Users-damsleth                       -> ("", "meta")
      -Users-damsleth--claude-projects      -> ("", "meta")
    """
    name = memory_dir.parent.name  # the encoded-cwd folder
    lowered = name.lower()

    # Bare home root or the .claude tree itself -> global/meta.
    if re.fullmatch(r"-users-[^-]+", lowered) or "claude-projects" in lowered:
        return ProjectContext(project="", scope="meta")

    if "-code-" in lowered:
        project = lowered.split("-code-", 1)[1].strip("-")
        return ProjectContext(project=_tag_safe(project), scope="dev")

    if lowered.endswith("-code"):
        return ProjectContext(project="", scope="dev")

    # Non-code project dir (e.g. ~/brain). Last segment as project name.
    segs = [s for s in lowered.split("-") if s]
    project = _tag_safe(segs[-1]) if segs else ""
    return ProjectContext(project=project, scope="personal")


_TAG_OK = re.compile(r"[^a-zæøå0-9_-]")


def _tag_safe(value: str) -> str:
    value = value.strip().lower()
    value = _TAG_OK.sub("-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value


# ---------------------------------------------------------------------------
# Classification: Claude type -> ledger (type, source)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    note_type: str   # canonical NOTE_LAYOUTS key (facts/preferences/...)
    source: str      # ledger source enum (user/assistant/...)
    reason: str      # human-readable explanation for the dry-run report


def classify(claude_type: str, name: str, body: str) -> Classification:
    """Map a Claude memory type onto a ledger note type + source.

    ``feedback`` and ``user`` map cleanly. ``project`` and ``reference``
    are heterogeneous, so we refine them with body/name markers:
      - open-work language        -> loops (status: open)
      - definition/framework language -> concepts
      - otherwise                 -> facts
    """
    ctype = (claude_type or "").strip().lower()
    name_l = name.lower()
    text = f"{name}\n{body}".lower()

    if ctype == "feedback":
        # Guidance on how the agent should work → preference, from the user.
        return Classification("preferences", "user", "feedback → pref (agent guidance)")

    if ctype == "user":
        # Who the user is → stable fact, from the user.
        return Classification("facts", "user", "user → fact (about the user)")

    # The title is the strongest signal: an "architecture"/"philosophy" note
    # is a concept even if its body mentions follow-up work.
    if _CONCEPT_MARKERS.search(name_l):
        return Classification("concepts", "assistant", "concept marker in title → concept")

    if ctype == "project" and _LOOP_MARKERS.search(text):
        return Classification("loops", "assistant", "project + open-work markers → loop")

    if _CONCEPT_MARKERS.search(text):
        return Classification("concepts", "assistant", f"{ctype or 'note'} + concept markers → concept")

    if ctype == "reference":
        return Classification("facts", "assistant", "reference → fact (durable pointer/config)")

    # project default, or any unknown type.
    return Classification("facts", "assistant", f"{ctype or 'note'} → fact (durable)")


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


@dataclass
class PlannedNote:
    origin_path: Path
    origin_hash: str
    name: str
    description: str
    claude_type: str
    session_id: str
    project: str
    scope: str
    note_type: str
    source: str
    lang: str
    confidence: float
    tags: list[str]
    reason: str
    created: str
    updated: str
    target_rel: str          # logical "<subdir>/<file>.md"
    external_id: str
    body: str                # original source body (private-stripped)
    skipped: bool = False
    skip_reason: str = ""
    link_count: int = 0
    resolved_links: int = 0

    def frontmatter(self) -> dict[str, Any]:
        fm: dict[str, Any] = {
            "created": self.created,
            "updated": self.updated,
            "tags": self.tags,
            "confidence": round(float(self.confidence), 2),
            "source": self.source,
            "scope": self.scope,
            "lang": self.lang,
        }
        if self.note_type == "loops":
            fm["status"] = "open"
        # Provenance triad (see schema proposal). Tolerated as extra fields
        # by lint today; formalize in schema.yaml to make them first-class.
        fm["via"] = VIA_CHANNEL
        fm["origin"] = str(self.origin_path)
        fm["external_id"] = self.external_id
        fm["claude_type"] = self.claude_type
        return fm

    def render(self) -> str:
        from ledger.parsing.frontmatter import serialize_frontmatter

        return (
            serialize_frontmatter(self.frontmatter())
            + "\n\n"
            + _compose_body(self).rstrip()
            + "\n"
        )


@dataclass
class ImportPlan:
    memory_root: Path
    notes: list[PlannedNote] = field(default_factory=list)
    folders_scanned: int = 0
    files_seen: int = 0

    @property
    def planned(self) -> list[PlannedNote]:
        return [n for n in self.notes if not n.skipped]

    @property
    def skipped(self) -> list[PlannedNote]:
        return [n for n in self.notes if n.skipped]


_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_NOTE_PREFIXES = tuple(layout.prefix for layout in NOTE_LAYOUTS.values())


def _resolution_key(text: str) -> str:
    """Canonical key for matching a link token to a note, collapsing
    kebab/snake variants (slugify maps both ``-`` and ``_`` to ``_``)."""
    return slugify(text)


def _strip_note_prefix(stem: str) -> str:
    for prefix in _NOTE_PREFIXES:
        if stem.startswith(prefix):
            return stem[len(prefix) :]
    return stem


def _build_link_resolution(notes: list[PlannedNote], cfg: LedgerConfig) -> dict[str, str]:
    """Map a normalized link key → resolvable note filename (with ``.md``).

    Covers (a) the notes being imported this run and (b) notes already in
    the ledger, so re-runs and cross-links to pre-existing notes resolve
    too. The batch is authoritative over existing files.
    """
    resolution: dict[str, str] = {}

    notes_dir = cfg.ledger_notes_dir
    if notes_dir and Path(notes_dir).is_dir():
        for path in Path(notes_dir).rglob("*.md"):
            if path.name == "MEMORY.md" or "08_indices" in path.parts:
                continue
            stem = path.stem
            resolution.setdefault(_resolution_key(_strip_note_prefix(stem)), path.name)
            resolution.setdefault(_resolution_key(stem), path.name)

    for note in notes:
        prefix = NOTE_LAYOUTS[note.note_type].prefix
        resolution[_resolution_key(note.name)] = f"{prefix}{slugify(note.name)}.md"

    return resolution


def _rewrite_links(body: str, resolution: dict[str, str]) -> tuple[str, int]:
    """Rewrite ``[[token]]`` → ``[[filename.md|token]]`` when the token
    resolves to a known note, so the ledger link graph picks up the edge.
    Unresolved links are left untouched — inert, and (lacking a ``.md``
    target) they never trip the broken-link lint check.

    Returns ``(new_body, resolved_count)``.
    """
    resolved = 0

    def repl(match: "re.Match[str]") -> str:
        nonlocal resolved
        token, _sep, display = match.group(1).partition("|")
        token = token.strip()
        display = display.strip() or token
        target = resolution.get(_resolution_key(token))
        if target:
            resolved += 1
            return f"[[{target}|{display}]]"
        return match.group(0)

    return _WIKILINK.sub(repl, body), resolved


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stat_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state_path(cfg: LedgerConfig) -> Path:
    """Return the canonical state-file path, migrating legacy location if needed."""
    notes_dir = Path(cfg.ledger_notes_dir)
    new_path = backend_state_dir(notes_dir, _BACKEND) / "state.json"
    legacy_path = indices_dir(cfg.ledger_notes_dir) / _LEGACY_STATE_FILENAME
    relocate_legacy_file(legacy_path, new_path)
    return new_path


def _load_state(cfg: LedgerConfig) -> dict[str, Any]:
    path = _state_path(cfg)
    if not path.is_file():
        return {"entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"entries": {}}


def _save_state(cfg: LedgerConfig, state: dict[str, Any]) -> None:
    path = _state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_text(path, json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def iter_memory_files(memory_root: Path) -> list[Path]:
    """All ``*/memory/*.md`` files (excluding MEMORY.md), sorted."""
    out: list[Path] = []
    for memory_dir in sorted(memory_root.glob("*/memory")):
        if not memory_dir.is_dir():
            continue
        for md in sorted(memory_dir.glob("*.md")):
            if md.name == "MEMORY.md":
                continue
            out.append(md)
    return out


def build_plan(
    *,
    memory_root: Path,
    cfg: LedgerConfig,
    mode: str = "inbox",
) -> ImportPlan:
    """Scan the memory tree and produce a write plan (no writes)."""
    files = iter_memory_files(memory_root)
    state = _load_state(cfg)
    entries = state.get("entries", {})

    plan = ImportPlan(memory_root=memory_root)
    plan.files_seen = len(files)
    plan.folders_scanned = len(set(p.parent for p in files))

    for path in files:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = parse_claude_note(raw)
        body = strip_private_tags(body)

        name = fm.get("name") or path.stem
        description = fm.get("description", "")
        claude_type = fm.get("metadata.type", "")
        session_id = fm.get("metadata.originSessionId", "")

        ctx = decode_project_context(path.parent)
        cls = classify(claude_type, name, body)

        try:
            origin_hash = sha1_file(path)
        except OSError:
            continue

        tags = _build_tags(claude_type, ctx.project)
        lang = infer_lang(body)
        slug = slugify(name)
        prefix = NOTE_LAYOUTS[cls.note_type].prefix
        subdir = NOTE_LAYOUTS[cls.note_type].subdir
        target_subdir = "00_inbox" if mode == "inbox" else subdir
        target_rel = f"{target_subdir}/{prefix}{slug}.md"
        external_id = f"{ctx.project + '/' if ctx.project else ''}{name}"

        stat = path.stat()
        created = _stat_iso(getattr(stat, "st_birthtime", stat.st_ctime))
        updated = _stat_iso(stat.st_mtime)

        note = PlannedNote(
            origin_path=path,
            origin_hash=origin_hash,
            name=name,
            description=description,
            claude_type=claude_type,
            session_id=session_id,
            project=ctx.project,
            scope=ctx.scope,
            note_type=cls.note_type,
            source=cls.source,
            lang=lang,
            confidence=0.8 if cls.note_type == "loops" else 0.85,
            tags=tags,
            reason=cls.reason,
            created=created,
            updated=updated,
            target_rel=target_rel,
            external_id=external_id,
            body=body,
            link_count=len(_WIKILINK.findall(body)),
        )

        prev = entries.get(str(path))
        if prev and prev.get("hash") == origin_hash:
            note.skipped = True
            note.skip_reason = "unchanged since last import"

        plan.notes.append(note)

    # Second pass: rewrite [[wikilinks]] now that every note's target
    # filename is known (resolution map needs the whole batch).
    resolution = _build_link_resolution(plan.notes, cfg)
    for note in plan.notes:
        note.body, note.resolved_links = _rewrite_links(note.body, resolution)

    return plan


def _build_tags(claude_type: str, project: str) -> list[str]:
    tags = ["imported", VIA_CHANNEL]
    if claude_type:
        tags.append(_tag_safe(claude_type))
    if project:
        tags.append(project)
    # De-dupe, keep order, cap at schema max (10) with headroom.
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out[:6]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    dry_run: bool
    mode: str
    files_seen: int
    folders_scanned: int
    written: int = 0
    skipped: int = 0
    written_paths: list[str] = field(default_factory=list)


def run_import(
    *,
    memory_root: Path | None = None,
    mode: str = "inbox",
    dry_run: bool = False,
    cfg: LedgerConfig | None = None,
) -> tuple[ImportResult, ImportPlan]:
    cfg = cfg or get_config()
    memory_root = (memory_root or DEFAULT_MEMORY_ROOT).expanduser()
    plan = build_plan(memory_root=memory_root, cfg=cfg, mode=mode)

    result = ImportResult(
        dry_run=dry_run,
        mode=mode,
        files_seen=plan.files_seen,
        folders_scanned=plan.folders_scanned,
        skipped=len(plan.skipped),
    )

    if dry_run:
        result.written = len(plan.planned)
        result.written_paths = [n.target_rel for n in plan.planned]
        return result, plan

    state = _load_state(cfg)
    entries = state.setdefault("entries", {})

    for note in plan.planned:
        subdir = "00_inbox" if mode == "inbox" else NOTE_LAYOUTS[note.note_type].subdir
        if mode == "inbox":
            target_dir = inbox_dir(cfg.ledger_notes_dir)
        else:
            target_dir = note_type_dir(cfg.ledger_notes_dir, note.note_type)
        target_dir.mkdir(parents=True, exist_ok=True)

        prefix = NOTE_LAYOUTS[note.note_type].prefix
        slug = slugify(note.name)
        target = target_dir / f"{prefix}{slug}.md"
        while target.exists():
            suffix = sha1_text(f"{note.external_id}|{note.origin_hash}")[:6]
            target = target_dir / f"{prefix}{slug}__{suffix}.md"

        write_markdown(target, note.frontmatter(), _compose_body(note))
        rel = f"{subdir}/{target.name}"
        result.written += 1
        result.written_paths.append(rel)

        entries[str(note.origin_path)] = {
            "hash": note.origin_hash,
            "note_path": rel,
            "ts": _now_iso(),
            "external_id": note.external_id,
        }
        append_timeline_entry(
            cfg.timeline_path,
            "created",
            target,
            f"imported from {VIA_CHANNEL} ({note.claude_type})",
            root_dir=cfg.ledger_root,
            ledger_notes_dir=cfg.ledger_notes_dir,
        )

    _save_state(cfg, state)
    return result, plan


def _compose_body(note: PlannedNote) -> str:
    """Assemble the ledger note body: H1, original content, (loop scaffold,)
    and a Provenance section. Loops get a `## Next action` section with a
    checkbox so they satisfy `ledger sleep lint`."""
    title = note.description.strip() or note.name.replace("-", " ")
    parts = [f"# {title}", "", note.body.strip()]
    if note.note_type == "loops":
        parts += [
            "",
            "## Next action",
            "- [ ] Review this imported loop and define the concrete next step",
        ]
    parts += ["", "## Provenance", f"- Source: {note.origin_path}"]
    if note.session_id:
        parts.append(f"- Origin session: {note.session_id}")
    parts.append(f"- Claude memory type: {note.claude_type}")
    parts.append(f"- Imported via: {VIA_CHANNEL}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Reporting (human-readable dry-run output)
# ---------------------------------------------------------------------------


def render_report(plan: ImportPlan, *, mode: str, preview: int = 4) -> str:
    planned = plan.planned
    by_type: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    for note in planned:
        by_type[note.note_type] = by_type.get(note.note_type, 0) + 1
        by_scope[note.scope] = by_scope.get(note.scope, 0) + 1

    lines: list[str] = []
    lines.append("Claude-memory → ledger import (DRY RUN)")
    lines.append("=" * 52)
    lines.append(f"memory root : {plan.memory_root}")
    lines.append(f"write mode  : {mode}  (real run lands notes here)")
    lines.append(
        f"scanned     : {plan.files_seen} files across {plan.folders_scanned} project folders"
    )
    lines.append(f"to import   : {len(planned)}   skipped (unchanged): {len(plan.skipped)}")
    total_links = sum(n.link_count for n in planned)
    resolved_links = sum(n.resolved_links for n in planned)
    lines.append(
        f"wikilinks   : {resolved_links}/{total_links} resolved to ledger notes "
        f"(unresolved left inert)"
    )
    lines.append("")
    lines.append("by ledger type : " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    lines.append("by scope       : " + ", ".join(f"{k}={v}" for k, v in sorted(by_scope.items())))
    lines.append("")
    lines.append("MAPPING TABLE")
    lines.append("-" * 52)
    lines.append("claude_type  ledger_type/scope  target                                  reason")
    for note in planned:
        dest = note.target_rel
        if len(dest) > 38:
            dest = dest[:37] + "…"
        lines.append(
            f"{note.claude_type:<11}  {note.note_type[:5]:<5}/{note.scope:<8}  {dest:<38}  {note.reason}"
        )

    lines.append("")
    lines.append(f"EXAMPLE HITS (first {min(preview, len(planned))} rendered in full)")
    lines.append("=" * 52)
    # Prefer a diverse sample across ledger types for the previews.
    chosen = _diverse_sample(planned, preview)
    for note in chosen:
        lines.append("")
        lines.append(f"### {note.origin_path.name}  →  {note.target_rel}")
        lines.append("```markdown")
        lines.append(note.render().rstrip())
        lines.append("```")

    return "\n".join(lines) + "\n"


def _diverse_sample(notes: list[PlannedNote], limit: int) -> list[PlannedNote]:
    chosen: list[PlannedNote] = []
    seen_types: set[str] = set()
    # First pass: one per distinct ledger type.
    for note in notes:
        if note.note_type not in seen_types:
            chosen.append(note)
            seen_types.add(note.note_type)
        if len(chosen) >= limit:
            return chosen
    # Fill remainder in order.
    for note in notes:
        if note not in chosen:
            chosen.append(note)
        if len(chosen) >= limit:
            break
    return chosen


__all__ = [
    "DEFAULT_MEMORY_ROOT",
    "Classification",
    "ImportPlan",
    "ImportResult",
    "PlannedNote",
    "ProjectContext",
    "build_plan",
    "classify",
    "decode_project_context",
    "iter_memory_files",
    "parse_claude_note",
    "render_report",
    "run_import",
]
