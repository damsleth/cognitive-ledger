"""Custom exception hierarchy for Cognitive Ledger.

This module provides a structured exception hierarchy to replace
the generic `except Exception` patterns throughout the codebase.
"""

from __future__ import annotations

from typing import Any


class LedgerError(Exception):
    """Base exception for all Cognitive Ledger errors.

    All custom exceptions should inherit from this class to allow
    for consistent error handling across the application.
    """

    def __init__(self, message: str, **context: Any) -> None:
        """Initialize the error with message and optional context.

        Args:
            message: Human-readable error description.
            **context: Additional context key-value pairs.
        """
        super().__init__(message)
        self.message = message
        self.context = context

    def __str__(self) -> str:
        if self.context:
            ctx_str = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
            return f"{self.message} ({ctx_str})"
        return self.message


# --- Parse Errors ---


class ParseError(LedgerError):
    """Base class for parsing errors."""


class FrontmatterParseError(ParseError):
    """Raised when YAML frontmatter cannot be parsed.

    Examples:
        - Invalid YAML syntax
        - Missing closing delimiter
        - Invalid field types
    """

    def __init__(
        self,
        message: str,
        *,
        line_number: int | None = None,
        file_path: str | None = None,
    ) -> None:
        super().__init__(
            message,
            line_number=line_number,
            file_path=file_path,
        )
        self.line_number = line_number
        self.file_path = file_path


class SectionParseError(ParseError):
    """Raised when markdown section parsing fails."""


class TimestampParseError(ParseError):
    """Raised when a timestamp string cannot be parsed."""

    def __init__(self, value: str, expected_format: str = "ISO 8601") -> None:
        super().__init__(
            f"Invalid timestamp: {value!r}",
            value=value,
            expected_format=expected_format,
        )


# --- Note Errors ---


class NoteError(LedgerError):
    """Base class for note-related errors."""


class NoteNotFoundError(NoteError):
    """Raised when a requested note does not exist.

    NOTE: Prefer returning empty results over raising this for searches.
    Use for explicit single-note lookups only.
    """

    def __init__(self, path: str) -> None:
        super().__init__(f"Note not found: {path}", path=path)
        self.path = path


class NoteReadError(NoteError):
    """Raised when a note file cannot be read."""

    def __init__(self, path: str, reason: str | None = None) -> None:
        message = f"Cannot read note: {path}"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(message, path=path, reason=reason)


class NoteWriteError(NoteError):
    """Raised when a note file cannot be written."""

    def __init__(self, path: str, reason: str | None = None) -> None:
        message = f"Cannot write note: {path}"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(message, path=path, reason=reason)


class InvalidNoteTypeError(NoteError):
    """Raised when an invalid note type is specified."""

    def __init__(self, note_type: str, valid_types: tuple[str, ...] | None = None) -> None:
        valid = valid_types or ("facts", "preferences", "goals", "loops", "concepts")
        super().__init__(
            f"Invalid note type: {note_type!r}. Valid types: {', '.join(valid)}",
            note_type=note_type,
            valid_types=valid,
        )


# --- Validation Errors ---


class ValidationError(LedgerError):
    """Base class for input validation errors."""


class QueryValidationError(ValidationError):
    """Raised when a query string is invalid.

    Includes length limits, encoding issues, and banned characters.
    """

    def __init__(self, query: str, reason: str) -> None:
        super().__init__(
            f"Invalid query: {reason}",
            query=query[:100] + "..." if len(query) > 100 else query,
            reason=reason,
        )


class ScopeValidationError(ValidationError):
    """Raised when a scope value is invalid."""

    def __init__(self, scope: str, valid_scopes: tuple[str, ...] | None = None) -> None:
        valid = valid_scopes or ("home", "work", "dev", "personal", "meta", "all")
        super().__init__(
            f"Invalid scope: {scope!r}. Valid scopes: {', '.join(valid)}",
            scope=scope,
            valid_scopes=valid,
        )


class PathValidationError(ValidationError):
    """Raised when a path is invalid or attempts directory traversal."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(
            f"Invalid path: {reason}",
            path=path,
            reason=reason,
        )


# --- Configuration Errors ---


class ConfigError(LedgerError):
    """Base class for configuration errors."""


class ConfigNotFoundError(ConfigError):
    """Raised when a configuration file cannot be found."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Configuration file not found: {path}", path=path)


class ConfigParseError(ConfigError):
    """Raised when a configuration file cannot be parsed."""


# --- Embedding Errors ---


class EmbeddingError(LedgerError):
    """Base class for embedding/semantic search errors."""


class EmbeddingBackendError(EmbeddingError):
    """Raised when the embedding backend fails."""


class EmbeddingModelNotFoundError(EmbeddingError):
    """Raised when the embedding model cannot be loaded."""


# --- Evaluation Errors ---


class EvalError(LedgerError):
    """Base class for evaluation framework errors."""


class EvalCaseValidationError(EvalError):
    """Raised when an eval case is invalid.

    This replaces the existing EvalCaseValidationError class
    in scripts/ledger with improved context.
    """

    def __init__(
        self,
        message: str,
        *,
        case_index: int | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(
            message,
            case_index=case_index,
            field=field,
        )
        self.case_index = case_index
        self.field = field
