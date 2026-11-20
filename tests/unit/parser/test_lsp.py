"""LSP-backed call resolution, against a fake language server (``fake_lsp.py``)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from intensify.parser import AnalysisResult, analyze_repo
from intensify.parser.lsp import LspOptions, _Symbols
from intensify.spec.models import Confidence, EdgeType

FAKE = Path(__file__).with_name("fake_lsp.py")


def position(root: Path, path: str, needle: str, offset: int = 0) -> tuple[int, int]:
    """0-based line and UTF-16 character of ``needle`` (plus ``offset``) in ``path``."""
    for number, line in enumerate((root / path).read_text().splitlines()):
        if needle in line:
            index = line.index(needle) + offset
            return number, len(line[:index].encode("utf-16-le")) // 2
    raise AssertionError(f"{needle!r} not in {path}")


class Fake:
    """Builds the fake server's answer table, and the options that start it."""

    def __init__(self, root: Path, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.table: dict[str, object] = {}
        self.table_file = tmp / "table.json"
        self.log = tmp / "requests.log"
        monkeypatch.setenv("FAKE_LSP_TABLE", str(self.table_file))
        monkeypatch.setenv("FAKE_LSP_LOG", str(self.log))

    def answer(self, at: tuple[str, str, int], to: tuple[str, str, int] | str) -> None:
        path, needle, offset = at
        line, char = position(self.root, path, needle, offset)
        if isinstance(to, str):
            location = {"uri": to, "range": _range(0, 0)}
        else:
            target_line, target_char = position(self.root, to[0], to[1], to[2])
            location = {
                "uri": (self.root / to[0]).resolve().as_uri(),
                "range": _range(target_line, target_char),
            }
        self.table[f"{path}:{line}:{char}"] = [location]

    def options(self, *languages: str, **kwargs: float) -> LspOptions:
        self.table_file.write_text(json.dumps(self.table))
        command = [sys.executable, str(FAKE)]
        return LspOptions(
            languages=frozenset(languages),
            commands=dict.fromkeys(languages, command),
            **kwargs,
        )


def _range(line: int, char: int) -> dict[str, object]:
    return {"start": {"line": line, "character": char}, "end": {"line": line, "character": char}}


def calls(result: AnalysisResult) -> set[tuple[str, str]]:
    return {(d.source, d.target) for d in result.dependencies if d.type is EdgeType.CALLS}


@pytest.fixture
def py_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "lib.py").write_text(
        "class Product:\n    def ship(self):\n        return 1\n\n\n"
        "class Builder:\n    def build(self):\n        return Product()\n\n\n"
        "def make():\n    return Builder()\n"
    )
    (root / "app.py").write_text(
        "from lib import make\n\n\n"
        "def run():\n"
        '    label = "héllo 😀"; make().build().ship()\n'
        "    print(label)\n"
    )
    return root


def test_server_resolves_what_static_analysis_misses(
    py_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = analyze_repo(py_repo)
    assert calls(static) == {
        ("app.run", "lib.make"),
        ("lib.Builder.build", "lib.Product"),
        ("lib.make", "lib.Builder"),
    }

    fake = Fake(py_repo, tmp_path, monkeypatch)
    # Positions after non-ASCII text, so the UTF-16 conversion matters.
    fake.answer(("app.py", "build()", 0), ("lib.py", "def build", 4))
    fake.answer(("app.py", "ship()", 0), ("lib.py", "def ship", 4))
    fake.answer(("app.py", "print", 0), "file:///usr/lib/python3/builtins.pyi")  # outside
    result = analyze_repo(py_repo, lsp=fake.options("python"))

    assert calls(result) == calls(static) | {
        ("app.run", "lib.Builder.build"),
        ("app.run", "lib.Product.ship"),
    }
    assert {d.confidence for d in result.dependencies if d.source == "app.run"} == {Confidence.high}
    assert result.warnings == []


def test_overloads_follow_the_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Cart.java").write_text(
        "public class Cart {\n"
        "  public void add(String sku) {}\n"
        "  public void add(int cents) {}\n"
        "  public void fill() { add(5); }\n"
        "}\n"
    )
    # Without a server, the call goes to the first overload, as before.
    assert ("Cart.Cart.fill", "Cart.Cart.add") in calls(analyze_repo(root))

    fake = Fake(root, tmp_path, monkeypatch)
    fake.answer(("Cart.java", "add(5)", 0), ("Cart.java", "add(int", 0))
    result = analyze_repo(root, lsp=fake.options("java"))
    assert ("Cart.Cart.fill", "Cart.Cart.add-2") in calls(result)
    assert ("Cart.Cart.fill", "Cart.Cart.add") not in calls(result)


def test_kotlin_calls_into_java(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Helper.java").write_text("public class Helper {\n  public static void run() {}\n}\n")
    (root / "Main.kt").write_text("fun main() {\n    Helper.run()\n}\n")
    assert calls(analyze_repo(root)) == set()

    fake = Fake(root, tmp_path, monkeypatch)
    fake.answer(("Main.kt", "run()", 0), ("Helper.java", "run()", 0))
    result = analyze_repo(root, lsp=fake.options("kotlin"))
    assert calls(result) == {("Main.main", "Helper.Helper.run")}


def test_definitions_map_to_the_symbol_they_name(tmp_path: Path) -> None:
    (tmp_path / "shapes.ts").write_text(
        "export class Circle {\n  constructor(public r: number) {}\n  area() { return 1; }\n}\n"
    )
    (tmp_path / "Box.java").write_text("public class Box {\n  public Box(int size) {}\n}\n")
    result = analyze_repo(tmp_path)
    symbols = _Symbols(tmp_path, result.symbols)

    def at(path: str, needle: str) -> str | None:
        line, char = position(tmp_path, path, needle)
        uri = (tmp_path / path).resolve().as_uri()
        found = symbols.target({"uri": uri, "range": _range(line, char)})
        return found.id if found else None

    assert at("shapes.ts", "area") == "shapes.Circle.area"
    assert at("shapes.ts", "constructor") == "shapes.Circle"  # a constructor means its class
    assert at("Box.java", "Box(int") == "Box.Box"  # a Java constructor too
    assert at("shapes.ts", "r: number") is None  # a parameter is not a symbol
    assert symbols.target({"uri": "file:///elsewhere/x.ts", "range": _range(0, 0)}) is None


def test_slow_server_keeps_static_results(
    py_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake(py_repo, tmp_path, monkeypatch)
    fake.answer(("app.py", "build()", 0), ("lib.py", "def build", 4))
    monkeypatch.setenv("FAKE_LSP_DELAY", "2")
    result = analyze_repo(py_repo, lsp=fake.options("python", request_timeout=0.2, timeout=1.0))
    assert calls(result) == calls(analyze_repo(py_repo))
    assert len(result.warnings) == 1
    assert result.warnings[0].startswith("lsp: python server answered 0 of ")


def test_missing_or_broken_servers_fall_back(
    py_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = analyze_repo(py_repo)
    broken = analyze_repo(
        py_repo,
        lsp=LspOptions(commands={"python": [str(py_repo / "no-such-server")]}),
    )
    assert calls(broken) == calls(static)
    assert broken.warnings[0].startswith("lsp: python server failed (could not start")

    monkeypatch.setenv("PATH", "")
    missing = analyze_repo(py_repo, lsp=LspOptions())
    assert calls(missing) == calls(static)
    assert missing.warnings == ["lsp: no server for python on PATH; using static resolution"]


def test_only_unresolved_calls_are_sent(
    py_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake(py_repo, tmp_path, monkeypatch)
    analyze_repo(py_repo, lsp=fake.options("python"))
    asked = set(fake.log.read_text().split())
    make_line, make_char = position(py_repo, "app.py", "make()")
    assert f"app.py:{make_line}:{make_char}" not in asked  # resolved statically
    assert len(asked) == 3  # build, ship, print
