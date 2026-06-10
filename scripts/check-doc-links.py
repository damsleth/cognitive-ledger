#!/usr/bin/env python3
"""Check that all Markdown links in docs/ and README.md resolve to real targets.

Usage::

    python scripts/check-doc-links.py               # check all docs
    python scripts/check-doc-links.py README.md      # check one file
    python scripts/check-doc-links.py --strict        # exit 1 on any broken link

Exits:
    0  All links resolve.
    1  One or more links are broken (only in --strict mode).
    0  Broken links are printed as warnings otherwise (default: informational).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]]*)\]\(([^)]+)\)"
)


def _check_file(path: Path, root: Path) -> list[tuple[Path, str, str]]:
    """Return list of (file, link_text, link_target) for broken links in *path*."""
    broken: list[tuple[Path, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return broken

    for match in MARKDOWN_LINK_RE.finditer(text):
        link_text = match.group(1)
        target = match.group(2).strip()

        # Skip external URLs.
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue

        # Strip anchor fragment.
        target_path = target.split("#")[0]
        if not target_path:
            continue

        # Resolve relative to the file's directory.
        resolved = (path.parent / target_path).resolve()
        if not resolved.exists():
            broken.append((path, link_text, target))

    return broken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Markdown doc links")
    parser.add_argument(
        "files",
        nargs="*",
        help="Markdown files to check (default: docs/**/*.md and README.md)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any broken links are found",
    )
    args = parser.parse_args(argv)

    if args.files:
        paths = [Path(f).resolve() for f in args.files]
    else:
        paths = list(REPO_ROOT.glob("docs/**/*.md")) + [REPO_ROOT / "README.md"]
        paths = [p for p in paths if p.is_file()]

    all_broken: list[tuple[Path, str, str]] = []
    for path in sorted(paths):
        broken = _check_file(path, REPO_ROOT)
        all_broken.extend(broken)

    if not all_broken:
        print(f"check-doc-links: all links ok ({len(paths)} file(s) checked)")
        return 0

    print(f"check-doc-links: {len(all_broken)} broken link(s) found:")
    for file_path, text, target in all_broken:
        rel = file_path.relative_to(REPO_ROOT)
        print(f"  {rel}: [{text}]({target})")

    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
