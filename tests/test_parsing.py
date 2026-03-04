"""Tests for ledger.parsing module."""

import unittest
from datetime import timezone

from ledger.parsing.frontmatter import (
    strip_quotes,
    strip_inline_comment,
    parse_inline_list,
    parse_scalar,
    parse_frontmatter_lines,
    parse_frontmatter_text,
    parse_timestamp,
    normalize_tags,
    to_parsed_frontmatter,
)
from ledger.parsing.sections import (
    normalize_section_name,
    parse_sections,
    extract_title,
    first_content_line,
    first_checkbox,
    shorten,
)
from ledger.parsing.links import (
    extract_links,
    extract_link_tokens,
)
from ledger.parsing.tokenizer import tokenize


class TestStripQuotes(unittest.TestCase):
    """Tests for strip_quotes function."""

    def test_double_quotes(self):
        self.assertEqual(strip_quotes('"hello"'), "hello")

    def test_single_quotes(self):
        self.assertEqual(strip_quotes("'hello'"), "hello")

    def test_no_quotes(self):
        self.assertEqual(strip_quotes("hello"), "hello")

    def test_mismatched_quotes(self):
        self.assertEqual(strip_quotes('"hello\''), '"hello\'')

    def test_empty_string(self):
        self.assertEqual(strip_quotes(""), "")

    def test_quotes_only(self):
        self.assertEqual(strip_quotes('""'), "")


class TestStripInlineComment(unittest.TestCase):
    """Tests for strip_inline_comment function."""

    def test_simple_comment(self):
        self.assertEqual(strip_inline_comment("value # comment"), "value")

    def test_no_comment(self):
        self.assertEqual(strip_inline_comment("value"), "value")

    def test_comment_in_double_quotes(self):
        self.assertEqual(
            strip_inline_comment('"value # not comment"'),
            '"value # not comment"',
        )

    def test_comment_in_single_quotes(self):
        self.assertEqual(
            strip_inline_comment("'value # not comment'"),
            "'value # not comment'",
        )

    def test_comment_after_quoted(self):
        self.assertEqual(
            strip_inline_comment('"value" # comment'),
            '"value"',
        )


class TestParseInlineList(unittest.TestCase):
    """Tests for parse_inline_list function."""

    def test_simple_list(self):
        self.assertEqual(parse_inline_list("[a, b, c]"), ["a", "b", "c"])

    def test_quoted_items(self):
        self.assertEqual(parse_inline_list('["a", "b"]'), ["a", "b"])

    def test_empty_list(self):
        self.assertEqual(parse_inline_list("[]"), [])

    def test_single_item(self):
        self.assertEqual(parse_inline_list("[solo]"), ["solo"])

    def test_mixed_quotes(self):
        self.assertEqual(parse_inline_list("[plain, 'quoted', \"double\"]"), ["plain", "quoted", "double"])


class TestParseScalar(unittest.TestCase):
    """Tests for parse_scalar function."""

    def test_integer(self):
        self.assertEqual(parse_scalar("42"), 42)

    def test_float(self):
        self.assertEqual(parse_scalar("3.14"), 3.14)

    def test_true(self):
        self.assertTrue(parse_scalar("true"))
        self.assertTrue(parse_scalar("yes"))

    def test_false(self):
        self.assertFalse(parse_scalar("false"))
        self.assertFalse(parse_scalar("no"))

    def test_string(self):
        self.assertEqual(parse_scalar("hello"), "hello")

    def test_list(self):
        self.assertEqual(parse_scalar("[a, b]"), ["a", "b"])

    def test_quoted_string(self):
        self.assertEqual(parse_scalar('"hello"'), "hello")

    def test_with_comment(self):
        self.assertEqual(parse_scalar("value # comment"), "value")

    def test_empty(self):
        self.assertEqual(parse_scalar(""), "")


class TestParseFrontmatterLines(unittest.TestCase):
    """Tests for parse_frontmatter_lines function."""

    def test_simple_key_value(self):
        lines = ["key: value"]
        result = parse_frontmatter_lines(lines)
        self.assertEqual(result["key"], "value")

    def test_multiple_keys(self):
        lines = [
            "created: 2026-02-13T10:00:00Z",
            "confidence: 0.9",
        ]
        result = parse_frontmatter_lines(lines)
        self.assertEqual(result["created"], "2026-02-13T10:00:00Z")
        self.assertEqual(result["confidence"], 0.9)

    def test_inline_list(self):
        lines = ["tags: [alpha, beta, gamma]"]
        result = parse_frontmatter_lines(lines)
        self.assertEqual(result["tags"], ["alpha", "beta", "gamma"])

    def test_block_list(self):
        lines = [
            "tags:",
            "  - alpha",
            "  - beta",
            "  - gamma",
        ]
        result = parse_frontmatter_lines(lines)
        self.assertEqual(result["tags"], ["alpha", "beta", "gamma"])

    def test_comment_lines_ignored(self):
        lines = [
            "# This is a comment",
            "key: value",
        ]
        result = parse_frontmatter_lines(lines)
        self.assertEqual(result, {"key": "value"})

    def test_inline_comment_stripped(self):
        lines = ['source: "inferred" # comment']
        result = parse_frontmatter_lines(lines)
        self.assertEqual(result["source"], "inferred")


class TestParseFrontmatterText(unittest.TestCase):
    """Tests for parse_frontmatter_text function."""

    def test_with_frontmatter(self):
        text = """---
created: 2026-02-13T10:00:00Z
tags: [test]
---

# Title

Body content.
"""
        fm, body = parse_frontmatter_text(text)
        self.assertEqual(fm["created"], "2026-02-13T10:00:00Z")
        self.assertIn("# Title", body)

    def test_without_frontmatter(self):
        text = "# Title\n\nBody content."
        fm, body = parse_frontmatter_text(text)
        self.assertEqual(fm, {})
        self.assertEqual(body, text)

    def test_unclosed_frontmatter(self):
        text = "---\ncreated: value\n# Never closed"
        fm, body = parse_frontmatter_text(text)
        self.assertEqual(fm, {})
        self.assertEqual(body, text)


class TestParseTimestamp(unittest.TestCase):
    """Tests for parse_timestamp function."""

    def test_valid_timestamp(self):
        ts = parse_timestamp("2026-02-13T10:00:00Z")
        self.assertEqual(ts.year, 2026)
        self.assertEqual(ts.month, 2)
        self.assertEqual(ts.tzinfo, timezone.utc)

    def test_empty_string(self):
        self.assertIsNone(parse_timestamp(""))

    def test_invalid_format(self):
        self.assertIsNone(parse_timestamp("not-a-date"))


class TestNormalizeTags(unittest.TestCase):
    """Tests for normalize_tags function."""

    def test_list_input(self):
        self.assertEqual(normalize_tags(["Alpha", "BETA"]), ["alpha", "beta"])

    def test_string_input(self):
        self.assertEqual(normalize_tags("single"), ["single"])

    def test_none_input(self):
        self.assertEqual(normalize_tags(None), [])

    def test_deduplication(self):
        self.assertEqual(normalize_tags(["a", "A", "a"]), ["a"])

    def test_embedded_list(self):
        self.assertEqual(normalize_tags(["[x, y]"]), ["x", "y"])


class TestNormalizeSectionName(unittest.TestCase):
    """Tests for normalize_section_name function."""

    def test_next_actions_alias(self):
        self.assertEqual(normalize_section_name("Next Actions"), "next action")

    def test_next_steps_alias(self):
        self.assertEqual(normalize_section_name("Next Steps"), "next action")

    def test_why_matters_alias(self):
        self.assertEqual(normalize_section_name("Why matters"), "why it matters")

    def test_plain_section(self):
        self.assertEqual(normalize_section_name("Context"), "context")


class TestParseSections(unittest.TestCase):
    """Tests for parse_sections function."""

    def test_h2_sections(self):
        body = """## Statement

Some statement.

## Context

Some context.
"""
        sections = parse_sections(body)
        self.assertIn("statement", sections)
        self.assertIn("context", sections)
        # Content lines may include blank lines, so check full list
        statement_content = "\n".join(sections["statement"])
        self.assertIn("Some statement.", statement_content)

    def test_h3_sections(self):
        body = """### Sub Section

Content.
"""
        sections = parse_sections(body)
        self.assertIn("sub section", sections)


class TestExtractTitle(unittest.TestCase):
    """Tests for extract_title function."""

    def test_h1_title(self):
        body = "# My Title\n\nContent."
        self.assertEqual(extract_title(body), "My Title")

    def test_loop_prefix_stripped(self):
        body = "# Loop: Fix the bug\n\nContent."
        self.assertEqual(extract_title(body), "Fix the bug")

    def test_no_title(self):
        body = "Just content without title."
        self.assertEqual(extract_title(body), "")


class TestFirstContentLine(unittest.TestCase):
    """Tests for first_content_line function."""

    def test_skips_headers(self):
        body = "# Title\n\nFirst content."
        self.assertEqual(first_content_line(body), "First content.")

    def test_extracts_list_item(self):
        body = "# Title\n\n- List item"
        self.assertEqual(first_content_line(body), "List item")


class TestFirstCheckbox(unittest.TestCase):
    """Tests for first_checkbox function."""

    def test_unchecked(self):
        text = "- [ ] Todo item"
        self.assertEqual(first_checkbox(text), "Todo item")

    def test_checked(self):
        text = "- [x] Done item"
        self.assertEqual(first_checkbox(text), "Done item")

    def test_no_checkbox(self):
        text = "- Plain item"
        self.assertEqual(first_checkbox(text), "")


class TestShorten(unittest.TestCase):
    """Tests for shorten function."""

    def test_short_text(self):
        self.assertEqual(shorten("hello", 10), "hello")

    def test_long_text(self):
        result = shorten("this is a very long text that needs truncation", 20)
        self.assertTrue(result.endswith("..."))
        self.assertLessEqual(len(result), 20)

    def test_zero_width(self):
        self.assertEqual(shorten("hello", 0), "")


class TestExtractLinks(unittest.TestCase):
    """Tests for extract_links function."""

    def test_wiki_link(self):
        body = "See [[other_note]]."
        links = extract_links(body)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].target, "other_note")
        self.assertTrue(links[0].is_wiki_link)

    def test_wiki_link_with_display(self):
        body = "See [[note|Display Text]]."
        links = extract_links(body)
        self.assertEqual(links[0].target, "note")
        self.assertEqual(links[0].display_text, "Display Text")

    def test_markdown_link(self):
        body = "See [the doc](path/to/doc.md)."
        links = extract_links(body)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].target, "path/to/doc.md")
        self.assertFalse(links[0].is_wiki_link)


class TestExtractLinkTokens(unittest.TestCase):
    """Tests for extract_link_tokens function."""

    def test_captures_from_markdown_links(self):
        body = "See [Progressive Disclosure](notes/06_concepts/concept__progressive_disclosure.md)"
        tokens = extract_link_tokens(body)
        self.assertIn("progressive", tokens)
        self.assertIn("disclosure", tokens)

    def test_captures_from_wiki_links(self):
        body = "See [[concept__compressed_attention]]."
        tokens = extract_link_tokens(body)
        self.assertIn("compressed", tokens)
        self.assertIn("attention", tokens)


class TestTokenize(unittest.TestCase):
    """Tests for tokenize function."""

    def test_basic_tokenization(self):
        tokens = tokenize("Hello World")
        self.assertEqual(tokens, {"hello", "world"})

    def test_stopwords_removed(self):
        tokens = tokenize("the quick brown fox")
        self.assertNotIn("the", tokens)
        self.assertIn("quick", tokens)

    def test_numbers_included(self):
        tokens = tokenize("version 42")
        self.assertIn("42", tokens)
        self.assertIn("version", tokens)

    def test_custom_stopwords(self):
        tokens = tokenize("hello world", stopwords=frozenset({"hello"}))
        self.assertNotIn("hello", tokens)
        self.assertIn("world", tokens)


if __name__ == "__main__":
    unittest.main()
