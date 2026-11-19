"""Round-trip YAML reading and writing.

Machine edits to ``intensify.yaml`` must keep comments, key order and formatting intact, so all
reads and writes go through ruamel.yaml in round-trip mode. Files written in the canonical style
round-trip byte-for-byte: 2-space mapping indentation, list items indented 2 under their key, and
flow collections without inner padding (``{from: a, to: b}``, not ``{ from: a, to: b }``).
Other styles are preserved semantically but may be normalized on write.
"""

from __future__ import annotations

from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096  # never re-wrap long strings
    return yaml


def loads(text: str) -> Any:
    """Parse YAML text into round-trip data (CommentedMap / CommentedSeq / scalars)."""
    return _yaml().load(text)


def read(path: Path) -> Any:
    return loads(path.read_text(encoding="utf-8"))


def dumps(data: Any) -> str:
    buf = StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def write(data: Any, path: Path) -> None:
    path.write_text(dumps(data), encoding="utf-8")


def to_plain(data: Any) -> Any:
    """Convert round-trip data to plain dicts, lists and builtin scalars for validation."""
    if isinstance(data, dict):
        return {str(k): to_plain(v) for k, v in data.items()}
    if isinstance(data, list):
        return [to_plain(v) for v in data]
    # bool before int: bool is an int subclass.
    for scalar in (bool, int, float, str):
        if isinstance(data, scalar):
            return scalar(data)
    return data


def line_of(data: Any, path: Sequence[str | int]) -> int | None:
    """Best-effort 1-based line number of the value at ``path`` within round-trip data.

    Walks as deep as the path exists and returns the line of the deepest key or item found,
    so an error about a missing field points at the object that should contain it.
    """
    line: int | None = None
    current = data
    for step in path:
        if isinstance(current, CommentedMap) and step in current:
            line = current.lc.key(step)[0] + 1
        elif (
            isinstance(current, CommentedSeq) and isinstance(step, int) and 0 <= step < len(current)
        ):
            line = current.lc.item(step)[0] + 1
        else:
            break
        current = current[step]
    return line
