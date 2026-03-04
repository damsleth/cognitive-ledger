"""Tests for ledger.io module."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger.io import (
    FileLock,
    atomic_write,
    safe_write_text,
    safe_read_text,
)
from ledger.io.safe_write import ensure_parent_dir
from ledger.errors import NoteWriteError, NoteReadError


class TestFileLock(unittest.TestCase):
    """Tests for FileLock context manager."""

    def test_creates_lock_file(self):
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            path = Path(f.name)

        try:
            with FileLock(path):
                lock_path = path.with_suffix(".md.lock")
                self.assertTrue(lock_path.exists())
            # Lock file is intentionally kept for debugging (advisory lock handles contention)
            self.assertTrue(lock_path.exists())
        finally:
            path.unlink(missing_ok=True)
            path.with_suffix(".md.lock").unlink(missing_ok=True)

    def test_multiple_sequential_locks(self):
        # Test that locks can be acquired sequentially (not nested)
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            path = Path(f.name)

        try:
            with FileLock(path):
                pass  # First lock
            with FileLock(path):
                pass  # Second lock after release
        finally:
            path.unlink(missing_ok=True)
            path.with_suffix(".md.lock").unlink(missing_ok=True)

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "subdir" / "deep" / "file.md"
            with FileLock(path):
                self.assertTrue(path.parent.exists())


class TestAtomicWrite(unittest.TestCase):
    """Tests for atomic_write context manager."""

    def test_writes_content_atomically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"

            with atomic_write(path) as f:
                f.write("test content")

            self.assertEqual(path.read_text(), "test content")

    def test_no_partial_write_on_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"
            path.write_text("original")

            try:
                with atomic_write(path) as f:
                    f.write("partial")
                    raise ValueError("simulated error")
            except NoteWriteError:
                pass

            # Original content preserved
            self.assertEqual(path.read_text(), "original")

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "new" / "sub" / "dir" / "file.md"

            with atomic_write(path) as f:
                f.write("content")

            self.assertTrue(path.exists())

    def test_binary_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "binary.bin"

            with atomic_write(path, mode="wb") as f:
                f.write(b"\x00\x01\x02")

            self.assertEqual(path.read_bytes(), b"\x00\x01\x02")


class TestSafeWriteText(unittest.TestCase):
    """Tests for safe_write_text function."""

    def test_writes_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"

            safe_write_text(path, "hello world")

            self.assertEqual(path.read_text(), "hello world")

    def test_with_locking(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"

            safe_write_text(path, "locked write", use_lock=True)

            self.assertEqual(path.read_text(), "locked write")

    def test_without_locking(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"

            safe_write_text(path, "unlocked write", use_lock=False)

            self.assertEqual(path.read_text(), "unlocked write")

    def test_unicode_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"

            safe_write_text(path, "Hello 世界 🌍")

            self.assertEqual(path.read_text(), "Hello 世界 🌍")


class TestSafeReadText(unittest.TestCase):
    """Tests for safe_read_text function."""

    def test_reads_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("test content")
            path = Path(f.name)

        try:
            content = safe_read_text(path)
            self.assertEqual(content, "test content")
        finally:
            path.unlink()

    def test_file_not_found_raises(self):
        with self.assertRaises(NoteReadError) as ctx:
            safe_read_text("/nonexistent/path.md")

        self.assertIn("not found", str(ctx.exception))

    def test_with_locking(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("locked read")
            path = Path(f.name)

        try:
            content = safe_read_text(path, use_lock=True)
            self.assertEqual(content, "locked read")
        finally:
            path.unlink()


class TestEnsureParentDir(unittest.TestCase):
    """Tests for ensure_parent_dir function."""

    def test_creates_missing_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a" / "b" / "c" / "file.md"

            result = ensure_parent_dir(path)

            self.assertTrue(result.parent.exists())
            self.assertEqual(result, path)

    def test_existing_dir_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "file.md"

            result = ensure_parent_dir(path)

            self.assertEqual(result, path)


if __name__ == "__main__":
    unittest.main()
