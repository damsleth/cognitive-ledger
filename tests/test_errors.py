"""Tests for ledger.errors and ledger.validation modules."""

import unittest
from pathlib import Path
from unittest.mock import patch

from ledger.errors import (
    LedgerError,
    ParseError,
    FrontmatterParseError,
    NoteError,
    NoteNotFoundError,
    NoteReadError,
    ValidationError,
    QueryValidationError,
    ScopeValidationError,
    PathValidationError,
    EvalCaseValidationError,
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
)


class TestLedgerError(unittest.TestCase):
    """Tests for base LedgerError class."""

    def test_basic_error(self):
        err = LedgerError("Something went wrong")
        self.assertEqual(str(err), "Something went wrong")

    def test_error_with_context(self):
        err = LedgerError("Failed", path="/test", code=42)
        self.assertIn("path=", str(err))
        self.assertIn("code=", str(err))
        self.assertEqual(err.context["path"], "/test")

    def test_inheritance(self):
        self.assertTrue(issubclass(ParseError, LedgerError))
        self.assertTrue(issubclass(NoteError, LedgerError))
        self.assertTrue(issubclass(ValidationError, LedgerError))


class TestFrontmatterParseError(unittest.TestCase):
    """Tests for FrontmatterParseError."""

    def test_with_line_number(self):
        err = FrontmatterParseError("Invalid YAML", line_number=5)
        self.assertEqual(err.line_number, 5)

    def test_with_file_path(self):
        err = FrontmatterParseError("Invalid", file_path="/test.md")
        self.assertEqual(err.file_path, "/test.md")


class TestNoteErrors(unittest.TestCase):
    """Tests for note-related errors."""

    def test_note_not_found(self):
        err = NoteNotFoundError("/path/to/note.md")
        self.assertIn("/path/to/note.md", str(err))
        self.assertEqual(err.path, "/path/to/note.md")

    def test_note_read_error(self):
        err = NoteReadError("/test.md", "permission denied")
        self.assertIn("permission denied", str(err))


class TestValidationErrors(unittest.TestCase):
    """Tests for validation errors."""

    def test_query_validation_error(self):
        err = QueryValidationError("bad query", "too short")
        self.assertIn("too short", str(err))

    def test_scope_validation_error(self):
        err = ScopeValidationError("invalid")
        self.assertIn("invalid", str(err))

    def test_path_validation_error(self):
        err = PathValidationError("../../../etc/passwd", "directory traversal")
        self.assertIn("traversal", str(err))


class TestEvalCaseValidationError(unittest.TestCase):
    """Tests for EvalCaseValidationError."""

    def test_with_case_index(self):
        err = EvalCaseValidationError("Missing field", case_index=3)
        self.assertEqual(err.case_index, 3)

    def test_with_field(self):
        err = EvalCaseValidationError("Invalid", field="expected")
        self.assertEqual(err.field, "expected")


class TestValidateQuery(unittest.TestCase):
    """Tests for validate_query function."""

    def test_valid_query(self):
        result = validate_query("hello world")
        self.assertEqual(result, "hello world")

    def test_strips_whitespace(self):
        result = validate_query("  hello  ")
        self.assertEqual(result, "hello")

    def test_empty_query_raises(self):
        with self.assertRaises(QueryValidationError):
            validate_query("")

    def test_none_query_raises(self):
        with self.assertRaises(QueryValidationError):
            validate_query(None)

    def test_too_long_raises(self):
        with self.assertRaises(QueryValidationError):
            validate_query("x" * 3000)

    def test_null_bytes_raises(self):
        with self.assertRaises(QueryValidationError):
            validate_query("hello\x00world")


class TestValidateScope(unittest.TestCase):
    """Tests for validate_scope function."""

    def test_valid_scope(self):
        self.assertEqual(validate_scope("dev"), "dev")

    def test_normalizes_case(self):
        self.assertEqual(validate_scope("DEV"), "dev")

    def test_life_alias(self):
        self.assertEqual(validate_scope("life"), "personal")

    def test_all_allowed_by_default(self):
        self.assertEqual(validate_scope("all"), "all")

    def test_all_disallowed_when_disabled(self):
        with self.assertRaises(ScopeValidationError):
            validate_scope("all", allow_all=False)

    def test_invalid_scope_raises(self):
        with self.assertRaises(ScopeValidationError):
            validate_scope("invalid_scope")


class TestValidateLimit(unittest.TestCase):
    """Tests for validate_limit function."""

    def test_valid_limit(self):
        self.assertEqual(validate_limit(10), 10)

    def test_converts_string(self):
        self.assertEqual(validate_limit("50"), 50)

    def test_too_low_raises(self):
        with self.assertRaises(ValueError):
            validate_limit(0)

    def test_too_high_raises(self):
        with self.assertRaises(ValueError):
            validate_limit(10000)

    def test_non_integer_raises(self):
        with self.assertRaises(ValueError):
            validate_limit("not a number")


class TestValidatePathWithinRoot(unittest.TestCase):
    """Tests for validate_path_within_root function."""

    def test_relative_path_within_root(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()  # Resolve to handle /private/var symlinks
            (root / "subdir").mkdir()
            result = validate_path_within_root("subdir/file.md", root)
            # Just check result is under the resolved root
            self.assertTrue(root in result.parents or str(result).startswith(str(root)))

    def test_directory_traversal_blocked(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaises(PathValidationError):
                validate_path_within_root("../../../etc/passwd", root)

    def test_null_bytes_blocked(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaises(PathValidationError):
                validate_path_within_root("file\x00.md", root)


class TestValidateNoteType(unittest.TestCase):
    """Tests for validate_note_type function."""

    def test_valid_type(self):
        self.assertEqual(validate_note_type("facts"), "facts")

    def test_normalizes_case(self):
        self.assertEqual(validate_note_type("FACTS"), "facts")

    def test_all_allowed(self):
        self.assertEqual(validate_note_type("all"), "all")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            validate_note_type("invalid")


class TestValidateStatus(unittest.TestCase):
    """Tests for validate_status function."""

    def test_valid_status(self):
        self.assertEqual(validate_status("open"), "open")

    def test_normalizes_case(self):
        self.assertEqual(validate_status("OPEN"), "open")

    def test_all_allowed(self):
        self.assertEqual(validate_status("all"), "all")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            validate_status("invalid")


class TestSanitizeFilename(unittest.TestCase):
    """Tests for sanitize_filename function."""

    def test_simple_text(self):
        self.assertEqual(sanitize_filename("hello world"), "hello_world")

    def test_removes_special_chars(self):
        self.assertEqual(sanitize_filename("hello@world!"), "helloworld")

    def test_collapses_underscores(self):
        self.assertEqual(sanitize_filename("hello   world"), "hello_world")

    def test_truncates_long_names(self):
        result = sanitize_filename("a" * 200, max_length=50)
        self.assertEqual(len(result), 50)

    def test_empty_becomes_untitled(self):
        self.assertEqual(sanitize_filename("!!!"), "untitled")


if __name__ == "__main__":
    unittest.main()
