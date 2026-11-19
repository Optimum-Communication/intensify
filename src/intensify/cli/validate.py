"""``intensify validate``: check a spec against the schema and semantic rules."""

from __future__ import annotations

import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from intensify.spec import Diagnostic, LoadedSpec, Severity, load_and_validate


class OutputFormat(StrEnum):
    text = "text"
    json = "json"


def _display_path(path: Path) -> str:
    """Relative to the working directory when inside it, otherwise absolute."""
    try:
        rel = os.path.relpath(path)
    except ValueError:  # different drive on Windows
        return str(path.absolute())
    return str(path.absolute()) if rel.startswith("..") else rel


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def render_text(loaded: LoadedSpec) -> None:
    for d in loaded.diagnostics:
        where = _display_path(d.file) + (f":{d.line}" if d.line else "")
        color = "red" if d.severity is Severity.error else "yellow"
        typer.echo(
            f"{where}: "
            + typer.style(d.severity.value, fg=color, bold=True)
            + f" [{d.code}] "
            + (f"{d.pointer}: " if d.path else "")
            + d.message
        )

    errors, warnings = len(loaded.errors), len(loaded.warnings)
    if errors:
        summary = _plural(errors, "error") + (
            f", {_plural(warnings, 'warning')}" if warnings else ""
        )
        typer.secho(f"✗ {summary}", fg="red", bold=True)
        return
    stats = f"{_plural(len(loaded.nodes), 'node')}, {_plural(len(loaded.edges), 'edge')}"
    if len(loaded.files) > 1:
        stats += f" across {_plural(len(loaded.files), 'file')}"
    suffix = f" ({_plural(warnings, 'warning')})" if warnings else ""
    typer.secho(f"✓ {_display_path(loaded.path)} is valid: {stats}{suffix}", fg="green")


def _diagnostic_json(d: Diagnostic) -> dict[str, Any]:
    return {
        "severity": d.severity.value,
        "code": d.code,
        "message": d.message,
        "file": _display_path(d.file),
        "line": d.line,
        "path": d.pointer or None,
    }


def render_json(loaded: LoadedSpec) -> None:
    payload = {
        "valid": loaded.ok,
        "files": [_display_path(f.path) for f in loaded.files],
        "nodes": len(loaded.nodes),
        "edges": len(loaded.edges),
        "diagnostics": [_diagnostic_json(d) for d in loaded.diagnostics],
    }
    typer.echo(json.dumps(payload, indent=2))


def validate(
    path: Annotated[
        Path,
        typer.Argument(help="Spec file, or a directory containing intensify.yaml."),
    ] = Path("."),
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Output format.")
    ] = OutputFormat.text,
) -> None:
    """Validate intensify.yaml (and included files) against the spec."""
    loaded = load_and_validate(path)
    if output_format is OutputFormat.json:
        render_json(loaded)
    else:
        render_text(loaded)
    if not loaded.ok:
        raise typer.Exit(1)
