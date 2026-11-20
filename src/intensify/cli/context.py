"""``intensify context``: a prompt-ready bundle for an agent working on one node."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from intensify.cli.node import load_or_exit
from intensify.context import build, render_markdown, to_dict


class Format(StrEnum):
    markdown = "markdown"
    json = "json"


def context(
    node_id: Annotated[str, typer.Argument(help="Node to build context for.")],
    spec: Annotated[
        Path,
        typer.Option("--spec", "-s", help="Spec file, or a directory containing intensify.yaml."),
    ] = Path("."),
    output_format: Annotated[Format, typer.Option("--format", "-f")] = Format.markdown,
    include_source: Annotated[
        bool, typer.Option("--include-source", help="Include the code of the node and its deps.")
    ] = False,
    budget: Annotated[
        int | None,
        typer.Option(help="Approximate token limit; lower-priority sections are dropped first."),
    ] = None,
) -> None:
    """Print the context an agent needs to implement or change a node."""
    loaded = load_or_exit(spec)
    try:
        bundle = build(loaded, node_id, include_source=include_source)
    except KeyError:
        typer.secho(f"node {node_id!r} does not exist", fg="red", err=True)
        raise typer.Exit(1) from None
    if output_format is Format.json:
        typer.echo(json.dumps(to_dict(bundle), indent=2))
    else:
        typer.echo(render_markdown(bundle, budget=budget), nl=False)
