from __future__ import annotations

from pathlib import Path

import pytest

from intensify.parser import AnalysisResult, AnalyzeOptions, Symbol, analyze_repo
from intensify.parser.python import is_test_path
from intensify.spec.models import EdgeType, NodeKind
from tests.conftest import FIXTURE_REPOS


@pytest.fixture(scope="module")
def result() -> AnalysisResult:
    return analyze_repo(FIXTURE_REPOS / "py_shop")


def sym(result: AnalysisResult, sym_id: str) -> Symbol:
    return next(s for s in result.symbols if s.id == sym_id)


def edges(result: AnalysisResult, edge_type: EdgeType) -> set[tuple[str, str]]:
    return {(d.source, d.target) for d in result.dependencies if d.type is edge_type}


def test_module_ids_follow_packages(result: AnalysisResult) -> None:
    modules = {s.id: s.path for s in result.symbols if s.kind is NodeKind.module}
    assert modules["shop"] == "src/shop/__init__.py"
    assert modules["shop.payments.gateway"] == "src/shop/payments/gateway.py"
    # Not in a package: the ID comes from the path.
    assert modules["tests.test_cart"] == "tests/test_cart.py"


def test_module_parents_are_packages(result: AnalysisResult) -> None:
    assert sym(result, "shop.payments.gateway").parent == "shop.payments"
    assert sym(result, "shop.payments").parent == "shop"
    assert sym(result, "shop").parent is None


def test_methods_and_contracts(result: AnalysisResult) -> None:
    add = sym(result, "shop.cart.Cart.add")
    assert (add.kind, add.symbol, add.parent) == (NodeKind.function, "Cart.add", "shop.cart.Cart")
    assert add.signature == "def add(self, sku: str, cents: int, qty: int = 1) -> None"
    assert add.contract == {
        "inputs": [
            {"name": "sku", "type": "str"},
            {"name": "cents", "type": "int"},
            {"name": "qty", "type": "int", "required": False},
        ],
        "errors": [{"type": "CartError"}],
    }
    assert add.complexity == {"cyclomatic": 3, "loc": 6}
    assert add.fingerprint is not None
    assert add.fingerprint.startswith("sha256:")


def test_static_methods_keep_first_parameter_and_splats(result: AnalysisResult) -> None:
    currency = sym(result, "shop.payments.gateway.StripeGateway.currency")
    assert currency.contract is not None
    assert currency.contract["inputs"] == [
        {"name": "*codes", "type": "str", "required": False},
        {"name": "**opts", "required": False},
    ]


def test_private_symbols_are_extracted_but_not_emitted(result: AnalysisResult) -> None:
    assert not sym(result, "shop.cart.Cart._debug").emit
    assert not sym(result, "shop._internal").emit
    assert not sym(result, "shop._internal.audit").emit
    assert sym(result, "shop.cart.Cart.__init__").emit


def test_test_modules_are_emitted_without_their_functions(result: AnalysisResult) -> None:
    assert sym(result, "tests.test_cart").emit
    assert not sym(result, "tests.test_cart.test_add").emit


def test_calls_resolve_through_imports_aliases_and_self(result: AnalysisResult) -> None:
    calls = edges(result, EdgeType.CALLS)
    assert ("shop.cart.Cart.total", "shop.pricing.subtotal") in calls  # module attribute
    assert ("shop.cart.Cart.total", "shop.pricing.apply_discount") in calls  # aliased import
    assert ("shop.cart.Cart.add", "shop._internal.audit") in calls  # private target, still found
    assert (
        "shop.payments.gateway.StripeGateway.charge",
        "shop.payments.retry.with_retries",
    ) in calls  # relative import
    assert (
        "shop.payments.gateway.StripeGateway.checkout",
        "shop.payments.gateway.StripeGateway.charge",
    ) in calls  # self.method()
    # Builtins and calls on unknown objects are not edges.
    assert not any(target.endswith(("append", "repr", "print")) for _, target in calls)


def test_inheritance_and_verification(result: AnalysisResult) -> None:
    assert edges(result, EdgeType.IMPLEMENTS) == {
        ("shop.payments.gateway.StripeGateway", "shop.payments.gateway.Gateway")
    }
    assert edges(result, EdgeType.VERIFIES) == {
        ("tests.test_cart", "shop.cart.Cart"),
        ("tests.test_cart", "shop.pricing.apply_discount"),
    }


def test_depth_and_public_only_options() -> None:
    root = FIXTURE_REPOS / "py_shop"
    shallow = analyze_repo(root, options=AnalyzeOptions(depth=NodeKind.module))
    assert {s.kind for s in shallow.symbols if s.emit} == {NodeKind.module}
    everything = analyze_repo(root, options=AnalyzeOptions(public_only=False))
    assert sym(everything, "shop.cart.Cart._debug").emit


def test_fingerprint_ignores_names_but_not_logic(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def one(x):\n    return x + 1\n")
    (tmp_path / "b.py").write_text("def renamed(y):\n    # a comment\n    return y + 1\n")
    (tmp_path / "c.py").write_text("def one(x):\n    return x + 2\n")
    result = analyze_repo(tmp_path)
    prints = {s.path: s.fingerprint for s in result.symbols if s.kind is NodeKind.function}
    assert prints["a.py"] == prints["b.py"]
    assert prints["a.py"] != prints["c.py"]


def test_syntax_errors_are_warnings(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def ok():\n    pass\n\ndef broken(:\n")
    result = analyze_repo(tmp_path)
    assert result.warnings == ["broken.py: syntax errors; results may be incomplete"]
    assert any(s.id == "broken.ok" for s in result.symbols)


@pytest.mark.parametrize(
    ("path", "is_test"),
    [
        ("shop/test_cart.py", True),
        ("shop/cart_test.py", True),
        ("tests/helpers.py", True),
        ("blog/tests.py", True),  # Django keeps an app's tests in tests.py
        ("conftest.py", True),
        ("shop/testing.py", False),
        ("shop/contest.py", False),
    ],
)
def test_is_test_path(path: str, is_test: bool) -> None:
    assert is_test_path(path) is is_test
