"""``intensify init``: create a starter ``intensify.yaml``."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

import typer

from intensify.spec import load_and_validate
from intensify.spec.constants import (
    DEFAULT_FILENAME,
    PLACEHOLDER_SUMMARY,
    SCHEMA_URL,
    SPEC_DOC_URL,
    SPEC_VERSION,
)

PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

TEMPLATE = """\
# yaml-language-server: $schema={schema_url}
#
# Intensify spec: the intent graph of this repository.
# Reference: {doc_url}
intensify: "{version}"

project:
  id: {project_id}
  name: {name}

config:
  analyze:
    depth: function  # finest node kind `intensify analyze` creates: module | class | function
    public_only: true  # skip private symbols
  drift:
    fail_on: [undocumented_edge, contract_mismatch, invariant_violation, missing_node]

nodes:
  - id: {project_id}
    kind: repo
    lifecycle_state: active
    intent:
      summary: {summary}
"""


def slugify(name: str) -> str:
    """Turn a directory name into a valid project ID."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")
    return slug or "project"


def render(project_id: str, name: str, summary: str) -> str:
    # json.dumps yields a double-quoted string, which is also a valid YAML scalar.
    return TEMPLATE.format(
        schema_url=SCHEMA_URL,
        doc_url=SPEC_DOC_URL,
        version=SPEC_VERSION,
        project_id=project_id,
        name=json.dumps(name, ensure_ascii=False),
        summary=json.dumps(summary, ensure_ascii=False),
    )


def init(
    directory: Annotated[
        Path,
        typer.Argument(file_okay=False, help="Repository root to initialize."),
    ] = Path("."),
    project_id: Annotated[
        str | None,
        typer.Option("--id", help="Project ID. Defaults to the directory name, slugified."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(help="Human-readable project name. Defaults to the directory name."),
    ] = None,
    summary: Annotated[
        str | None,
        typer.Option(help="One-line intent of the repository."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Create a starter intensify.yaml in the repository root."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / DEFAULT_FILENAME
    if target.exists() and not force:
        typer.secho(f"{target} already exists (use --force to overwrite)", fg="red", err=True)
        raise typer.Exit(1)

    dir_name = directory.resolve().name
    project_id = project_id or slugify(dir_name)
    if not PROJECT_ID_RE.match(project_id):
        typer.secho(
            f"invalid project ID {project_id!r}: use lowercase letters, digits, '_' or '-', "
            "starting with a letter or digit",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)

    target.write_text(
        render(project_id, name or dir_name, summary or PLACEHOLDER_SUMMARY), encoding="utf-8"
    )

    # The template is fixed, so failing here means a bug in the CLI, not in the user's input.
    loaded = load_and_validate(target)
    if not loaded.ok:
        details = "; ".join(d.message for d in loaded.errors)
        raise RuntimeError(f"generated spec is invalid: {details}")

    typer.secho(f"Created {target}", fg="green")
    if summary is None:
        typer.echo("Next: describe the repository's intent in nodes[0].intent.summary.")
