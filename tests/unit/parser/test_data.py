from __future__ import annotations

from pathlib import Path

import pytest

from intensify.parser import AnalysisResult, analyze_repo
from intensify.parser.data import classify, sql_tables
from intensify.spec.models import Confidence, EdgeType
from tests.conftest import FIXTURE_REPOS


@pytest.fixture(scope="module")
def result() -> AnalysisResult:
    return analyze_repo(FIXTURE_REPOS / "data_shop")


def data_edges(result: AnalysisResult) -> set[tuple[str, str, str]]:
    return {
        (d.type.value, d.source, d.target)
        for d in result.dependencies
        if d.type in {EdgeType.READS_FROM, EdgeType.WRITES_TO}
    }


@pytest.mark.parametrize(
    ("sql", "reads", "writes"),
    [
        (
            "SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id",
            ["orders", "customers"],
            [],
        ),
        ("SELECT * FROM a, b AS bb, c WHERE x = 1", ["a", "b", "c"], []),
        ('insert into public."order_lines" (a) values (?)', [], ["order_lines"]),
        (
            "INSERT INTO t (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = excluded.a",
            [],
            ["t"],
        ),
        (
            "UPDATE orders SET s = $1 FROM payments p WHERE p.id = orders.id",
            ["payments"],
            ["orders"],
        ),
        ("DELETE FROM sessions WHERE expires < now()", [], ["sessions"]),
        ("WITH recent AS (SELECT * FROM orders) SELECT * FROM recent", ["orders"], []),
        ("MERGE INTO stock s USING deliveries d ON (s.id = d.id)", ["deliveries"], ["stock"]),
        ("INSERT OR REPLACE INTO kv VALUES (?, ?)", [], ["kv"]),
        ("TRUNCATE TABLE audit_log", [], ["audit_log"]),
        ("SELECT * FROM orders WHERE id = 1 FOR UPDATE", ["orders"], []),
        ("select extract(year from created) from orders", ["orders"], []),
        ("select o from Order o order by o.id", ["Order"], []),  # JPQL
        ("SELECT name FROM t WHERE note = 'from x'", ["t"], []),
    ],
)
def test_sql_tables(sql: str, reads: list[str], writes: list[str]) -> None:
    tables = sql_tables(sql)
    assert tables is not None
    assert (tables.reads, tables.writes) == (reads, writes)


@pytest.mark.parametrize("prose", ["Update failed", "SELECT 1", "Delete this?", "select an item"])
def test_prose_is_not_sql(prose: str) -> None:
    assert sql_tables(prose) is None


def test_classify() -> None:
    assert classify("findByEmail") is False
    assert classify("get_or_create") is True
    assert classify("bulk_create") is True
    assert classify("save") is True
    assert classify("getRepository") is None
    assert classify("serialize") is None


def test_edges_across_languages(result: AnalysisResult) -> None:
    assert data_edges(result) == {
        # Python: SQL constants and literals, SQLAlchemy, Django.
        ("READS_FROM", "shop.repo", "shop.models.Order"),  # module-level SQL constant
        ("READS_FROM", "shop.repo", "shop.models.Customer"),
        ("READS_FROM", "shop.repo.open_orders", "shop.models.Order"),  # select(Order)
        ("WRITES_TO", "shop.repo.place", "shop.models.Order"),  # session.add(Order(...))
        ("WRITES_TO", "shop.repo.purge", "shop.models.Order"),  # delete(Order)
        ("WRITES_TO", "shop.repo.rename", "shop.models.Customer"),  # implicitly joined strings
        ("READS_FROM", "shop.repo.count", "shop.models.Customer"),  # session.get(Customer, 1)
        ("READS_FROM", "shop.orders.views.invoices", "shop.orders.models.Invoice"),
        ("WRITES_TO", "shop.orders.views.void", "shop.orders.models.Invoice"),  # .filter().delete()
        ("READS_FROM", "shop.orders.views.refund_total", "shop.orders.models.Refund"),  # db_table
        # Java and Kotlin: JPA, JPQL, JDBC, Spring Data.
        ("READS_FROM", "com.shop.Catalog.Catalog.byId", "com.shop.Product.Product"),
        ("WRITES_TO", "com.shop.Catalog.Catalog.add", "com.shop.Product.Product"),
        ("READS_FROM", "com.shop.Catalog.Catalog.expensive", "com.shop.Product.Product"),
        ("WRITES_TO", "com.shop.Catalog.Catalog.reprice", "com.shop.Product.Product"),  # @Table
        ("READS_FROM", "com.shop.Catalog.Catalog.reprice", "com.shop.Stock.StockLevel"),  # default
        ("READS_FROM", "com.shop.Stock.Warehouse.levels", "com.shop.Stock.StockLevel"),
        ("WRITES_TO", "com.shop.Stock.Warehouse.receive", "com.shop.Stock.StockLevel"),
        # TypeScript: TypeORM repository.
        ("READS_FROM", "ts.src.user.Users.active", "ts.src.user.User"),
        ("WRITES_TO", "ts.src.user.Users.remove", "ts.src.user.User"),
        # Go: database/sql and GORM.
        ("WRITES_TO", "go.store.store", "go.store.store.Ledger"),  # TableName()
        ("WRITES_TO", "go.store.store.Record", "go.store.store.Payment"),  # g.Create(&Payment{})
        ("READS_FROM", "go.store.store.Total", "go.store.store.Payment"),  # gorm.Model default
    }
    assert {d.confidence for d in result.dependencies if d.type is EdgeType.READS_FROM} == {
        Confidence.medium
    }


def test_no_models_no_edges(tmp_path: Path) -> None:
    (tmp_path / "q.py").write_text('def f(db):\n    return db.execute("SELECT * FROM orders")\n')
    assert data_edges(analyze_repo(tmp_path)) == set()


def test_tests_make_no_edges_but_prose_naming_a_table_can(tmp_path: Path) -> None:
    (tmp_path / "models.py").write_text("class Order:\n    __tablename__ = 'orders'\n")
    (tmp_path / "test_orders.py").write_text(
        "def test_it(db):\n    db.execute('DELETE FROM orders')\n"
    )
    (tmp_path / "ui.py").write_text("def hint():\n    return 'Select an order from orders'\n")
    result = analyze_repo(tmp_path)
    # Only the UI hint looks like SQL; the test's access is not architecture.
    assert data_edges(result) == {("READS_FROM", "ui.hint", "models.Order")}


def test_ambiguous_default_table_names_are_dropped(tmp_path: Path) -> None:
    for pkg in ("a", "b"):
        (tmp_path / pkg).mkdir()
        (tmp_path / pkg / "Item.java").write_text(
            f"package {pkg};\n@jakarta.persistence.Entity\npublic class Item {{}}\n"
        )
    (tmp_path / "Q.java").write_text(
        "public class Q { public void f(java.sql.Statement s) throws Exception {\n"
        '  s.execute("DELETE FROM items"); } }\n'
    )
    assert data_edges(analyze_repo(tmp_path)) == set()
