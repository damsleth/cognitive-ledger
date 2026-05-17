"""Corpus service - the single source of truth for the web layer.

Wraps :mod:`ledger.browse` and :mod:`ledger.notes` so route handlers
never touch the filesystem directly. Builds a small in-memory index
(stem -> filesystem path) at construction time for fast wikilink
resolution; everything else is re-read on demand so that edits made
through ``$EDITOR`` or the CLI show up on the next page load.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ledger import browse as browse_lib
from ledger.config import LedgerConfig, get_config
from ledger.parsing import parse_frontmatter_text
from ledger.parsing.links import extract_links


@dataclass(frozen=True)
class NoteTypeInfo:
    """Display metadata for a note type sidebar entry."""

    key: str
    """Internal key, e.g. ``"facts"``. Used in URLs and ledger.browse calls."""

    label: str
    """Human label, e.g. ``"fact"``. Already singular per ledger conventions."""

    folder: str
    """Logical folder name, e.g. ``"02_facts"``."""

    count: int
    """Number of notes currently on disk."""


class Corpus:
    """Read-only view over the ledger note corpus.

    Construction scans the configured ``ledger_notes_dir`` once to build
    a ``stem -> Path`` index. Methods that list notes call back into
    :mod:`ledger.browse` on every invocation so disk edits are visible
    without restarting the server (Phase 1 contract: no caching of
    list views).
    """

    def __init__(self, config: LedgerConfig | None = None) -> None:
        self._config = config or get_config()
        self._stem_index: dict[str, Path] = {}
        self._outgoing: dict[str, list[str]] = {}
        self._incoming: dict[str, list[str]] = {}
        self._broken_outgoing: dict[str, list[str]] = {}
        self._refresh_stem_index()
        self._rebuild_link_maps()

    @property
    def config(self) -> LedgerConfig:
        return self._config

    @property
    def notes_dir(self) -> Path:
        notes_dir = self._config.ledger_notes_dir
        assert notes_dir is not None, "ledger_notes_dir is unset"
        return Path(notes_dir)

    def reload(self) -> None:
        """Re-scan the stem index and rebuild the link maps.

        Cheap: one directory listing per type plus a single read of each
        note body. Called after the user runs ``ledger sleep index`` and
        hits ``/admin/reload`` to pick up new notes without restarting.
        """
        self._refresh_stem_index()
        self._rebuild_link_maps()

    # ------------------------------------------------------------------
    # Note types
    # ------------------------------------------------------------------

    def note_types(self) -> list[NoteTypeInfo]:
        """Return note types in the canonical sidebar order, with counts."""
        cfg_types: dict[str, dict[str, Any]] = self._config.note_types
        ordered_keys = list(self._config.core_note_types)
        # Include any non-core types defined in config (e.g. identity).
        for key in cfg_types:
            if key not in ordered_keys:
                ordered_keys.append(key)

        infos: list[NoteTypeInfo] = []
        for key in ordered_keys:
            meta = cfg_types.get(key)
            if not meta:
                continue
            dir_path = self._config.note_type_dir(key)
            count = sum(1 for _ in dir_path.glob("*.md")) if dir_path.is_dir() else 0
            infos.append(
                NoteTypeInfo(
                    key=key,
                    label=str(meta.get("label", key)),
                    folder=dir_path.name,
                    count=count,
                )
            )
        return infos

    def note_type(self, key: str) -> NoteTypeInfo | None:
        for info in self.note_types():
            if info.key == key:
                return info
        return None

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_by_type(
        self, note_type: str, *, loop_status: str | None = None
    ) -> list[browse_lib.BrowseItem]:
        """List notes for a type. ``"all"`` aggregates across types."""
        if note_type == "all":
            return browse_lib.sorted_items("all")
        if not self.note_type(note_type):
            return []
        if note_type == "loops":
            return browse_lib.sorted_items("loops", loop_status=loop_status)
        return browse_lib.sorted_items(note_type)

    def recent(self, limit: int = 30) -> list[browse_lib.BrowseItem]:
        """Most recently updated notes across all types."""
        return browse_lib.sorted_items("all")[:limit]

    # ------------------------------------------------------------------
    # Single-note resolution
    # ------------------------------------------------------------------

    def get_by_stem(self, stem: str) -> browse_lib.BrowseItem | None:
        """Resolve a note by filename stem (e.g. ``"fact__nocos_account"``)."""
        # Refresh lazily on miss: a new note added since startup is rare
        # but should still be reachable.
        path = self._stem_index.get(stem)
        if path is None:
            self._refresh_stem_index()
            path = self._stem_index.get(stem)
        if path is None or not path.is_file():
            return None
        note_type = self._infer_type(path)
        if note_type == "loops":
            return browse_lib.loop_item(path)
        return browse_lib.generic_item(path, note_type)

    def stem_exists(self, stem: str) -> bool:
        if stem in self._stem_index:
            return True
        self._refresh_stem_index()
        return stem in self._stem_index

    # ------------------------------------------------------------------
    # Backlinks
    # ------------------------------------------------------------------

    def outgoing_stems(self, stem: str) -> list[str]:
        """Resolved wikilink targets in this note's body, deduplicated."""
        return list(self._outgoing.get(stem, ()))

    def incoming_stems(self, stem: str) -> list[str]:
        """Other notes that reference this stem via a wikilink."""
        return list(self._incoming.get(stem, ()))

    def broken_outgoing(self, stem: str) -> list[str]:
        """Wikilink targets in this note that don't resolve to a known stem."""
        return list(self._broken_outgoing.get(stem, ()))

    def link_titles(self, stems: list[str]) -> list[tuple[str, str]]:
        """Map a list of stems to ``(stem, title)`` pairs preserving order."""
        out: list[tuple[str, str]] = []
        for s in stems:
            item = self.get_by_stem(s)
            out.append((s, (item.title.strip() if item and item.title else s)))
        return out

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rebuild_link_maps(self) -> None:
        """Walk every note body once to derive outgoing + reverse link maps.

        Uses :func:`ledger.parsing.links.extract_links` so we share the
        wikilink parser with the rest of the codebase. Unresolved
        targets land in ``_broken_outgoing`` so the note detail view can
        warn about them without re-parsing.
        """
        outgoing: dict[str, list[str]] = {}
        incoming: dict[str, list[str]] = {}
        broken: dict[str, list[str]] = {}

        for stem, path in self._stem_index.items():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            _frontmatter, body = parse_frontmatter_text(text)

            out_resolved: list[str] = []
            out_broken: list[str] = []
            seen_out: set[str] = set()
            seen_broken: set[str] = set()

            for link in extract_links(body):
                if not link.is_wiki_link:
                    continue
                target = self._normalize_link_target(link.target)
                if not target or target == stem:
                    continue
                if target in self._stem_index:
                    if target in seen_out:
                        continue
                    seen_out.add(target)
                    out_resolved.append(target)
                else:
                    if target in seen_broken:
                        continue
                    seen_broken.add(target)
                    out_broken.append(target)

            if out_resolved:
                outgoing[stem] = out_resolved
            if out_broken:
                broken[stem] = out_broken

            for target in out_resolved:
                incoming.setdefault(target, []).append(stem)

        # Stable, deduplicated ordering for the incoming list.
        for target, sources in incoming.items():
            seen: set[str] = set()
            ordered: list[str] = []
            for s in sources:
                if s in seen:
                    continue
                seen.add(s)
                ordered.append(s)
            incoming[target] = sorted(ordered)

        self._outgoing = outgoing
        self._incoming = incoming
        self._broken_outgoing = broken

    @staticmethod
    def _normalize_link_target(target: str) -> str:
        target = target.strip()
        if "/" in target:
            target = target.rsplit("/", 1)[-1]
        if target.endswith(".md"):
            target = target[:-3]
        return target

    def _refresh_stem_index(self) -> None:
        index: dict[str, Path] = {}
        for key in self._config.note_types:
            dir_path = self._config.note_type_dir(key)
            if not dir_path.is_dir():
                continue
            for md_path in dir_path.glob("*.md"):
                index[md_path.stem] = md_path
        self._stem_index = index

    def _infer_type(self, path: Path) -> str:
        for key in self._config.note_types:
            dir_path = self._config.note_type_dir(key)
            if path.is_relative_to(dir_path):
                return key
        return "facts"
