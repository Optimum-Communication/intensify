"""Finding the source files to analyze."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pathspec

MAX_FILE_BYTES = 1_000_000
"""Larger files are almost always generated or minified, and are skipped."""

_ALWAYS_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox", ".mypy_cache"}
)


def discover(
    root: Path,
    *,
    extensions: Iterable[str],
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[str]:
    """POSIX paths (relative to ``root``) of analyzable files, sorted.

    Respects ``.gitignore`` (through git when available), then ``include`` and ``exclude`` globs
    in gitignore syntax.
    """
    exts = tuple(extensions)
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk_files(root)

    include_spec = pathspec.GitIgnoreSpec.from_lines(include) if include else None
    exclude_spec = pathspec.GitIgnoreSpec.from_lines(exclude or [])
    out = []
    for rel in candidates:
        if not rel.endswith(exts) or rel.endswith(".d.ts") or ".min." in rel:
            continue
        if any(part in _ALWAYS_SKIP_DIRS for part in rel.split("/")[:-1]):
            continue
        if include_spec is not None and not include_spec.match_file(rel):
            continue
        if exclude_spec.match_file(rel):
            continue
        path = root / rel
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        out.append(rel)
    return sorted(out)


def _git_files(root: Path) -> list[str] | None:
    """Tracked and untracked-but-not-ignored files, or None outside a git work tree."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "."],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in proc.stdout.decode("utf-8", errors="replace").split("\0") if p]


def _walk_files(root: Path) -> list[str]:
    gitignore = root / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
    ignored = pathspec.GitIgnoreSpec.from_lines(lines)
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else f"{rel_dir}/"
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _ALWAYS_SKIP_DIRS
            and not d.startswith(".")
            and not ignored.match_file(f"{prefix}{d}/")
        ]
        out.extend(f"{prefix}{f}" for f in filenames if not ignored.match_file(f"{prefix}{f}"))
    return out
