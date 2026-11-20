"""Command-line interface. Each command lives in its own module and is registered on ``app``."""

from typing import Annotated

import typer

from intensify import __version__

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


def main() -> None:
    app()
