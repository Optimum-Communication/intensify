from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from intensify.cli import app
from tests.conftest import CopyRepo

runner = CliRunner()


def init(root: Path) -> None:
    assert runner.invoke(app, ["init", str(root), "--id", "shop"]).exit_code == 0


def test_analyze_reports_and_writes(copy_repo: CopyRepo) -> None:
    root = copy_repo("py_shop")
    init(root)
    result = runner.invoke(app, ["analyze", str(root)])
    assert result.exit_code == 0, result.output
    assert "Analyzed 8 files (python: 8)" in result.output
    assert "+ 22 nodes added" in result.output
    assert "Wrote" in result.output

    again = runner.invoke(app, ["analyze", str(root)])
    assert "intensify.yaml is up to date." in again.output


def test_dry_run_prints_diff(copy_repo: CopyRepo) -> None:
    root = copy_repo("py_shop")
    init(root)
    before = (root / "intensify.yaml").read_text()
    result = runner.invoke(app, ["analyze", str(root), "--dry-run"])
    assert result.exit_code == 0
    assert "+  - id: shop.cart" in result.output
    assert "Dry run: no files were written." in result.output
    assert (root / "intensify.yaml").read_text() == before


def test_depth_module_creates_only_modules(copy_repo: CopyRepo) -> None:
    root = copy_repo("py_shop")
    init(root)
    result = runner.invoke(app, ["analyze", str(root), "--depth", "module"])
    assert result.exit_code == 0, result.output
    text = (root / "intensify.yaml").read_text()
    assert "kind: function" not in text
    assert "kind: class" not in text
    assert "id: shop.payments.gateway\n" in text


def test_invalid_depth_is_rejected(copy_repo: CopyRepo) -> None:
    root = copy_repo("py_shop")
    init(root)
    result = runner.invoke(app, ["analyze", str(root), "--depth", "block"])
    assert result.exit_code == 2


def test_invalid_spec_is_not_touched(copy_repo: CopyRepo) -> None:
    root = copy_repo("py_shop")
    (root / "intensify.yaml").write_text('intensify: "1.0"\nproject: {id: Bad Id}\n')
    result = runner.invoke(app, ["analyze", str(root)])
    assert result.exit_code == 1
    assert "Fix the errors above" in result.output
    assert (root / "intensify.yaml").read_text() == 'intensify: "1.0"\nproject: {id: Bad Id}\n'


def test_paths_option_accepts_absolute_paths(copy_repo: CopyRepo) -> None:
    root = copy_repo("py_shop")
    init(root)
    result = runner.invoke(
        app, ["analyze", str(root), "--paths", str(root / "src/shop/pricing.py")]
    )
    assert result.exit_code == 0, result.output
    text = (root / "intensify.yaml").read_text()
    assert "id: shop.pricing.apply_discount" in text
    assert "id: shop.cart.Cart" not in text
