"""Tests for privacy fence tag stripping."""

import unittest

from ledger.parsing.privacy import strip_private_tags


class TestStripPrivateTags(unittest.TestCase):

    def test_no_tags(self):
        assert strip_private_tags("Hello world") == "Hello world"

    def test_empty_string(self):
        assert strip_private_tags("") == ""

    def test_single_inline_tag(self):
        result = strip_private_tags("before <private>secret</private> after")
        assert result == "before  after"

    def test_multiline_tag(self):
        text = "public\n<private>\nline1\nline2\n</private>\nmore public"
        result = strip_private_tags(text)
        assert result == "public\n\nmore public"

    def test_multiple_tags(self):
        text = "<private>a</private> mid <private>b</private> end"
        result = strip_private_tags(text)
        assert result == " mid  end"

    def test_case_insensitive(self):
        result = strip_private_tags("x <Private>y</PRIVATE> z")
        assert result == "x  z"

    def test_unclosed_tag_strips_remainder(self):
        text = "this mentions <private> without closing"
        # Unclosed tag strips everything after it (safer: over-redact)
        assert strip_private_tags(text) == "this mentions "

    def test_preserves_surrounding_content(self):
        text = "keep this\n<private>remove this</private>\nand this"
        result = strip_private_tags(text)
        assert "keep this" in result
        assert "and this" in result
        assert "remove this" not in result

    def test_adjacent_tags(self):
        text = "<private>a</private><private>b</private>"
        assert strip_private_tags(text) == ""

    def test_nested_tags(self):
        text = "<private>outer <private>inner</private> rest</private>"
        result = strip_private_tags(text)
        assert "inner" not in result
        assert "outer" not in result
        assert "rest" not in result


class TestStripPrivateTagsFromParsing(unittest.TestCase):
    """Test that strip_private_tags is importable from the parsing package."""

    def test_importable(self):
        from ledger.parsing import strip_private_tags as fn
        assert callable(fn)
        assert fn("x <private>y</private> z") == "x  z"


if __name__ == "__main__":
    unittest.main()
