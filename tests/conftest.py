from __future__ import annotations

import shutil
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

# Sample repositories for the analyzers contain their own test files; never collect them.
collect_ignore_glob = ["fixtures/*"]

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_REPOS = REPO_ROOT / "tests" / "fixtures" / "repos"
SPEC_DIR = REPO_ROOT / "spec" / "v1.0"

WriteSpec = Callable[..., Path]
CopyRepo = Callable[[str], Path]


@pytest.fixture
def copy_repo(tmp_path: Path) -> CopyRepo:
    """Copy a fixture repository into tmp_path and return the copy's root."""

    def copy(name: str) -> Path:
        target = tmp_path / name
        shutil.copytree(FIXTURE_REPOS / name, target)
        return target

    return copy


@pytest.fixture
def write_spec(tmp_path: Path) -> WriteSpec:
    """Write a dedented YAML file under tmp_path and return its path."""

    def write(text: str, name: str = "intensify.yaml") -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        return path

    return write
