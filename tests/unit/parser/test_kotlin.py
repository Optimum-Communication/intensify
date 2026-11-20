from __future__ import annotations

from pathlib import Path

import pytest

from intensify.parser import AnalysisResult, Symbol, analyze_repo, symbol_span
from intensify.spec.models import EdgeType, NodeKind
from tests.conftest import FIXTURE_REPOS


@pytest.fixture(scope="module")
def result() -> AnalysisResult:
    return analyze_repo(FIXTURE_REPOS / "kotlin_shop")


def sym(result: AnalysisResult, sym_id: str) -> Symbol:
    return next(s for s in result.symbols if s.id == sym_id)


def edges(result: AnalysisResult, edge_type: EdgeType) -> set[tuple[str, str]]:
    return {(d.source, d.target) for d in result.dependencies if d.type is edge_type}


def test_module_ids_follow_packages(result: AnalysisResult) -> None:
    modules = {s.id: (s.path, s.language) for s in result.symbols if s.kind is NodeKind.module}
    assert modules["com.shop.Cart"] == ("src/main/kotlin/com/shop/Cart.kt", "kotlin")
    assert "com.shop.payments.Gateway" in modules
    assert sym(result, "com.shop.CartTest").is_test
    assert result.warnings == []
    # Gradle's Kotlin scripts are build configuration, not analyzed code.
    assert all(path.endswith(".kt") for path in result.files)


def test_declarations_and_contracts(result: AnalysisResult) -> None:
    add = sym(result, "com.shop.Cart.Cart.add")
    assert (add.kind, add.symbol, add.parent) == (
        NodeKind.function,
        "Cart.add",
        "com.shop.Cart.Cart",
    )
    # Annotations and KDoc are not part of the signature.
    assert add.signature == "fun add(sku: String, cents: Int, qty: Int = 1)"
    assert add.contract == {
        "inputs": [
            {"name": "sku", "type": "String"},
            {"name": "cents", "type": "Int"},
            {"name": "qty", "type": "Int", "required": False},  # has a default
        ],
        "errors": [{"type": "CartError"}],  # @Throws and throw
    }
    assert add.complexity == {"cyclomatic": 3, "loc": 8}
    assert sym(result, "com.shop.Cart.Cart.total").contract == {
        "inputs": [{"name": "codes", "type": "vararg String", "required": False}],
        "outputs": [{"type": "Int"}],
    }
    assert sym(result, "com.shop.Cart.Cart.add-2").symbol == "Cart.add"  # overload
    assert sym(result, "com.shop.Cart.Line").signature == (
        "data class Line(val sku: String, val cents: Int, val qty: Int = 1)"
    )
    assert sym(result, "com.shop.Pricing.Pricing").signature == "object Pricing"
    assert sym(result, "com.shop.Pricing.Pricing.roundCents").complexity["cyclomatic"] == 3
    assert sym(result, "com.shop.PriceRule.Discount.None").kind is NodeKind.class_
    # Companion object members sit under their class; extensions under their file.
    assert sym(result, "com.shop.payments.Gateway.Gateway.sandbox").parent == (
        "com.shop.payments.Gateway.Gateway"
    )
    assert sym(result, "com.shop.Pricing.halfTotal").signature == "fun Cart.halfTotal(): Int"


def test_visibility(result: AnalysisResult) -> None:
    assert not sym(result, "com.shop.Cart.Cart.debug").emit  # internal
    assert not sym(result, "com.shop.Cart.Cart.secret").emit  # private
    assert not sym(result, "com.shop.Pricing.HalfOff").emit  # internal class
    assert not sym(result, "com.shop.Pricing.HalfOff.apply").emit  # inside a hidden class
    assert sym(result, "com.shop.payments.Gateway.Gateway.send").emit  # protected
    assert sym(result, "com.shop.Cart.Cart.empty").emit  # default is public


def test_calls_resolve_through_imports_and_inferred_types(result: AnalysisResult) -> None:
    assert edges(result, EdgeType.CALLS) == {
        ("com.shop.Cart.Cart.add", "com.shop.CartError.CartError"),  # constructor
        ("com.shop.Cart.Cart.add", "com.shop.Cart.Line"),
        ("com.shop.Cart.Cart.add", "com.shop.util.Log.Log.info"),  # aliased import, object
        ("com.shop.Cart.Cart.add-2", "com.shop.Cart.Cart.add"),
        ("com.shop.Cart.Cart.total", "com.shop.Pricing.Pricing.subtotal"),
        ("com.shop.Cart.Cart.total", "com.shop.PriceRule.PriceRule.apply"),  # for over a property
        ("com.shop.Cart.Cart.checkout", "com.shop.Cart.Cart.total"),
        ("com.shop.Cart.Cart.checkout", "com.shop.payments.Gateway.Gateway.charge"),  # property
        ("com.shop.Cart.Cart.empty", "com.shop.Cart.Cart"),
        ("com.shop.Cart.Cart.empty", "com.shop.payments.Gateway.Gateway.sandbox"),  # companion
        ("com.shop.Pricing.Pricing.subtotal", "com.shop.Cart.Line.amount"),  # for over List<Line>
        ("com.shop.Pricing.Pricing.subtotal", "com.shop.Pricing.Pricing.roundCents"),
        ("com.shop.Pricing.HalfOff.apply", "com.shop.util.Log.Log.warn"),  # star import
        ("com.shop.Pricing.halfTotal", "com.shop.Pricing.HalfOff"),
        ("com.shop.Pricing.halfTotal", "com.shop.Pricing.HalfOff.apply"),  # result of a call
        ("com.shop.Pricing.halfTotal", "com.shop.Cart.Cart.total"),  # receiver of an extension
        ("com.shop.payments.Gateway.Gateway.charge", "com.shop.payments.Gateway.Gateway.send"),
        ("com.shop.payments.Gateway.Gateway.charge", "com.shop.util.Log.Log.info"),
        ("com.shop.payments.Gateway.Gateway.send", "com.shop.payments.Gateway.Receipt"),
        ("com.shop.payments.Gateway.Gateway.sandbox", "com.shop.payments.Gateway.Gateway"),
    }


def test_heritage_and_test_links(result: AnalysisResult) -> None:
    assert edges(result, EdgeType.IMPLEMENTS) == {
        ("com.shop.PriceRule.Discount", "com.shop.PriceRule.PriceRule"),
        ("com.shop.PriceRule.Discount.None", "com.shop.PriceRule.Discount"),
        ("com.shop.Pricing.HalfOff", "com.shop.PriceRule.PriceRule"),
    }
    assert edges(result, EdgeType.VERIFIES) == {
        ("com.shop.CartTest", "com.shop.Cart.Cart"),
        ("com.shop.CartTest", "com.shop.Cart.Cart.add"),
        ("com.shop.CartTest", "com.shop.Pricing.halfTotal"),  # extension on a local
        ("com.shop.CartTest", "com.shop.payments.Gateway.Gateway"),
    }


def test_inferred_results_inherited_members_and_super(tmp_path: Path) -> None:
    (tmp_path / "Base.kt").write_text(
        "package p\n\nopen class Base {\n  open fun run() {}\n  fun stop() {}\n"
        "  fun self() = this\n}\n"
    )
    (tmp_path / "Child.kt").write_text(
        "package p\n\nclass Child : Base() {\n"
        "  override fun run() { super.run(); stop() }\n"
        "  fun go(other: Child) { val again = other.self(); again.stop() }\n}\n"
        "fun make(): Child = Child()\n"
        "fun use() { make().run() }\n"
    )
    result = analyze_repo(tmp_path)
    assert edges(result, EdgeType.IMPLEMENTS) == {("p.Child.Child", "p.Base.Base")}
    assert edges(result, EdgeType.CALLS) == {
        ("p.Child.Child.run", "p.Base.Base.run"),  # super
        ("p.Child.Child.run", "p.Base.Base.stop"),  # inherited
        ("p.Child.Child.go", "p.Base.Base.self"),
        ("p.Child.Child.go", "p.Base.Base.stop"),  # through an expression-body result
        ("p.Child.make", "p.Child.Child"),
        ("p.Child.use", "p.Child.make"),
        ("p.Child.use", "p.Child.Child.run"),  # through a declared result type
    }


def test_lambda_parameters_shadow_functions(tmp_path: Path) -> None:
    (tmp_path / "A.kt").write_text(
        "package a\n\nfun helper() {}\n"
        "fun run(xs: List<Int>, helper: () -> Unit) { helper(); xs.forEach { it.helper() } }\n"
    )
    assert edges(analyze_repo(tmp_path), EdgeType.CALLS) == set()


def test_fingerprint_ignores_comments(tmp_path: Path) -> None:
    (tmp_path / "A.kt").write_text("fun f(): Int = 1\n")
    before = sym(analyze_repo(tmp_path), "A.f").fingerprint
    (tmp_path / "A.kt").write_text("/** Doc. */\nfun f(): Int = // one\n    1\n")
    assert sym(analyze_repo(tmp_path), "A.f").fingerprint == before


def test_symbol_span() -> None:
    root = FIXTURE_REPOS / "kotlin_shop"
    assert symbol_span(root, "src/main/kotlin/com/shop/Cart.kt", "Cart.checkout") == (34, 37)


def test_syntax_errors(tmp_path: Path) -> None:
    (tmp_path / "Broken.kt").write_text("package p\n\nclass Broken { fun f( { }\n")
    result = analyze_repo(tmp_path)
    assert result.warnings == ["Broken.kt: syntax errors; results may be incomplete"]
