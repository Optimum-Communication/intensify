"""Deriving node IDs from file paths and symbol names."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_INVALID = re.compile(r"[^A-Za-z0-9_$-]+")

SOURCE_ROOTS = frozenset({"src", "lib"})
"""Leading directories dropped from path-based IDs (``src/shop/cart.py`` -> ``shop.cart``)."""


def segment(name: str) -> str:
    """Make one ID segment from an arbitrary name (``cart.test`` -> ``cart-test``)."""
    cleaned = _INVALID.sub("-", name).strip("-")
    return cleaned or "_"


def join(*parts: str) -> str:
    return ".".join(segment(p) for part in parts for p in part.split(".") if p)


def path_id(path: str, *, index_names: frozenset[str] = frozenset()) -> str:
    """ID for a module from its path: extension and leading source root dropped.

    Files named in ``index_names`` (``index``, ``__init__``) stand for their directory.
    """
    pure = PurePosixPath(path)
    stem = pure.name.removesuffix(pure.suffix)  # cart.test.ts -> cart.test
    parts = [*pure.parent.parts, stem]
    if len(parts) > 1 and parts[0] in SOURCE_ROOTS:
        parts.pop(0)
    if parts[-1] in index_names and len(parts) > 1:
        parts.pop()
    # Each path part is one segment, so dots inside a file name become dashes.
    return ".".join(segment(p) for p in parts)


class Allocator:
    """Hands out unique node IDs. A taken ID gets a numeric suffix: ``shop.cart-2``."""

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def claim(self, base: str) -> str:
        candidate, n = base, 1
        while candidate in self._taken:
            n += 1
            candidate = f"{base}-{n}"
        self._taken.add(candidate)
        return candidate
