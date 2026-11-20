"""Helpers over tree-sitter syntax trees shared by the language analyzers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator

from tree_sitter import Node

_WS = re.compile(r"\s+")
_TRAILER = re.compile(r"\s*(=>|\{|:)$")
_COMMENTS = frozenset({"comment", "line_comment", "block_comment"})


def text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def squash(source: str) -> str:
    """Collapse a multi-line declaration into one tidy line."""
    out = _WS.sub(" ", source).strip()
    out = re.sub(r"([(\[<]) ", r"\1", out)
    out = re.sub(r",? ([)\]>])", r"\1", out)
    return out


def header(node: Node, body: Node | None) -> str:
    """Source text of ``node`` up to (not including) its body, squashed to one line."""
    raw = node.text or b""
    if body is not None:
        raw = raw[: body.start_byte - node.start_byte]
    return _TRAILER.sub("", squash(raw.decode("utf-8", errors="replace")))


def walk(node: Node, *, prune: Callable[[Node], bool] | None = None) -> Iterator[Node]:
    """Depth-first descendants of ``node`` (excluding itself). ``prune`` stops descent."""
    stack = list(reversed(node.children))
    while stack:
        current = stack.pop()
        yield current
        if prune is None or not prune(current):
            stack.extend(reversed(current.children))


def fingerprint(node: Node, identifier_types: frozenset[str]) -> str:
    """Hash of the node's structure with identifier names and comments removed.

    Survives renames and moves to another file, but changes when the logic changes.
    """
    digest = hashlib.sha256()
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in _COMMENTS:
            continue
        digest.update(current.type.encode())
        if current.child_count == 0:
            token = b"_" if current.type in identifier_types else (current.text or b"")
            digest.update(b"\x00" + token)
        digest.update(b"\x01")
        stack.extend(reversed(current.children))
    return f"sha256:{digest.hexdigest()}"


def line_count(node: Node) -> int:
    return node.end_point.row - node.start_point.row + 1
