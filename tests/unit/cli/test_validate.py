from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from intensify.cli import app
from tests.conftest import SPEC_DIR, WriteSpec

runner = CliRunner()

INVALID = """\
intensify: "1.0"
project: {id: demo}
nodes:
  - {id: demo, kind: repo, intent: {summary: Root}}
  - {id: demo.x, kind: module, parent_id: nope, intent: {summary: X}}
"""


def test_valid_spec_exits_zero() -> None:
    result = runner.invoke(app, ["validate", str(SPEC_DIR / "examples/full.yaml")])
    assert result.exit_code == 0, result.output
    assert "is valid: 6 nodes, 4 edges" in result.output


def test_invalid_spec_exits_one_with_located_errors(write_spec: WriteSpec) -> None:
    result = runner.invoke(app, ["validate", str(write_spec(INVALID))])
    assert result.exit_code == 1
    assert "intensify.yaml:5: error [unknown-parent] nodes[1].parent_id:" in result.output
    assert "✗ 1 error" in result.output


@pytest.mark.parametrize("valid", [True, False])
def test_json_output(write_spec: WriteSpec, valid: bool) -> None:
    path = SPEC_DIR / "examples/split" if valid else write_spec(INVALID)
    result = runner.invoke(app, ["validate", str(path), "--format", "json"])
    payload = json.loads(result.output)
    assert payload["valid"] is valid
    assert result.exit_code == (0 if valid else 1)
    if valid:
        assert len(payload["files"]) == 3
        assert payload["diagnostics"] == []
    else:
        [diag] = payload["diagnostics"]
        assert diag["code"] == "unknown-parent"
        assert diag["line"] == 5
        assert diag["path"] == "nodes[1].parent_id"
