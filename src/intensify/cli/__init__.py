"""Command-line interface. Each command lives in its own module and is registered on ``app``."""

from typing import Annotated

import typer

from intensify import __version__
from intensify.cli.analyze import analyze
from intensify.cli.init import init
from intensify.cli.validate import validate

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


def main() -> None:
    app()
