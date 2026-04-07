"""Tests for ledger.validation module."""

import tempfile
import unittest
from pathlib import Path

from ledger.errors import (
  QueryValidationError,
  ScopeValidationError,
  PathValidationError,
)
from ledger.validation import (
  validate_query,
  validate_scope,
  validate_limit,
  validate_path_within_root,
  validate_note_type,
  validate_status,
  sanitize_filename,
  VALID_SCOPES,
  VALID_SCOPES_WITH_ALL,
)


class TestValidateQuery(unittest.TestCase):
  """Tests for validate_query."""

  def test_valid_query(self):
    self.assertEqual(validate_query("hello world"), "hello world")

  def test_strips_whitespace(self):
    self.assertEqual(validate_query("  spaced  "), "spaced")

  def test_empty_string_raises(self):
    with self.assertRaises(QueryValidationError):
      validate_query("")

  def test_whitespace_only_raises(self):
    with self.assertRaises(QueryValidationError):
      validate_query("   ")

  def test_none_raises(self):
    with self.assertRaises(QueryValidationError):
      validate_query(None)

  def test_too_long_raises(self):
    with self.assertRaises(QueryValidationError):
      validate_query("x" * 2001)

  def test_exactly_max_length(self):
    result = validate_query("x" * 2000)
    self.assertEqual(len(result), 2000)

  def test_custom_max_length(self):
    with self.assertRaises(QueryValidationError):
      validate_query("hello", max_length=3)

  def test_null_bytes_raises(self):
    with self.assertRaises(QueryValidationError):
      validate_query("hello\x00world")

  def test_unicode_query(self):
    result = validate_query("norsk tekst med ae oe aa")
    self.assertEqual(result, "norsk tekst med ae oe aa")

  def test_emoji_query(self):
    result = validate_query("search for rockets")
    self.assertEqual(result, "search for rockets")


class TestValidateScope(unittest.TestCase):
  """Tests for validate_scope."""

  def test_all_valid_scopes(self):
    for scope in ("home", "work", "dev", "personal", "meta"):
      self.assertEqual(validate_scope(scope), scope)

  def test_life_alias_to_personal(self):
    self.assertEqual(validate_scope("life"), "personal")

  def test_normalizes_case(self):
    self.assertEqual(validate_scope("DEV"), "dev")
    self.assertEqual(validate_scope("Work"), "work")

  def test_strips_whitespace(self):
    self.assertEqual(validate_scope("  dev  "), "dev")

  def test_all_allowed_by_default(self):
    self.assertEqual(validate_scope("all"), "all")

  def test_all_disallowed(self):
    with self.assertRaises(ScopeValidationError):
      validate_scope("all", allow_all=False)

  def test_invalid_scope_raises(self):
    with self.assertRaises(ScopeValidationError):
      validate_scope("bogus")

  def test_none_raises(self):
    with self.assertRaises(ScopeValidationError):
      validate_scope(None)

  def test_empty_string_raises(self):
    with self.assertRaises(ScopeValidationError):
      validate_scope("")

  def test_valid_scopes_constant(self):
    self.assertIn("home", VALID_SCOPES)
    self.assertIn("work", VALID_SCOPES)
    self.assertIn("dev", VALID_SCOPES)
    self.assertIn("personal", VALID_SCOPES)
    self.assertIn("meta", VALID_SCOPES)
    self.assertNotIn("all", VALID_SCOPES)

  def test_valid_scopes_with_all_constant(self):
    self.assertIn("all", VALID_SCOPES_WITH_ALL)


class TestValidateLimit(unittest.TestCase):
  """Tests for validate_limit."""

  def test_valid_limit(self):
    self.assertEqual(validate_limit(10), 10)

  def test_min_boundary(self):
    self.assertEqual(validate_limit(1), 1)

  def test_max_boundary(self):
    self.assertEqual(validate_limit(1000), 1000)

  def test_converts_string_to_int(self):
    self.assertEqual(validate_limit("42"), 42)

  def test_below_min_raises(self):
    with self.assertRaises(ValueError):
      validate_limit(0)

  def test_above_max_raises(self):
    with self.assertRaises(ValueError):
      validate_limit(1001)

  def test_non_integer_string_raises(self):
    with self.assertRaises(ValueError):
      validate_limit("abc")

  def test_none_raises(self):
    with self.assertRaises(ValueError):
      validate_limit(None)

  def test_custom_range(self):
    self.assertEqual(validate_limit(5, min_val=5, max_val=10), 5)
    self.assertEqual(validate_limit(10, min_val=5, max_val=10), 10)
    with self.assertRaises(ValueError):
      validate_limit(4, min_val=5, max_val=10)
    with self.assertRaises(ValueError):
      validate_limit(11, min_val=5, max_val=10)

  def test_float_truncated(self):
    self.assertEqual(validate_limit(5.9), 5)


class TestValidatePathWithinRoot(unittest.TestCase):
  """Tests for validate_path_within_root."""

  def test_relative_path_within_root(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir).resolve()
      (root / "subdir").mkdir()
      result = validate_path_within_root("subdir/file.md", root)
      self.assertTrue(str(result).startswith(str(root)))

  def test_simple_filename(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir).resolve()
      result = validate_path_within_root("note.md", root)
      self.assertEqual(result, root / "note.md")

  def test_directory_traversal_blocked(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir).resolve()
      with self.assertRaises(PathValidationError):
        validate_path_within_root("../../../etc/passwd", root)

  def test_double_dot_traversal_blocked(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir).resolve()
      with self.assertRaises(PathValidationError):
        validate_path_within_root("subdir/../../outside", root)

  def test_null_bytes_blocked(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir).resolve()
      with self.assertRaises(PathValidationError):
        validate_path_within_root("file\x00.md", root)

  def test_absolute_path_within_root(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir).resolve()
      abs_path = root / "notes" / "test.md"
      result = validate_path_within_root(str(abs_path), root)
      self.assertTrue(str(result).startswith(str(root)))

  def test_absolute_path_outside_root_blocked(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir).resolve()
      with self.assertRaises(PathValidationError):
        validate_path_within_root("/etc/passwd", root)


class TestValidateNoteType(unittest.TestCase):
  """Tests for validate_note_type."""

  def test_valid_types(self):
    for nt in ("identity", "facts", "preferences", "goals", "loops", "concepts"):
      self.assertEqual(validate_note_type(nt), nt)

  def test_normalizes_case(self):
    self.assertEqual(validate_note_type("FACTS"), "facts")
    self.assertEqual(validate_note_type("Loops"), "loops")

  def test_strips_whitespace(self):
    self.assertEqual(validate_note_type("  goals  "), "goals")

  def test_all_returns_all(self):
    self.assertEqual(validate_note_type("all"), "all")

  def test_invalid_raises(self):
    with self.assertRaises(ValueError):
      validate_note_type("invalid_type")

  def test_none_raises(self):
    with self.assertRaises(ValueError):
      validate_note_type(None)

  def test_empty_string_raises(self):
    with self.assertRaises(ValueError):
      validate_note_type("")


class TestValidateStatus(unittest.TestCase):
  """Tests for validate_status."""

  def test_valid_statuses(self):
    for s in ("open", "closed", "blocked", "snoozed"):
      self.assertEqual(validate_status(s), s)

  def test_normalizes_case(self):
    self.assertEqual(validate_status("OPEN"), "open")
    self.assertEqual(validate_status("Blocked"), "blocked")

  def test_strips_whitespace(self):
    self.assertEqual(validate_status("  closed  "), "closed")

  def test_all_returns_all(self):
    self.assertEqual(validate_status("all"), "all")

  def test_invalid_raises(self):
    with self.assertRaises(ValueError):
      validate_status("pending")

  def test_none_raises(self):
    with self.assertRaises(ValueError):
      validate_status(None)

  def test_empty_string_raises(self):
    with self.assertRaises(ValueError):
      validate_status("")


class TestSanitizeFilename(unittest.TestCase):
  """Tests for sanitize_filename."""

  def test_simple_text(self):
    self.assertEqual(sanitize_filename("hello world"), "hello_world")

  def test_preserves_hyphens_underscores(self):
    self.assertEqual(sanitize_filename("my-note_v2"), "my-note_v2")

  def test_removes_special_chars(self):
    self.assertEqual(sanitize_filename("hello@world!#$%"), "helloworld")

  def test_lowercases(self):
    self.assertEqual(sanitize_filename("Hello World"), "hello_world")

  def test_collapses_multiple_spaces(self):
    self.assertEqual(sanitize_filename("hello   world"), "hello_world")

  def test_strips_leading_trailing_underscores(self):
    self.assertEqual(sanitize_filename("  hello  "), "hello")

  def test_empty_becomes_untitled(self):
    self.assertEqual(sanitize_filename(""), "untitled")

  def test_all_special_chars_becomes_untitled(self):
    self.assertEqual(sanitize_filename("!@#$%^&*()"), "untitled")

  def test_truncates_long_names(self):
    result = sanitize_filename("a" * 200)
    self.assertEqual(len(result), 100)

  def test_custom_max_length(self):
    result = sanitize_filename("a" * 200, max_length=50)
    self.assertEqual(len(result), 50)

  def test_truncation_strips_trailing_underscore(self):
    # "a b" -> "a_b"; if we truncate at 2 we get "a_" -> stripped to "a"
    result = sanitize_filename("a b c d e f", max_length=2)
    self.assertEqual(result, "a")

  def test_mixed_unicode_and_ascii(self):
    # non-alnum unicode chars are stripped
    result = sanitize_filename("note-about-stuff")
    self.assertEqual(result, "note-about-stuff")


if __name__ == "__main__":
  unittest.main()
