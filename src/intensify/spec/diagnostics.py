"""Diagnostics reported by loading and validating a spec."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Severity(StrEnum):
    error = "error"
    warning = "warning"


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: str
    """Stable kebab-case identifier, e.g. ``unknown-parent``. Safe to match on in scripts."""
    message: str
    file: Path
    path: tuple[str | int, ...] = ()
    """Location inside the file, e.g. ``("nodes", 2, "parent_id")``."""
    line: int | None = None

    @property
    def pointer(self) -> str:
        """The path as ``nodes[2].parent_id``."""
        return format_path(self.path)


def format_path(path: Sequence[str | int]) -> str:
    out = ""
    for step in path:
        if isinstance(step, int):
            out += f"[{step}]"
        else:
            out += f".{step}" if out else step
    return out
