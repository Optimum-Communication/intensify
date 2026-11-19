"""``intensify skills``: install the bundled AI skills for an agent."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from intensify.skills import DEFAULT_DIRS, Target, bundled, install

app = typer.Typer(help="Install the bundled AI skills for coding agents.", no_args_is_help=True)


@app.command("list")
def list_skills() -> None:
    """List the bundled skills."""
    for skill in bundled():
        typer.echo(f"{skill.name}: {skill.description}")


@app.command("install")
def install_skills(
    target: Annotated[Target, typer.Option(help="Agent to install for.")] = Target.claude,
    directory: Annotated[
        Path | None,
        typer.Option("--dir", help="Where to install. Defaults to the agent's usual location."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite changed files.")] = False,
) -> None:
    """Copy the skills to where the agent looks for them (run from the repository root)."""
    results = install(target, directory or DEFAULT_DIRS[target], force=force)
    colors = {
        "created": "green",
        "updated": "green",
        "unchanged": "bright_black",
        "skipped": "yellow",
    }
    for path, status in results:
        typer.echo(typer.style(f"{status:<9}", fg=colors[status]) + f" {path}")
    if any(status == "skipped" for _, status in results):
        typer.secho("Some files were changed locally; use --force to overwrite them.", fg="yellow")
