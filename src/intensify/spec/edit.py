"""Safe, comment-preserving edits to a spec: the write path for the CLI, skills and MCP server.

Edits go to the file each node lives in. ``save`` validates the result and restores the original
files if the edit would leave the spec invalid, so callers never leave a broken spec behind.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import ScalarString

from intensify.spec import yamlio
from intensify.spec.diagnostics import Diagnostic
from intensify.spec.loader import LoadedSpec, SpecFile
from intensify.spec.validate import load_and_validate

NODE_KEY_ORDER = (
    "id",
    "kind",
    "name",
    "parent_id",
    "lifecycle_state",
    "provenance",
    "intent",
    "contract",
    "technical",
    "governance",
)
"""Canonical node key order. Keys added to an existing node are placed accordingly."""

_SUB_ORDERS: dict[str, tuple[str, ...]] = {
    "intent": ("summary", "rationale", "author"),
    "contract": ("inputs", "outputs", "errors"),
    "technical": ("language", "location", "signature", "complexity", "fingerprint"),
    "governance": ("owners", "tags", "invariants"),
}


class EditError(Exception):
    """The edit was rejected; nothing was written."""

    def __init__(self, message: str, diagnostics: list[Diagnostic] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or []


def put(
    mapping: CommentedMap, key: str, value: Any, order: tuple[str, ...] = NODE_KEY_ORDER
) -> None:
    """Set ``mapping[key]``; a new key goes in its canonical position rather than at the end."""
    if key in mapping:
        if isinstance(mapping[key], ScalarString) and not isinstance(value, ScalarString):
            # ruamel keeps the old value's quoting on assignment; re-insert to write it plain.
            position = list(mapping).index(key)
            comment = mapping.ca.items.pop(key, None)
            del mapping[key]
            mapping.insert(position, key, value)
            if comment is not None:
                mapping.ca.items[key] = comment
        else:
            mapping[key] = value
        return
    if key not in order:
        mapping[key] = value
        return
    rank = order.index(key)
    position = sum(1 for k in mapping if k in order and order.index(k) < rank)
    mapping.insert(position, key, value)


def to_yaml(value: Any) -> Any:
    """Plain data -> round-trip data, using flow style for small flat mappings."""
    if isinstance(value, dict):
        out = CommentedMap()
        for k, v in value.items():
            out[k] = to_yaml(v)
        if value and all(not isinstance(v, dict | list) for v in value.values()):
            out.fa.set_flow_style()
        return out
    if isinstance(value, list):
        seq = CommentedSeq(to_yaml(v) for v in value)
        if value and all(not isinstance(v, dict | list) for v in value):
            seq.fa.set_flow_style()  # tags: [a, b]
        return seq
    return value


def find(loaded: LoadedSpec, node_id: str) -> tuple[SpecFile, CommentedMap]:
    for entry in loaded.nodes:
        if entry.node.id == node_id:
            return entry.file, entry.file.data["nodes"][entry.index]
    raise EditError(f"node {node_id!r} does not exist")


def patch(
    mapping: CommentedMap, changes: dict[str, Any], order: tuple[str, ...] = NODE_KEY_ORDER
) -> None:
    """Deep-merge ``changes`` into a node (or part of one): mappings merge, anything else replaces.

    A ``None`` value removes the key.
    """
    for key, value in changes.items():
        if value is None:
            mapping.pop(key, None)
        elif isinstance(value, dict) and isinstance(mapping.get(key), dict):
            patch(mapping[key], value, _SUB_ORDERS.get(key, ()))
        else:
            put(mapping, key, to_yaml(value), order)


def add_node(loaded: LoadedSpec, node: dict[str, Any]) -> None:
    """Insert a node after its parent's subtree, in the parent's file (the root file otherwise)."""
    node_id = node["id"]
    if any(e.node.id == node_id for e in loaded.nodes):
        raise EditError(f"node {node_id!r} already exists")
    parent_id = node.get("parent_id")
    file = next(f for f in loaded.files if f.is_root)
    if parent_id is not None:
        parent = next((e for e in loaded.nodes if e.node.id == parent_id), None)
        if parent is None:
            raise EditError(f"parent {parent_id!r} does not exist")
        file = parent.file
    if file.data.get("nodes") is None:
        file.data["nodes"] = CommentedSeq()
    seq: CommentedSeq = file.data["nodes"]

    parent_of = {e.node.id: e.node.parent_id for e in loaded.nodes}
    position = len(seq)
    if parent_id is not None:
        for index, item in enumerate(seq):
            current: str | None = str(item.get("id"))
            seen: set[str] = set()
            while current is not None and current not in seen:
                if current == parent_id:
                    position = index + 1
                    break
                seen.add(current)
                current = parent_of.get(current)
    ordered = {k: node[k] for k in NODE_KEY_ORDER if k in node}
    ordered.update({k: v for k, v in node.items() if k not in ordered})
    item = to_yaml(ordered)
    item.fa.set_block_style()
    for key in ("intent", "governance"):
        if isinstance(item.get(key), CommentedMap):
            item[key].fa.set_block_style()  # match what `analyze` writes
    seq.insert(position, item)


def save(loaded: LoadedSpec) -> LoadedSpec:
    """Write every changed file, validate, and roll back on errors. Returns the reloaded spec."""
    originals: dict[Path, str] = {}
    for file in loaded.files:
        before = file.path.read_text(encoding="utf-8")
        after = yamlio.dumps(file.data)
        if before != after:
            originals[file.path] = before
            file.path.write_text(after, encoding="utf-8")
    reloaded = load_and_validate(loaded.path)
    if not reloaded.ok:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")
        details = "; ".join(f"{d.pointer or d.file.name}: {d.message}" for d in reloaded.errors)
        raise EditError(f"the edit would make the spec invalid: {details}", reloaded.errors)
    return reloaded
