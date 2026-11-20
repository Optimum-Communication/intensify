from __future__ import annotations

from pathlib import Path

import pytest

from intensify.parser import AnalysisResult, Symbol, analyze_repo
from intensify.spec.models import EdgeType, NodeKind
from tests.conftest import FIXTURE_REPOS


@pytest.fixture(scope="module")
def result() -> AnalysisResult:
    return analyze_repo(FIXTURE_REPOS / "ts_shop")


def sym(result: AnalysisResult, sym_id: str) -> Symbol:
    return next(s for s in result.symbols if s.id == sym_id)


def edges(result: AnalysisResult, edge_type: EdgeType) -> set[tuple[str, str]]:
    return {(d.source, d.target) for d in result.dependencies if d.type is edge_type}


def test_module_ids_and_languages(result: AnalysisResult) -> None:
    modules = {s.id: (s.path, s.language) for s in result.symbols if s.kind is NodeKind.module}
    assert modules == {
        "cart": ("src/cart.ts", "typescript"),
        "cart-test": ("src/cart.test.ts", "typescript"),
        "index": ("src/index.ts", "typescript"),
        "pricing": ("src/pricing.ts", "typescript"),
        "types": ("src/types.ts", "typescript"),
        "util.log": ("src/util/log.js", "javascript"),
    }


def test_declarations_and_signatures(result: AnalysisResult) -> None:
    assert sym(result, "pricing.subtotal").signature == (
        "function subtotal(items: Array<[string, number]>): number"
    )
    arrow = sym(result, "pricing.applyDiscount")
    assert arrow.signature == "const applyDiscount = (cents: number, code?: string): number"
    assert arrow.contract == {
        "inputs": [
            {"name": "cents", "type": "number"},
            {"name": "code", "type": "string", "required": False},
        ],
        "outputs": [{"type": "number"}],
    }
    assert sym(result, "types.PriceRule").kind is NodeKind.class_
    assert sym(result, "cart.Cart.add").contract == {
        "inputs": [
            {"name": "sku", "type": "string"},
            {"name": "cents", "type": "number"},
            {"name": "qty", "required": False},
        ],
        "errors": [{"type": "CartError"}],
    }
    assert sym(result, "util.log.warn").contract == {
        "inputs": [{"name": "message"}, {"name": "...rest", "required": False}]
    }


def test_visibility(result: AnalysisResult) -> None:
    assert not sym(result, "pricing.roundCents").emit  # not exported
    assert not sym(result, "cart.Cart.debug").emit  # #private
    assert sym(result, "cart.Cart.total").emit


def test_calls_resolve_through_aliases_namespaces_and_js_extension(result: AnalysisResult) -> None:
    assert edges(result, EdgeType.CALLS) == {
        ("cart.Cart.add", "cart.CartError"),  # new X()
        ("cart.Cart.add", "util.log.info"),  # import * as log from "./util/log.js"
        ("cart.Cart.total", "pricing.subtotal"),  # "@/pricing" via tsconfig paths
        ("cart.Cart.total", "pricing.applyDiscount"),
        ("pricing.HalfOff.apply", "pricing.applyDiscount"),
    }


def test_heritage_and_reexported_test_targets(result: AnalysisResult) -> None:
    assert edges(result, EdgeType.IMPLEMENTS) == {("pricing.HalfOff", "types.PriceRule")}
    # Imported through index.ts: `export *` and `export { applyDiscount as discount }`.
    assert edges(result, EdgeType.VERIFIES) == {
        ("cart-test", "cart.Cart"),
        ("cart-test", "pricing.applyDiscount"),
    }


def test_export_clause_and_default_export(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_text(
        "function helper() {}\nclass Thing { run() { helper(); } }\n"
        "export { helper as h };\nexport default Thing;\n"
    )
    (tmp_path / "b.ts").write_text(
        'import T, { h } from "./a";\nexport function go() { h(); new T(); }\n'
    )
    result = analyze_repo(tmp_path)
    assert sym(result, "a.helper").emit
    assert sym(result, "a.Thing").emit
    assert sym(result, "a.Thing.run").emit
    assert edges(result, EdgeType.CALLS) == {
        ("a.Thing.run", "a.helper"),
        ("b.go", "a.helper"),
        ("b.go", "a.Thing"),
    }


def test_unparseable_tsconfig_is_a_warning(tmp_path: Path) -> None:
    (tmp_path / "tsconfig.json").write_text("{ nope")
    (tmp_path / "a.ts").write_text("export const x = 1;\n")
    result = analyze_repo(tmp_path)
    assert len(result.warnings) == 1
    assert result.warnings[0].startswith("tsconfig.json: could not parse")
