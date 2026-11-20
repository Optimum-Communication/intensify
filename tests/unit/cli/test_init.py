from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from intensify.cli import app
from intensify.cli.init import slugify
from intensify.spec import PLACEHOLDER_SUMMARY, SCHEMA_URL, load_and_validate

runner = CliRunner()


def test_init_creates_valid_spec(tmp_path: Path) -> None:
    repo = tmp_path / "My Cool_Repo"
    result = runner.invoke(app, ["init", str(repo)])
    assert result.exit_code == 0, result.output

    text = (repo / "intensify.yaml").read_text()
    assert text.startswith(f"# yaml-language-server: $schema={SCHEMA_URL}\n")

    loaded = load_and_validate(repo)
    assert loaded.diagnostics == []
    assert loaded.document is not None
    assert loaded.document.project.id == "my-cool_repo"
    assert loaded.document.project.name == "My Cool_Repo"
    [root] = loaded.nodes
    assert (root.node.id, root.node.kind, root.node.lifecycle_state) == (
        "my-cool_repo",
        "repo",
        "active",
    )
    assert root.node.intent.summary == PLACEHOLDER_SUMMARY


def test_init_options_are_quoted_safely(tmp_path: Path) -> None:
    summary = 'Handles "refunds": partial & full\nacross # regions'
    result = runner.invoke(
        app, ["init", str(tmp_path), "--id", "pay", "--name", "Pay: v2", "--summary", summary]
    )
    assert result.exit_code == 0, result.output
    loaded = load_and_validate(tmp_path)
    assert loaded.ok
    assert loaded.document is not None
    assert loaded.document.project.name == "Pay: v2"
    assert loaded.nodes[0].node.intent.summary == summary


def test_init_refuses_to_overwrite(tmp_path: Path) -> None:
    (tmp_path / "intensify.yaml").write_text("keep me")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert (tmp_path / "intensify.yaml").read_text() == "keep me"


def test_init_force_overwrites(tmp_path: Path) -> None:
    (tmp_path / "intensify.yaml").write_text("old")
    result = runner.invoke(app, ["init", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert load_and_validate(tmp_path).ok


def test_init_rejects_invalid_project_id(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path), "--id", "Not Valid"])
    assert result.exit_code == 2
    assert not (tmp_path / "intensify.yaml").exists()


@pytest.mark.parametrize(
    ("name", "slug"),
    [("payments-service", "payments-service"), ("My App", "my-app"), ("--__", "project")],
)
def test_slugify(name: str, slug: str) -> None:
    assert slugify(name) == slug
