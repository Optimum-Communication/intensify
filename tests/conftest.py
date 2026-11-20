from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = REPO_ROOT / "spec" / "v1.0"

WriteSpec = Callable[..., Path]


@pytest.fixture
def write_spec(tmp_path: Path) -> WriteSpec:
    """Write a dedented YAML file under tmp_path and return its path."""

    def write(text: str, name: str = "intensify.yaml") -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        return path

    return write
