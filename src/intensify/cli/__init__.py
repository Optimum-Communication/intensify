"""Command-line interface. Each command lives in its own module and is registered on ``app``."""

from typing import Annotated

import typer

from intensify import __version__
from intensify.cli.analyze import analyze
from intensify.cli.cloud import login, logout, project_app, sync_app, whoami
from intensify.cli.context import context
from intensify.cli.drift import drift
from intensify.cli.init import init
from intensify.cli.mcp import mcp
from intensify.cli.node import app as node_app
from intensify.cli.skills import app as skills_app
from intensify.cli.validate import validate
from intensify.cli.verify import verify

app = typer.Typer(
    name="intensify",
    help="Define, visualize, and enforce the intent of your codebase.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"intensify {__version__}")
        raise typer.Exit


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Define, visualize, and enforce the intent of your codebase."""


app.command()(init)
app.command()(validate)
app.command()(analyze)
app.command()(drift)
app.command()(context)
app.command()(verify)
app.command()(mcp)
app.add_typer(node_app, name="node")
app.add_typer(skills_app, name="skills")
app.command()(login)
app.command()(logout)
app.command()(whoami)
app.add_typer(project_app, name="project")
app.add_typer(sync_app, name="sync")


def main() -> None:
    app()
