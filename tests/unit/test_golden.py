"""Golden files: the exact intensify.yaml that `init --analyze` produces for each fixture repo.

Regenerate after an intended change with:

    INTENSIFY_UPDATE_GOLDEN=1 uv run pytest tests/unit/test_golden.py
"""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from intensify.cli import app
from tests.conftest import REPO_ROOT, CopyRepo

GOLDEN = REPO_ROOT / "tests" / "golden"
runner = CliRunner()


@pytest.mark.parametrize(
    "fixture", ["py_shop", "ts_shop", "java_shop", "kotlin_shop", "go_shop", "data_shop"]
)
def test_init_analyze_matches_golden(copy_repo: CopyRepo, fixture: str) -> None:
    root = copy_repo(fixture)
    project_id = fixture.replace("_", "-")
    result = runner.invoke(
        app, ["init", str(root), "--id", project_id, "--name", fixture, "--analyze"]
    )
    assert result.exit_code == 0, result.output
    actual = (root / "intensify.yaml").read_text()

    golden = GOLDEN / f"{fixture}.intensify.yaml"
    if os.environ.get("INTENSIFY_UPDATE_GOLDEN"):
        golden.write_text(actual)
    assert actual == golden.read_text(), f"run with INTENSIFY_UPDATE_GOLDEN=1 to update {golden}"
