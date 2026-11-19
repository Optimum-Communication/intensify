"""``intensify verify``: check a node's code against its spec, then mark it active."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from intensify.cli.validate import render_text
from intensify.pipeline import SpecInvalidError
from intensify.verify import NodeNotFoundError
from intensify.verify import verify as run_verify


def verify(
    node_id: Annotated[str, typer.Argument(help="Node to verify.")],
    spec: Annotated[
        Path,
        typer.Option("--spec", "-s", help="Spec file, or a directory containing intensify.yaml."),
    ] = Path("."),
    run_tests: Annotated[
        bool, typer.Option("--run-tests/--skip-tests", help="Run config.verify.test_command.")
    ] = True,
    require_tests: Annotated[
        bool,
        typer.Option("--require-tests/--allow-untested", help="Fail if no test verifies the node."),
    ] = True,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Check only; don't update the spec.")
    ] = False,
) -> None:
    """Check that a node's code matches its contract, invariants and tests.

    When every check passes, the analysis of the node's file is written to the spec and the
    node is marked active.
    """
    try:
        result = run_verify(
            spec, node_id, run_tests=run_tests, require_tests=require_tests, dry_run=dry_run
        )
    except SpecInvalidError as exc:
        render_text(exc.loaded)
        raise typer.Exit(2) from None
    except NodeNotFoundError:
        typer.secho(f"node {node_id!r} does not exist", fg="red", err=True)
        raise typer.Exit(2) from None

    for check in result.checks:
        if check.skipped:
            mark = typer.style("-", fg="bright_black")
        elif check.ok:
            mark = typer.style("✓", fg="green")
        else:
            mark = typer.style("✗", fg="red")
        first, *rest = (check.detail or "").splitlines() or [""]
        typer.echo(f"{mark} {check.name}" + (f": {first}" if first else ""))
        for line in rest:
            typer.echo(f"    {line}")

    if not result.ok:
        typer.secho(f"✗ {node_id} is not verified", fg="red", bold=True)
        raise typer.Exit(1)
    if result.promoted:
        state = result.previous_state.value if result.previous_state else "unknown"
        verb = "would be marked" if dry_run else "marked"
        typer.secho(f"✓ {node_id} verified and {verb} active (was {state})", fg="green", bold=True)
    else:
        typer.secho(f"✓ {node_id} verified (already active)", fg="green", bold=True)
