"""``intensify node``: read and edit individual nodes without hand-editing YAML."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from intensify.cli.validate import render_text
from intensify.graph import Graph
from intensify.spec import LoadedSpec, load_and_validate, yamlio
from intensify.spec.constants import PLACEHOLDER_SUMMARY
from intensify.spec.edit import EditError, add_node, find, patch, save
from intensify.spec.models import Author, LifecycleState, NodeKind

app = typer.Typer(help="Read and edit individual nodes.", no_args_is_help=True)

SpecOption = Annotated[
    Path,
    typer.Option("--spec", "-s", help="Spec file, or a directory containing intensify.yaml."),
]


class Format(StrEnum):
    yaml = "yaml"
    json = "json"


class ListFormat(StrEnum):
    text = "text"
    json = "json"


def load_or_exit(path: Path) -> LoadedSpec:
    loaded = load_and_validate(path)
    if not loaded.ok:
        render_text(loaded)
        raise typer.Exit(2)
    return loaded


def _fail(message: str, code: int = 1) -> None:
    typer.secho(message, fg="red", err=True)
    raise typer.Exit(code)


def _save(loaded: LoadedSpec) -> None:
    try:
        save(loaded)
    except EditError as exc:
        _fail(str(exc))


def _parse_json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail(f"--json is not valid JSON: {exc}", 2)
    if not isinstance(value, dict):
        _fail("--json must be a JSON object", 2)
    result: dict[str, Any] = value
    return result


@app.command("get")
def get(
    node_id: Annotated[str, typer.Argument(help="Node ID.")],
    spec: SpecOption = Path("."),
    output_format: Annotated[Format, typer.Option("--format", "-f")] = Format.yaml,
) -> None:
    """Print one node."""
    loaded = load_or_exit(spec)
    try:
        _, node = find(loaded, node_id)
    except EditError as exc:
        _fail(str(exc))
    if output_format is Format.json:
        typer.echo(json.dumps(yamlio.to_plain(node), indent=2))
    else:
        typer.echo(yamlio.dumps(node), nl=False)


@app.command("list")
def list_nodes(
    query: Annotated[
        list[str] | None, typer.Argument(help="Words to search for in IDs, names and intent.")
    ] = None,
    spec: SpecOption = Path("."),
    kind: Annotated[NodeKind | None, typer.Option(help="Only this kind.")] = None,
    state: Annotated[LifecycleState | None, typer.Option(help="Only this lifecycle state.")] = None,
    tag: Annotated[str | None, typer.Option(help="Only nodes with this tag.")] = None,
    unenriched: Annotated[
        bool, typer.Option("--unenriched", help="Only nodes whose intent is the placeholder.")
    ] = False,
    output_format: Annotated[ListFormat, typer.Option("--format", "-f")] = ListFormat.text,
) -> None:
    """List and search nodes."""
    graph = Graph.from_spec(load_or_exit(spec))
    nodes = graph.search(" ".join(query or []), kind=kind, state=state, tag=tag)
    if unenriched:
        nodes = [n for n in nodes if n.intent.summary == PLACEHOLDER_SUMMARY]
    if output_format is ListFormat.json:
        payload = [
            {
                "id": n.id,
                "kind": n.kind.value,
                "lifecycle_state": n.lifecycle_state.value,
                "summary": n.intent.summary,
            }
            for n in nodes
        ]
        typer.echo(json.dumps(payload, indent=2))
        return
    width = max((len(n.id) for n in nodes), default=0)
    for n in nodes:
        state_text = n.lifecycle_state.value
        typer.echo(f"{n.id:<{width}}  {n.kind.value:<8}  {state_text:<11}  {n.intent.summary}")


@app.command("set")
def set_node(
    node_id: Annotated[str, typer.Argument(help="Node ID.")],
    spec: SpecOption = Path("."),
    summary: Annotated[str | None, typer.Option(help="intent.summary")] = None,
    rationale: Annotated[str | None, typer.Option(help="intent.rationale")] = None,
    author: Annotated[
        Author | None, typer.Option(help="intent.author: `ai` marks a draft for review.")
    ] = None,
    state: Annotated[LifecycleState | None, typer.Option(help="lifecycle_state")] = None,
    name: Annotated[str | None, typer.Option(help="Display name.")] = None,
    owner: Annotated[
        list[str] | None, typer.Option(help="governance.owners (repeatable; replaces the list).")
    ] = None,
    tag: Annotated[
        list[str] | None, typer.Option(help="governance.tags (repeatable; replaces the list).")
    ] = None,
    invariant: Annotated[
        list[str] | None,
        typer.Option(help="Add or replace an invariant: `ID=TEXT` (repeatable)."),
    ] = None,
    json_patch: Annotated[
        str | None,
        typer.Option(
            "--json", help="JSON object deep-merged into the node. `null` removes a field."
        ),
    ] = None,
) -> None:
    """Change fields of a node. Comments and formatting elsewhere are kept."""
    loaded = load_or_exit(spec)
    try:
        _, node = find(loaded, node_id)
    except EditError as exc:
        _fail(str(exc))

    changes: dict[str, Any] = {}
    intent = {
        k: v
        for k, v in {
            "summary": summary,
            "rationale": rationale,
            "author": author.value if author else None,
        }.items()
        if v is not None
    }
    if intent:
        changes["intent"] = intent
    if state is not None:
        changes["lifecycle_state"] = state.value
    if name is not None:
        changes["name"] = name
    governance: dict[str, Any] = {}
    if owner:
        governance["owners"] = owner
    if tag:
        governance["tags"] = tag
    if invariant:
        existing = yamlio.to_plain((node.get("governance") or {}).get("invariants") or [])
        by_id = {inv["id"]: inv for inv in existing}
        for item in invariant:
            inv_id, sep, text = item.partition("=")
            if not sep or not inv_id or not text:
                _fail(f"--invariant must look like ID=TEXT, got {item!r}", 2)
            by_id[inv_id.strip()] = {
                **by_id.get(inv_id.strip(), {}),
                "id": inv_id.strip(),
                "text": text.strip(),
            }
        governance["invariants"] = list(by_id.values())
    if governance:
        changes["governance"] = governance
    changes = _deep_merge(changes, _parse_json(json_patch))
    if not changes:
        _fail("nothing to change: pass at least one option", 2)
    patch(node, changes)
    _save(loaded)
    typer.secho(f"Updated {node_id}", fg="green")


@app.command("add")
def add(
    node_id: Annotated[str, typer.Argument(help="ID of the new node.")],
    kind: Annotated[NodeKind, typer.Option(help="Node kind.")],
    summary: Annotated[str, typer.Option(help="intent.summary")],
    spec: SpecOption = Path("."),
    parent: Annotated[str | None, typer.Option(help="parent_id")] = None,
    rationale: Annotated[str | None, typer.Option(help="intent.rationale")] = None,
    state: Annotated[LifecycleState, typer.Option(help="lifecycle_state")] = LifecycleState.planned,
    json_extra: Annotated[
        str | None, typer.Option("--json", help="JSON object with more fields for the node.")
    ] = None,
) -> None:
    """Add a node, planned by default. It is placed after its parent's subtree."""
    loaded = load_or_exit(spec)
    node: dict[str, Any] = {"id": node_id, "kind": kind.value}
    if parent:
        node["parent_id"] = parent
    node["lifecycle_state"] = state.value
    node["intent"] = {"summary": summary, **({"rationale": rationale} if rationale else {})}
    node = _deep_merge(node, _parse_json(json_extra))
    try:
        add_node(loaded, node)
    except EditError as exc:
        _fail(str(exc))
    _save(loaded)
    typer.secho(f"Added {node_id}", fg="green")


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
