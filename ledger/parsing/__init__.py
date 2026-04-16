"""Parsing utilities for Cognitive Ledger.

This package provides canonical implementations for parsing:
- YAML frontmatter
- Markdown sections
- Wiki-style and markdown links
- Text tokenization

These are THE source of truth. All other code should import from here.
"""

from .frontmatter import (
    parse_frontmatter_lines,
    parse_frontmatter_text,
    ParsedFrontmatter,
    strip_quotes,
    strip_inline_comment,
    parse_inline_list,
    parse_scalar,
    parse_timestamp,
    normalize_tags,
    to_parsed_frontmatter,
    serialize_frontmatter,
)
from .sections import (
    parse_sections,
    normalize_section_name,
    extract_title,
    first_content_line,
    first_checkbox,
    shorten,
)
from .links import (
    extract_links,
    extract_link_tokens,
    NoteLink,
)
from .privacy import (
    strip_private_tags,
)
from .tokenizer import (
    tokenize,
)

__all__ = [
    # Frontmatter
    "parse_frontmatter_lines",
    "parse_frontmatter_text",
    "ParsedFrontmatter",
    "strip_quotes",
    "strip_inline_comment",
    "parse_inline_list",
    "parse_scalar",
    "parse_timestamp",
    "normalize_tags",
    "to_parsed_frontmatter",
    "serialize_frontmatter",
    # Sections
    "parse_sections",
    "normalize_section_name",
    "extract_title",
    "first_content_line",
    "first_checkbox",
    "shorten",
    # Links
    "extract_links",
    "extract_link_tokens",
    "NoteLink",
    # Privacy
    "strip_private_tags",
    # Tokenizer
    "tokenize",
]
