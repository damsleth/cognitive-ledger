"""Markdown rendering for note bodies.

We use ``markdown-it-py`` (CommonMark-strict) and pre-process the body
to rewrite wikilinks into normal markdown links pointing at our
``/note/{stem}`` route. Anything we can't resolve to a known stem is
rendered as a span with the ``broken-link`` class so the user can see
dangling references without the page 404ing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt

from ledger.parsing import strip_private_tags
from ledger.web.services.corpus import Corpus


_WIKILINK_RE = re.compile(r"\[\[([^\]|\n]+?)(?:\|([^\]\n]+))?\]\]")


@dataclass(frozen=True)
class RenderedNote:
    html: str
    """Fully rendered HTML body, with wikilinks rewritten."""

    broken_links: tuple[str, ...]
    """Stems referenced by ``[[...]]`` that don't resolve to a known note."""


def _md() -> MarkdownIt:
    # Strict CommonMark + tables (for any tabular note bodies). No HTML
    # passthrough - notes are author-trusted but we keep the rule simple.
    return MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")


def _resolve_stem(target: str) -> str:
    """Normalize a wikilink target into a stem we can look up.

    Accepts: ``stem``, ``stem.md``, ``notes/02_facts/stem.md``, ``02_facts/stem``.
    """
    target = target.strip()
    # Strip leading paths and trailing extension.
    if "/" in target:
        target = target.rsplit("/", 1)[-1]
    if target.endswith(".md"):
        target = target[:-3]
    return target


def rewrite_wikilinks(body: str, corpus: Corpus) -> tuple[str, list[str]]:
    """Replace ``[[stem]]`` and ``[[stem|alias]]`` with markdown links.

    Bare ``[[stem]]`` links render with the target note's title as the
    display text (e.g. ``[[id__family]]`` -> "Identitet - Familie").
    Explicit ``[[stem|alias]]`` links keep the author-supplied alias.

    Returns the rewritten body plus a list of broken stems (in document
    order, deduplicated).
    """
    broken: list[str] = []
    seen_broken: set[str] = set()
    title_cache: dict[str, str] = {}

    def _display_for(stem: str, raw_target: str, alias: str | None) -> str:
        if alias is not None and alias.strip():
            return alias.strip()
        if stem not in title_cache:
            item = corpus.get_by_stem(stem)
            title_cache[stem] = (item.title.strip() if item and item.title else "")
        return title_cache[stem] or raw_target.strip()

    def _sub(match: re.Match[str]) -> str:
        raw_target = match.group(1)
        alias = match.group(2)
        stem = _resolve_stem(raw_target)
        if corpus.stem_exists(stem):
            display = _display_for(stem, raw_target, alias)
            return f"[{display}](/note/{stem})"
        # Broken link: fall back to the explicit alias or the raw target.
        if stem not in seen_broken:
            seen_broken.add(stem)
            broken.append(stem)
        display = (alias.strip() if alias else raw_target.strip())
        return f"§§BROKEN§§{display}§§/BROKEN§§"

    return _WIKILINK_RE.sub(_sub, body), broken


_BROKEN_HTML_RE = re.compile(r"§§BROKEN§§(.*?)§§/BROKEN§§", re.DOTALL)
_LEADING_H1_RE = re.compile(r"\A\s*#\s+[^\n]+\n+")


def _strip_leading_h1(body: str) -> str:
    """Drop a single leading H1 - the template renders the title already."""
    return _LEADING_H1_RE.sub("", body, count=1)


def render_body(body: str, corpus: Corpus) -> RenderedNote:
    """Render a note body to HTML, rewriting wikilinks."""
    body = strip_private_tags(body)
    rewritten, broken = rewrite_wikilinks(_strip_leading_h1(body), corpus)
    rendered_html = _md().render(rewritten)
    # m.group(1) was already escaped by markdown-it during render (html: False
    # forces angle brackets, quotes, and ampersands into entities), so the
    # span text-position is safe without further escaping.
    rendered = _BROKEN_HTML_RE.sub(
        lambda m: f'<span class="broken-link" title="unresolved wikilink">{m.group(1)}</span>',
        rendered_html,
    )
    return RenderedNote(html=rendered, broken_links=tuple(broken))
