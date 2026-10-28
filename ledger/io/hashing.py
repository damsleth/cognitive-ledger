"""Shared content and streaming file digests, preserving caller algorithms."""

import hashlib
from pathlib import Path


def digest(content: str | bytes, algorithm: str = "sha256") -> str:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.new(algorithm, data).hexdigest()


def file_digest(path: Path, algorithm: str = "sha256") -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, algorithm).hexdigest()
