"""``intensify analyze``: populate and refresh intensify.yaml from source code."""

from __future__ import annotations

import difflib
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer

from intensify.cli.validate import render_text
from intensify.merge import MergeReport
from intensify.parser import AnalysisResult
from intensify.pipeline import SpecInvalidError, analyze_and_merge
from intensify.spec import load_and_validate, yamlio
from intensify.spec.models import NodeKind


@dataclass
class AnalyzeOutcome:
    report: MergeReport
    analysis: AnalysisResult
    diffs: dict[Path, str] = field(default_factory=dict)
    """Unified diff per changed spec file."""
    seconds: float = 0.0


_MAX_LISTED = 10
"""Without --verbose, longer lists are abbreviated."""


def run(
    spec_path: Path,
    *,
    depth: NodeKind | None = None,
    prune: bool = False,
    paths: tuple[str, ...] = (),
    dry_run: bool = False,
) -> AnalyzeOutcome:
    """Analyze and merge; write changed spec files unless ``dry_run``.

    Raises ``SpecInvalidError`` if the spec has errors.
    """
    started = time.perf_counter()
    merged = analyze_and_merge(spec_path, depth=depth, prune=prune, paths=paths)
    outcome = AnalyzeOutcome(merged.report, merged.analysis)
    for file in merged.loaded.files:
        before = file.path.read_text(encoding="utf-8")
        after = yamlio.dumps(file.data)
        if before == after:
            continue
        name = os.path.relpath(file.path)
        outcome.diffs[file.path] = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
            )
        )
        if not dry_run:
            file.path.write_text(after, encoding="utf-8")
    outcome.seconds = time.perf_counter() - started
    return outcome


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def print_report(outcome: AnalyzeOutcome, *, dry_run: bool, verbose: bool) -> None:
    analysis, report = outcome.analysis, outcome.report
    languages = Counter(analysis.files.values())
    by_language = ", ".join(f"{lang}: {n}" for lang, n in sorted(languages.items()))
    typer.echo(
        f"Analyzed {_plural(len(analysis.files), 'file')}"
        + (f" ({by_language})" if by_language else "")
        + f" in {outcome.seconds:.1f}s"
    )
    for warning in analysis.warnings:
        typer.secho(f"  warning: {warning}", fg="yellow")

    def section(symbol: str, color: str, label: str, items: list[str]) -> None:
        if not items:
            return
        typer.secho(f"  {symbol} {_plural(len(items), 'node')} {label}", fg=color)
        if verbose or len(items) <= _MAX_LISTED:
            for item in items:
                typer.echo(f"      {item}")
        else:
            typer.echo(f"      {items[0]}, {items[1]}, ... (use --verbose for all)")

    section("+", "green", "added", report.added)
    section("~", "cyan", "updated", report.updated)
    section("→", "cyan", "moved", [f"{m.id}: {m.old} → {m.new}" for m in report.moved])
    section("↑", "green", "promoted to in_progress", report.promoted)
    section("✗", "yellow", "deprecated", report.deprecated)
    missing = [f"{node_id} ({where})" for node_id, where in report.missing]
    if not report.deprecated:
        section("?", "yellow", "missing from code (use --prune to deprecate)", missing)
    for message in report.ambiguous:
        typer.secho(f"  ! ambiguous: {message}", fg="yellow")
    if report.edges_added or report.edges_removed:
        typer.echo(f"  edges: +{report.edges_added} -{report.edges_removed}")

    if not outcome.diffs:
        typer.secho("intensify.yaml is up to date.", fg="green")
    elif dry_run:
        for diff in outcome.diffs.values():
            typer.echo(diff, nl=False)
        typer.secho("Dry run: no files were written.", fg="yellow")
    else:
        for path in outcome.diffs:
            typer.secho(f"Wrote {os.path.relpath(path)}", fg="green")


def analyze(
    path: Annotated[
        Path,
        typer.Argument(help="Spec file, or a directory containing intensify.yaml."),
    ] = Path("."),
    only: Annotated[
        list[str] | None,
        typer.Option(
            "--paths",
            "-p",
            help="Only update nodes for these files or directories (repeatable).",
        ),
    ] = None,
    depth: Annotated[
        NodeKind | None,
        typer.Option(help="Finest node kind to create. Overrides config.analyze.depth."),
    ] = None,
    prune: Annotated[
        bool, typer.Option("--prune", help="Mark nodes whose code is gone as deprecated.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Show the changes without writing them.")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="List every node.")] = False,
) -> None:
    """Add and refresh nodes and edges in intensify.yaml from source code.

    Never changes intent, governance, or hand-written edges, and never deletes nodes.
    """
    if depth is not None and depth not in {NodeKind.module, NodeKind.class_, NodeKind.function}:
        typer.secho("--depth must be module, class or function", fg="red", err=True)
        raise typer.Exit(2)
    spec_root = path if path.is_dir() else path.parent
    scope = tuple(_relative(p, spec_root) for p in only or [])
    try:
        outcome = run(path, depth=depth, prune=prune, paths=scope, dry_run=dry_run)
    except SpecInvalidError as exc:
        render_text(exc.loaded)
        typer.secho("Fix the errors above before running analyze.", fg="red", err=True)
        raise typer.Exit(1) from None
    print_report(outcome, dry_run=dry_run, verbose=verbose)

    if not dry_run and outcome.diffs:
        after = load_and_validate(path)
        if not after.ok:  # would be a bug in merge: report it rather than hide it
            render_text(after)
            raise typer.Exit(1)


def _relative(path: str, root: Path) -> str:
    """Accept paths relative to the working directory and make them relative to the spec root."""
    absolute = Path(path).resolve()
    try:
        return absolute.relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix().removeprefix("./")
