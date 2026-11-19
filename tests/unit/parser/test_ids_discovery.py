from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from intensify.parser.discovery import discover
from intensify.parser.ids import Allocator, path_id, segment


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/shop/cart.py", "shop.cart"),
        ("lib/a/b.ts", "a.b"),
        ("app/main.py", "app.main"),
        ("src/cart.test.ts", "cart-test"),
        ("src/index.ts", "index"),
        ("src/util/index.ts", "util"),
        ("main.py", "main"),
        ("src/[id]/page.tsx", "id.page"),
    ],
)
def test_path_id(path: str, expected: str) -> None:
    assert path_id(path, index_names=frozenset({"index"})) == expected


def test_segment_never_empty() -> None:
    assert segment("...") == "_"


def test_allocator_suffixes_duplicates() -> None:
    alloc = Allocator()
    assert [alloc.claim("a"), alloc.claim("a"), alloc.claim("a"), alloc.claim("b")] == [
        "a",
        "a-2",
        "a-3",
        "b",
    ]


def make_tree(root: Path) -> None:
    for rel in [
        "src/app.py",
        "src/gen/out.py",
        "src/app.min.js",
        "types/x.d.ts",
        "node_modules/pkg/index.js",
        "README.md",
        "tests/test_app.py",
    ]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("")
    (root / ".gitignore").write_text("src/gen/\n")


EXTS = (".py", ".ts", ".js")


def test_discover_without_git_reads_gitignore(tmp_path: Path) -> None:
    make_tree(tmp_path)
    assert discover(tmp_path, extensions=EXTS) == ["src/app.py", "tests/test_app.py"]


def test_discover_include_and_exclude(tmp_path: Path) -> None:
    make_tree(tmp_path)
    assert discover(tmp_path, extensions=EXTS, include=["src/**"]) == ["src/app.py"]
    assert discover(tmp_path, extensions=EXTS, exclude=["tests/"]) == ["src/app.py"]


def test_discover_uses_git_when_available(tmp_path: Path) -> None:
    make_tree(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("src/gen/\ntests/\n")
    assert discover(tmp_path, extensions=EXTS) == ["src/app.py"]
