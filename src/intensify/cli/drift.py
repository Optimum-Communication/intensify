"""``intensify drift``: check that the code still matches intensify.yaml."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from intensify.cli.validate import render_text as render_diagnostics
from intensify.differ import DriftReport, Level, detect
from intensify.differ.base import BaseRefError
from intensify.differ.report import to_codequality, to_json, to_markdown, to_sarif
from intensify.pipeline import SpecInvalidError
from intensify.spec.models import DriftCategory

EXIT_DRIFT = 1
EXIT_UNUSABLE = 2
"""The spec is invalid or the base revision can't be read: the check couldn't run."""


class OutputFormat(StrEnum):
    text = "text"
    json = "json"
    markdown = "markdown"
    sarif = "sarif"
    codequality = "codequality"


_COLORS = {Level.error: "red", Level.warning: "yellow", Level.note: "blue"}


def render_text(report: DriftReport, *, verbose: bool) -> None:
    where = os.path.relpath(report.spec_path)
    context = f"Checked {report.files_analyzed} files against {where}"
    if report.base:
        context += f" (compared with {report.base}"
        context += f"; {report.suppressed} existing findings hidden)" if report.suppressed else ")"
    typer.echo(context)

    hidden = 0
    for f in report.findings:
        if f.level is Level.note and not verbose:
            hidden += 1
            continue
        if f.code is not None:
            location = f"{f.code[0]}:{f.code[1]}"
        elif f.spec is not None:
            location = os.path.relpath(f.spec[0]) + (f":{f.spec[1]}" if f.spec[1] else "")
        else:
            location = where
        typer.echo(
            typer.style(f"{f.level.value:<7}", fg=_COLORS[f.level], bold=True)
            + f" {f.category.value:<19} {location}  {f.message}"
        )

    counts = [(level, report.count(level)) for level in Level]
    summary = ", ".join(f"{n} {lvl.value}{'' if n == 1 else 's'}" for lvl, n in counts if n)
    if hidden:
        summary += " (notes hidden; use --verbose)"
    if not report.findings:
        typer.secho("✓ No drift", fg="green")
    elif report.ok:
        typer.secho(f"✓ No blocking drift: {summary}", fg="green")
    else:
        typer.secho(f"✗ {summary}", fg="red", bold=True)


def drift(
    path: Annotated[
        Path,
        typer.Argument(help="Spec file, or a directory containing intensify.yaml."),
    ] = Path("."),
    base: Annotated[
        str | None,
        typer.Option(
            help="Git revision to compare with. Only drift introduced since then is reported."
        ),
    ] = None,
    fail_on: Annotated[
        list[DriftCategory] | None,
        typer.Option(
            "--fail-on",
            help="Category that fails the check (repeatable). Overrides config.drift.fail_on.",
        ),
    ] = None,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Format printed to stdout.")
    ] = OutputFormat.text,
    sarif: Annotated[
        Path | None, typer.Option(help="Also write a SARIF 2.1.0 report to this file.")
    ] = None,
    markdown: Annotated[
        Path | None, typer.Option(help="Also write a Markdown summary to this file.")
    ] = None,
    codequality: Annotated[
        Path | None,
        typer.Option(help="Also write a GitLab Code Quality report to this file."),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show notes too.")] = False,
) -> None:
    """Compare the code with intensify.yaml and report drift.

    Exits 1 when a category in fail_on is found, and 2 when the check can't run.
    """
    try:
        report = detect(path, fail_on=frozenset(fail_on) if fail_on else None, base=base)
    except SpecInvalidError as exc:
        render_diagnostics(exc.loaded)
        typer.secho("The spec is invalid, so drift can't be checked.", fg="red", err=True)
        raise typer.Exit(EXIT_UNUSABLE) from None
    except BaseRefError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(EXIT_UNUSABLE) from None

    for target, render in (
        (sarif, to_sarif),
        (markdown, to_markdown),
        (codequality, to_codequality),
    ):
        if target is not None:
            target.write_text(render(report), encoding="utf-8")

    match output_format:
        case OutputFormat.text:
            render_text(report, verbose=verbose)
        case OutputFormat.json:
            typer.echo(to_json(report), nl=False)
        case OutputFormat.markdown:
            typer.echo(to_markdown(report), nl=False)
        case OutputFormat.sarif:
            typer.echo(to_sarif(report), nl=False)
        case OutputFormat.codequality:
            typer.echo(to_codequality(report), nl=False)

    if not report.ok:
        raise typer.Exit(EXIT_DRIFT)
