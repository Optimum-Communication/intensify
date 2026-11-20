from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from intensify.cli import app
from intensify.cli.analyze import run as analyze
from intensify.cli.init import render
from tests.conftest import CopyRepo

runner = CliRunner()


@pytest.fixture
def repo(copy_repo: CopyRepo) -> Path:
    root = copy_repo("py_shop")
    (root / "intensify.yaml").write_text(render("py-shop", "Shop", "A tiny shop."))
    analyze(root)
    return root


def add_call(root: Path) -> None:
    path = root / "src/shop/pricing.py"
    path.write_text(
        path.read_text().replace(
            "return round(cents * (1 + TAX_RATE))", "return round(apply_discount(cents, 'X'))"
        )
    )


def test_clean_repo_exits_zero(repo: Path) -> None:
    result = runner.invoke(app, ["drift", str(repo)])
    assert result.exit_code == 0, result.output
    assert "✓ No blocking drift" in result.output
    assert "notes hidden" in result.output


def test_verbose_shows_notes(repo: Path) -> None:
    result = runner.invoke(app, ["drift", str(repo), "-v"])
    assert "unenriched_node" in result.output


def test_drift_exits_one_and_writes_reports(repo: Path, tmp_path: Path) -> None:
    add_call(repo)
    out = {name: tmp_path / name for name in ("r.sarif", "r.md", "r.json")}
    result = runner.invoke(
        app,
        [
            "drift",
            str(repo),
            "--sarif",
            str(out["r.sarif"]),
            "--markdown",
            str(out["r.md"]),
            "--codequality",
            str(out["r.json"]),
        ],
    )
    assert result.exit_code == 1
    assert "error   undocumented_edge   src/shop/pricing.py:18" in result.output
    assert json.loads(out["r.sarif"].read_text())["version"] == "2.1.0"
    assert out["r.md"].read_text().startswith("<!-- intensify-drift -->")
    assert isinstance(json.loads(out["r.json"].read_text()), list)


@pytest.mark.parametrize("fmt", ["json", "sarif", "codequality", "markdown"])
def test_stdout_formats(repo: Path, fmt: str) -> None:
    add_call(repo)
    result = runner.invoke(app, ["drift", str(repo), "--format", fmt])
    assert result.exit_code == 1
    if fmt != "markdown":
        json.loads(result.output)


def test_fail_on_override(repo: Path) -> None:
    add_call(repo)
    result = runner.invoke(app, ["drift", str(repo), "--fail-on", "missing_node"])
    assert result.exit_code == 0, result.output
    assert "warning undocumented_edge" in result.output


def test_invalid_spec_exits_two(repo: Path) -> None:
    (repo / "intensify.yaml").write_text('intensify: "1.0"\nproject: {id: Bad}\n')
    result = runner.invoke(app, ["drift", str(repo)])
    assert result.exit_code == 2
    assert "drift can't be checked" in result.output


def test_bad_base_exits_two(repo: Path) -> None:
    result = runner.invoke(app, ["drift", str(repo), "--base", "main"])
    assert result.exit_code == 2
    assert "not inside a git repository" in result.output
