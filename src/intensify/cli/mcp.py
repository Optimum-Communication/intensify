"""``intensify mcp``: run the MCP server over stdio for IDE agents."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from intensify.spec.loader import resolve_path


def mcp(
    spec: Annotated[
        Path,
        typer.Option("--spec", "-s", help="Spec file, or a directory containing intensify.yaml."),
    ] = Path("."),
) -> None:
    """Serve the Intensify graph to AI agents over the Model Context Protocol (stdio)."""
    from intensify.mcp import create_server  # noqa: PLC0415 - keep the MCP SDK off other commands

    create_server(resolve_path(spec).resolve()).run("stdio")
