from __future__ import annotations

from pathlib import Path

import pytest

from intensify.parser import AnalysisResult, Symbol, analyze_repo, symbol_span
from intensify.spec.models import EdgeType, NodeKind
from tests.conftest import FIXTURE_REPOS


@pytest.fixture(scope="module")
def result() -> AnalysisResult:
    return analyze_repo(FIXTURE_REPOS / "java_shop")


def sym(result: AnalysisResult, sym_id: str) -> Symbol:
    return next(s for s in result.symbols if s.id == sym_id)


def edges(result: AnalysisResult, edge_type: EdgeType) -> set[tuple[str, str]]:
    return {(d.source, d.target) for d in result.dependencies if d.type is edge_type}


def test_module_ids_follow_packages(result: AnalysisResult) -> None:
    modules = {s.id: (s.path, s.language) for s in result.symbols if s.kind is NodeKind.module}
    assert modules["com.shop.Cart"] == ("src/main/java/com/shop/Cart.java", "java")
    assert modules["com.shop.payments.Gateway"][0] == (
        "src/main/java/com/shop/payments/Gateway.java"
    )
    assert sym(result, "com.shop.CartTest").is_test
    assert result.warnings == []


def test_files_without_a_package_use_their_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Main.java").write_text("public class Main {}\n")
    result = analyze_repo(tmp_path)
    assert {s.id for s in result.symbols} == {"Main", "Main.Main"}


def test_types_methods_and_contracts(result: AnalysisResult) -> None:
    add = sym(result, "com.shop.Cart.Cart.add")
    assert (add.kind, add.symbol, add.parent) == (
        NodeKind.function,
        "Cart.add",
        "com.shop.Cart.Cart",
    )
    # Annotations and Javadoc are not part of the signature.
    assert add.signature == "public void add(String sku, int cents, int qty) throws CartError"
    assert add.contract == {
        "inputs": [
            {"name": "sku", "type": "String"},
            {"name": "cents", "type": "int"},
            {"name": "qty", "type": "int"},
        ],
        "errors": [{"type": "CartError"}],
    }
    assert add.complexity == {"cyclomatic": 3, "loc": 8}
    assert sym(result, "com.shop.Cart.Cart.total").contract == {
        "inputs": [{"name": "codes", "type": "String...", "required": False}],
        "outputs": [{"type": "int"}],
    }
    # Overloads share a symbol; the second gets a suffixed ID.
    assert sym(result, "com.shop.Cart.Cart.add-2").symbol == "Cart.add"
    assert sym(result, "com.shop.Cart.Cart.Line").signature == (
        "public record Line(String sku, int cents, int qty)"
    )
    assert sym(result, "com.shop.payments.Gateway.Gateway.Status").kind is NodeKind.class_
    assert sym(result, "com.shop.payments.Gateway.Gateway.charge").complexity == {
        "cyclomatic": 3,
        "loc": 9,
    }


def test_visibility(result: AnalysisResult) -> None:
    assert not sym(result, "com.shop.Cart.Cart.debug").emit  # private
    assert not sym(result, "com.shop.Pricing.Pricing.roundCents").emit  # package-private
    assert not sym(result, "com.shop.Pricing.HalfOff").emit  # package-private class
    assert not sym(result, "com.shop.Pricing.HalfOff.apply").emit  # inside a hidden class
    assert sym(result, "com.shop.payments.Gateway.Gateway.send").emit  # protected
    assert sym(result, "com.shop.PriceRule.PriceRule.apply").emit  # interface member


def test_calls_resolve_through_imports_and_declared_types(result: AnalysisResult) -> None:
    assert edges(result, EdgeType.CALLS) == {
        ("com.shop.Cart.Cart.add", "com.shop.CartError.CartError"),  # new, same package
        ("com.shop.Cart.Cart.add", "com.shop.Cart.Cart.Line"),  # nested record
        ("com.shop.Cart.Cart.add", "com.shop.util.Log.Log.info"),  # fully qualified
        ("com.shop.Cart.Cart.add-2", "com.shop.Cart.Cart.add"),  # unqualified, own class
        ("com.shop.Cart.Cart.checkout", "com.shop.payments.Gateway.Gateway.charge"),  # field
        ("com.shop.Cart.Cart.checkout", "com.shop.Cart.Cart.total"),
        ("com.shop.Cart.Cart.total", "com.shop.Pricing.Pricing.subtotal"),  # static
        ("com.shop.Cart.Cart.total", "com.shop.Pricing.Pricing.applyDiscount"),
        ("com.shop.Pricing.HalfOff.apply", "com.shop.Pricing.Pricing.roundCents"),
        ("com.shop.Pricing.Pricing.applyDiscount", "com.shop.Pricing.HalfOff"),
        ("com.shop.Pricing.Pricing.applyDiscount", "com.shop.util.Log.Log.warn"),  # static import
        ("com.shop.Pricing.Pricing.applyDiscount", "com.shop.PriceRule.PriceRule.apply"),  # local
        ("com.shop.Pricing.Pricing.subtotal", "com.shop.Cart.Cart.Line.amount"),  # for variable
        ("com.shop.Pricing.Pricing.subtotal", "com.shop.Pricing.Pricing.roundCents"),
        ("com.shop.payments.Gateway.Gateway.charge", "com.shop.util.Log.Log.info"),  # wildcard
        ("com.shop.payments.Gateway.Gateway.charge", "com.shop.payments.Gateway.Gateway.send"),
    }


def test_heritage_and_test_links(result: AnalysisResult) -> None:
    assert edges(result, EdgeType.IMPLEMENTS) == {
        ("com.shop.Pricing.HalfOff", "com.shop.PriceRule.PriceRule")
    }
    assert edges(result, EdgeType.VERIFIES) == {
        ("com.shop.CartTest", "com.shop.Cart.Cart"),
        ("com.shop.CartTest", "com.shop.Cart.Cart.add"),
        ("com.shop.CartTest", "com.shop.Cart.Cart.total"),
        ("com.shop.CartTest", "com.shop.Pricing.Pricing.applyDiscount"),
        ("com.shop.CartTest", "com.shop.payments.Gateway.Gateway"),
    }


def test_inherited_methods_and_super_calls(tmp_path: Path) -> None:
    (tmp_path / "Base.java").write_text(
        "package p;\npublic class Base { public void run() {} public void stop() {} }\n"
    )
    (tmp_path / "Child.java").write_text(
        "package p;\npublic class Child extends Base {\n"
        "  @Override public void run() { super.run(); stop(); }\n"
        "  public void go(Child other) { other.run(); }\n}\n"
    )
    result = analyze_repo(tmp_path)
    assert edges(result, EdgeType.IMPLEMENTS) == {("p.Child.Child", "p.Base.Base")}
    assert edges(result, EdgeType.CALLS) == {
        ("p.Child.Child.run", "p.Base.Base.run"),
        ("p.Child.Child.run", "p.Base.Base.stop"),
        ("p.Child.Child.go", "p.Child.Child.run"),
    }


def test_fingerprint_ignores_comments(tmp_path: Path) -> None:
    (tmp_path / "A.java").write_text("public class A { public int f() { return 1; } }\n")
    before = sym(analyze_repo(tmp_path), "A.A.f").fingerprint
    (tmp_path / "A.java").write_text(
        "public class A {\n  /** Doc. */\n  public int f() { return 1; } // one\n}\n"
    )
    assert sym(analyze_repo(tmp_path), "A.A.f").fingerprint == before


def test_symbol_span() -> None:
    root = FIXTURE_REPOS / "java_shop"
    assert symbol_span(root, "src/main/java/com/shop/Cart.java", "Cart.checkout") == (38, 40)


def test_descriptors_and_syntax_errors(tmp_path: Path) -> None:
    (tmp_path / "package-info.java").write_text("/** Docs. */\npackage p;\n")
    (tmp_path / "module-info.java").write_text("module p {}\n")
    (tmp_path / "Broken.java").write_text("package p;\npublic class Broken { void f( { }\n")
    result = analyze_repo(tmp_path)
    assert set(result.files) == {"Broken.java"}
    assert result.warnings == ["Broken.java: syntax errors; results may be incomplete"]
