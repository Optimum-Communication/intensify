from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from intensify.spec import load_and_validate
from intensify.spec.schema import load_schema
from tests.conftest import SPEC_DIR

EXAMPLES = [*sorted(SPEC_DIR.glob("examples/*.yaml")), SPEC_DIR / "examples/split/intensify.yaml"]


def test_canonical_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(json.loads((SPEC_DIR / "schema.json").read_text()))


def test_packaged_schema_matches_canonical_schema() -> None:
    # The package ships a copy; `make gen` keeps it in sync.
    assert load_schema("1.0") == json.loads((SPEC_DIR / "schema.json").read_text())


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: str(p.relative_to(SPEC_DIR)))
def test_spec_examples_are_valid(example: Path) -> None:
    loaded = load_and_validate(example)
    assert loaded.diagnostics == []
    assert loaded.nodes
