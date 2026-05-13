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

    Returns the rewritten body plus a list of broken stems (in document
    order, deduplicated).
    """
    broken: list[str] = []
    seen_broken: set[str] = set()

    def _sub(match: re.Match[str]) -> str:
        raw_target = match.group(1)
        alias = match.group(2)
        stem = _resolve_stem(raw_target)
        display = (alias or raw_target).strip()
        if corpus.stem_exists(stem):
            return f"[{display}](/note/{stem})"
        if stem not in seen_broken:
            seen_broken.add(stem)
            broken.append(stem)
        # Render as inline span via raw HTML... but we disabled html
        # passthrough, so emit a markdown placeholder that the template
        # styles via the ``broken-link`` class through a post-processing
        # step. The simplest path is a fenced inline-code marker the
        # template can swap; instead, we use a stable HTML comment plus
        # the display text wrapped in italics to keep CommonMark output
        # valid, then post-process the rendered HTML below.
        return f"§§BROKEN§§{display}§§/BROKEN§§"

    return _WIKILINK_RE.sub(_sub, body), broken


_BROKEN_HTML_RE = re.compile(r"§§BROKEN§§(.*?)§§/BROKEN§§", re.DOTALL)
_LEADING_H1_RE = re.compile(r"\A\s*#\s+[^\n]+\n+")


def _strip_leading_h1(body: str) -> str:
    """Drop a single leading H1 - the template renders the title already."""
    return _LEADING_H1_RE.sub("", body, count=1)


def render_body(body: str, corpus: Corpus) -> RenderedNote:
    """Render a note body to HTML, rewriting wikilinks."""
    rewritten, broken = rewrite_wikilinks(_strip_leading_h1(body), corpus)
    html = _md().render(rewritten)
    html = _BROKEN_HTML_RE.sub(
        lambda m: f'<span class="broken-link" title="unresolved wikilink">{m.group(1)}</span>',
        html,
    )
    return RenderedNote(html=html, broken_links=tuple(broken))
