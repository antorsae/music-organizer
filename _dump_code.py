#!/usr/bin/env python3
"""
Create a single-file dump of the project's relevant code and config.

Outputs to stdout by default, or to a file via --output.
Skips common boilerplate and build artifacts (e.g., .git, __pycache__, .venv).
Includes typical source and config files (e.g., .py, .md, .yaml, .json, .toml, .txt).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, List, Set


# Directories that are almost always boilerplate/unneeded for code review
IGNORE_DIRS: Set[str] = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "out",
    "coverage",
    ".idea",
    ".vscode",
    ".DS_Store",
    "_music_claude_output",
    ".claude",
}

# Individual files to ignore
IGNORE_FILES: Set[str] = {
    ".run.pid",
}

# File extensions that are useful for LLM code analysis
INCLUDE_EXTS: Set[str] = {
    ".py",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".ini",
    ".cfg",
    ".conf",
    ".sh",
    ".bash",
    ".bat",
    ".ps1",
}

# Specific filenames to include even without an extension
INCLUDE_NAMES: Set[str] = {
    "Makefile",
    "Dockerfile",
    "Procfile",
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "Pipfile.lock",
}


def is_included_file(path: Path) -> bool:
    """Return True if 'path' should be included in the dump."""
    name = path.name
    if name in IGNORE_FILES:
        return False
    if name in INCLUDE_NAMES:
        return True
    ext = path.suffix.lower()
    if ext in INCLUDE_EXTS:
        return True
    return False


def iter_relevant_files(root: Path) -> Iterable[Path]:
    """Yield relevant files under root, skipping known boilerplate dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Normalize and filter directories in-place for os.walk to skip them
        dirnames[:] = [
            d
            for d in dirnames
            if d not in IGNORE_DIRS and not d.startswith(".") or d in {".github"}
        ]

        for fname in filenames:
            fpath = Path(dirpath) / fname
            # Skip hidden or ignored files
            if fname in IGNORE_FILES:
                continue
            if fname.startswith(".") and fname not in {".env", ".env.example"}:
                continue
            if is_included_file(fpath):
                yield fpath


def read_text_safely(path: Path) -> str:
    """Read file content as text, replacing undecodable bytes."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:  # As a fallback, try binary then decode
        try:
            data = path.read_bytes()
            return data.decode("utf-8", errors="replace")
        except Exception:
            return f"<ERROR READING FILE: {e}>"


def build_dump(root: Path) -> str:
    files: List[Path] = sorted(
        (p for p in iter_relevant_files(root)), key=lambda p: str(p.relative_to(root))
    )

    lines: List[str] = []
    # Required prefix for the LLM
    lines.append(
        "The following contains a dump of most/all relevant code for the project:"
    )
    lines.append("")

    for path in files:
        rel = path.relative_to(root)
        lines.append("=" * 80)
        lines.append(f"FILE: {rel}")
        lines.append("=" * 80)
        lines.append(read_text_safely(path))
        # Ensure a trailing newline between files
        if not lines[-1].endswith("\n"):
            lines.append("")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a single-file dump of source and config files for LLM analysis."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write dump to this file instead of stdout",
    )

    args = parser.parse_args(argv)
    root = args.root.resolve()

    dump = build_dump(root)

    if args.output:
        args.output.write_text(dump, encoding="utf-8")
    else:
        sys.stdout.write(dump)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

