from __future__ import annotations

from pathlib import Path

import pytest

from intensify.parser import AnalysisResult, Symbol, analyze_repo, symbol_span
from intensify.spec.models import Confidence, EdgeType, NodeKind
from tests.conftest import FIXTURE_REPOS


@pytest.fixture(scope="module")
def result() -> AnalysisResult:
    return analyze_repo(FIXTURE_REPOS / "go_shop")


def sym(result: AnalysisResult, sym_id: str) -> Symbol:
    return next(s for s in result.symbols if s.id == sym_id)


def edges(result: AnalysisResult, edge_type: EdgeType) -> set[tuple[str, str]]:
    return {(d.source, d.target) for d in result.dependencies if d.type is edge_type}


def test_modules_and_skipped_files(result: AnalysisResult) -> None:
    modules = {s.id: (s.path, s.language) for s in result.symbols if s.kind is NodeKind.module}
    assert modules["cart.cart"] == ("cart/cart.go", "go")
    assert sym(result, "cart.cart_test").is_test
    # Generated files, vendored code and testdata are skipped.
    assert set(result.files) == {
        "cart/cart.go",
        "cart/cart_test.go",
        "cart/errors.go",
        "cmd/shop/main.go",
        "internal/log/log.go",
        "payments/gateway.go",
        "pricing/pricing.go",
    }
    assert result.warnings == []


def test_types_functions_and_contracts(result: AnalysisResult) -> None:
    add = sym(result, "cart.cart.Cart.Add")
    assert (add.kind, add.symbol, add.parent) == (NodeKind.function, "Cart.Add", "cart.cart.Cart")
    assert add.signature == "func (c *Cart) Add(sku string, cents, qty int) error"
    assert add.contract == {
        "inputs": [
            {"name": "sku", "type": "string"},
            {"name": "cents", "type": "int"},
            {"name": "qty", "type": "int"},
        ],
        "outputs": [{"type": "error"}],
        "errors": [{"type": "*Error"}],  # returned, and has an Error method
    }
    assert add.complexity == {"cyclomatic": 3, "loc": 8}
    assert sym(result, "cart.cart.New").contract == {
        "inputs": [{"name": "rules", "type": "...pr.Rule", "required": False}],
        "outputs": [{"type": "*Cart"}],
    }
    assert sym(result, "payments.gateway.Gateway.Charge").contract["errors"] == [  # type: ignore[index]
        {"type": "ErrDeclined"}  # sentinel
    ]
    assert sym(result, "payments.gateway.Gateway.Charge").complexity["cyclomatic"] == 6
    assert sym(result, "pricing.pricing.Round").complexity["cyclomatic"] == 3
    assert sym(result, "cart.cart.Cart").signature == "type Cart struct"
    assert sym(result, "pricing.pricing.Cents").signature == "type Cents int"
    assert sym(result, "pricing.pricing.Map").signature == (
        "func Map[T any](xs []T, f func(T) T) []T"
    )
    rule_apply = sym(result, "pricing.pricing.Rule.Apply")
    assert (rule_apply.signature, rule_apply.parent) == (
        "Apply(cents int) int",
        "pricing.pricing.Rule",
    )


def test_methods_sit_under_their_type_across_files(result: AnalysisResult) -> None:
    empty = sym(result, "cart.cart.Cart.Empty")
    assert (empty.path, empty.symbol, empty.parent) == (
        "cart/errors.go",
        "Cart.Empty",
        "cart.cart.Cart",
    )


def test_visibility(result: AnalysisResult) -> None:
    assert not sym(result, "cart.cart.Cart.debug").emit
    assert not sym(result, "pricing.pricing.roundDown").emit
    assert not sym(result, "payments.gateway.Gateway.try").emit
    assert not sym(result, "cmd.shop.main.main").emit
    assert sym(result, "pricing.pricing.HalfOff.Apply").emit


def test_calls_resolve_through_imports_and_types(result: AnalysisResult) -> None:
    assert edges(result, EdgeType.CALLS) == {
        ("cart.cart.New", "cart.cart.Cart"),  # composite literal
        ("cart.cart.New", "internal.log.log.New"),  # import
        ("cart.cart.Cart.Add", "cart.errors.Error"),
        ("cart.cart.Cart.Add", "cart.cart.Line"),
        ("cart.cart.Cart.Add", "internal.log.log.Logger.Info"),  # through a field
        ("cart.cart.Cart.Total", "pricing.pricing.Rule.Apply"),  # range over a field, aliased
        ("cart.cart.Cart.Total", "pricing.pricing.Round"),
        ("cmd.shop.main.main", "cart.cart.New"),
        ("cmd.shop.main.main", "cart.cart.Cart.Add"),  # result of New
        ("cmd.shop.main.main", "payments.gateway.Gateway.Charge"),  # var declaration
        ("internal.log.log.New", "internal.log.log.Logger"),
        ("payments.gateway.Gateway.Charge", "cart.cart.Cart.Empty"),  # parameter
        ("payments.gateway.Gateway.Charge", "cart.cart.Cart.Total"),
        ("payments.gateway.Gateway.Charge", "payments.gateway.Gateway.try"),  # receiver
    }


def test_embedding_interfaces_and_test_links(result: AnalysisResult) -> None:
    implements = {(d.source, d.target, d.confidence) for d in result.dependencies}
    assert {i for i in implements if i[0].startswith("pricing")} == {
        ("pricing.pricing.Named", "pricing.pricing.Rule", Confidence.high),  # embedded
        ("pricing.pricing.HalfOff", "pricing.pricing.Rule", Confidence.medium),  # method set
        ("pricing.pricing.HalfOff", "pricing.pricing.Named", Confidence.medium),
    }
    assert edges(result, EdgeType.VERIFIES) == {
        ("cart.cart_test", "cart.cart.New"),
        ("cart.cart_test", "cart.cart.Cart.Add"),
        ("cart.cart_test", "cart.cart.Cart.Total"),
        ("cart.cart_test", "pricing.pricing.HalfOff"),
    }


def test_without_go_mod_promoted_methods_and_dot_imports(tmp_path: Path) -> None:
    (tmp_path / "store").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "store" / "store.go").write_text(
        'package db\n\nimport "io"\n\n'
        "type Base struct{}\nfunc (Base) Close() error { return nil }\n"
        "type Store struct { Base }\n"
        "func Open() (*Store, error) { return &Store{}, nil }\n"
        "func (s *Store) Get(k string) string { return k }\n"
        "type ReadCloser interface { io.Reader; Close() error }\n"
        "type Closer interface { Close() error }\n"
    )
    (tmp_path / "app" / "app.go").write_text(
        'package app\n\nimport (\n\t"example.org/x/store"\n\t. "example.org/x/store"\n)\n\n'
        "func Run() {\n\ts, _ := db.Open()\n\t_ = s.Close()\n"
        '\tvar t *Store\n\tt.Get("x")\n\tf := func() { s.Get("y") }\n\tf()\n}\n'
    )
    result = analyze_repo(tmp_path)
    assert edges(result, EdgeType.CALLS) == {
        ("app.app.Run", "store.store.Open"),  # imported as its package clause name, db
        ("app.app.Run", "store.store.Base.Close"),  # promoted from the embedded Base
        ("app.app.Run", "store.store.Store.Get"),  # dot import, and inside a func literal
        ("store.store.Open", "store.store.Store"),
    }
    # ReadCloser embeds io.Reader, which isn't in the code, so it is never matched implicitly.
    assert {(d.source, d.target) for d in result.dependencies if d.type is EdgeType.IMPLEMENTS} == {
        ("store.store.Store", "store.store.Base"),
        ("store.store.Base", "store.store.Closer"),
        ("store.store.Store", "store.store.Closer"),
    }


def test_shadowed_names_are_not_calls(tmp_path: Path) -> None:
    (tmp_path / "a.go").write_text(
        "package a\n\nfunc helper() {}\n"
        "func Run(helper func()) { helper() }\n"
        "func Loop(xs []int) { for _, helper := range xs { _ = helper } }\n"
    )
    assert edges(analyze_repo(tmp_path), EdgeType.CALLS) == set()


def test_fingerprint_ignores_comments(tmp_path: Path) -> None:
    (tmp_path / "a.go").write_text("package a\n\nfunc F() int { return 1 }\n")
    before = sym(analyze_repo(tmp_path), "a.F").fingerprint
    (tmp_path / "a.go").write_text(
        "package a\n\n// F is one.\nfunc F() int {\n\treturn 1 // one\n}\n"
    )
    assert sym(analyze_repo(tmp_path), "a.F").fingerprint == before


def test_symbol_span() -> None:
    root = FIXTURE_REPOS / "go_shop"
    # Single-file parsing still finds a method whose type is declared in another file.
    assert symbol_span(root, "cart/errors.go", "Cart.Empty") == (13, 15)
    assert symbol_span(root, "cart/cart.go", "Cart.Add") == (31, 38)


def test_syntax_errors(tmp_path: Path) -> None:
    (tmp_path / "broken.go").write_text("package p\n\nfunc f( {\n")
    result = analyze_repo(tmp_path)
    assert result.warnings == ["broken.go: syntax errors; results may be incomplete"]
