"""Data access: which code reads and writes which tables, as ``READS_FROM`` / ``WRITES_TO`` edges.

The spec has no node kind for tables, so an edge goes to the model class that owns the table:
an SQLAlchemy, SQLModel or Django model, a JPA ``@Entity``, a TypeORM ``@Entity`` or a GORM
struct. Three things are detected, after the language analyzers have run:

* **Models**, and the table names each one owns (explicit, or the ORM's default naming).
* **SQL in string literals** (including JPQL), split into tables read and written, and matched
  to models by table name.
* **ORM calls anchored on a model**: ``Order.objects.filter()``, ``select(Order)``,
  ``em.find(Order.class, id)``, ``getRepository(Order).save()``, calls on a variable typed as a
  Spring Data repository or ``Repository<Order>``, and ``session.add(Order(...))``. The method
  names in the call chain decide between reading and writing.

Every edge is ``confidence: medium``: this is pattern matching, not type checking. Tables with no
model in the analyzed code, and calls from tests, produce no edges.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from tree_sitter import Language, Node, Parser

from intensify.parser.base import AnalysisResult, Dependency, Symbol, innermost
from intensify.parser.treesitter import text, walk
from intensify.spec.models import Confidence, EdgeType, NodeKind

# -- SQL -----------------------------------------------------------------------------------

_SQL_START = re.compile(
    r"^\s*\(?\s*(select|with|insert|update|delete|merge|replace|truncate)\b", re.IGNORECASE
)
_SQL_SHAPE = re.compile(
    r"\bselect\b[\s\S]+?\bfrom\b|\binsert\s+(?:or\s+\w+\s+|ignore\s+)?into\b"
    r"|\bupdate\s+\S+\s+set\b|\bdelete\s+from\b|\bmerge\s+into\b|\breplace\s+into\b"
    r"|\btruncate\s+(?:table\s+)?\w",
    re.IGNORECASE,
)
_SQL_TOKEN = re.compile(
    r'`[^`]*`|"[^"]*"|\[[^\]]*\]|[A-Za-z_][\w$]*(?:\.(?:[A-Za-z_][\w$]*|"[^"]*"|`[^`]*`))*|\S'
)
_SQL_STRIP = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*[\s\S]*?\*/")
_KEYWORD_TEXT = """
    select from where join inner outer left right full cross natural on using as and or not in
    is null group by having limit offset union all distinct insert into values update set
    delete merge when then else end case with recursive returning lateral only table truncate
    replace ignore exists between like ilike for share nowait skip locked do nothing conflict
    duplicate key window over partition
"""
_KEYWORDS = frozenset(_KEYWORD_TEXT.split())
"""Never table names. ``order`` is missing on purpose: JPQL entities are often called Order."""
_NOT_TABLE_FUNCTIONS = frozenset({"extract", "substring", "trim", "position", "overlay"})
"""Functions whose arguments use ``FROM`` without naming a table."""


@dataclass
class SqlTables:
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)


def sql_tables(source: str) -> SqlTables | None:
    """Tables an SQL (or JPQL) statement reads and writes, or None if it isn't SQL."""
    if not _SQL_START.match(source) or not _SQL_SHAPE.search(source):
        return None
    tokens = _SQL_TOKEN.findall(_SQL_STRIP.sub("''", source))
    lowered = [t.lower() for t in tokens]
    ctes = {
        _unquote(tokens[i])
        for i in range(len(tokens) - 2)
        if lowered[i + 1] == "as" and tokens[i + 2] == "(" and lowered[i] not in _KEYWORDS
    }
    out = SqlTables()

    def name_at(i: int) -> str | None:
        if i >= len(tokens) or tokens[i] == "(" or lowered[i] in _KEYWORDS:
            return None
        name = _unquote(tokens[i])
        return name if name and (name[0].isalpha() or name[0] == "_") else None

    def add(target: list[str], i: int) -> None:
        if (name := name_at(i)) is not None and name not in ctes and name not in target:
            target.append(name)

    calls: list[str] = []
    """Function names of the open parentheses."""
    for i, tok in enumerate(lowered):
        prev = lowered[i - 1] if i else ""
        if tok == "(":
            calls.append(prev)
        elif tok == ")" and calls:
            calls.pop()
        if tok == "from" and calls and calls[-1] in _NOT_TABLE_FUNCTIONS:
            continue
        if tok == "from" and prev not in {"delete", "distinct"}:
            add(out.reads, i + 1)
            _from_list(tokens, lowered, i + 2, lambda j: add(out.reads, j))
        elif tok == "join" or (tok == "using" and "merge" in lowered[:i]):
            add(out.reads, i + 1)
        elif tok == "into" and prev in {"insert", "replace", "ignore", "merge"} | _OR_ACTIONS:
            add(out.writes, i + 1)
        elif tok == "update" and prev not in {"do", "for", "on", "key"}:
            j = i + 1
            while j < len(lowered) and lowered[j] in {"only", "low_priority", "ignore"}:
                j += 1
            add(out.writes, j)
        elif tok == "from" and prev == "delete":
            add(out.writes, i + 1)
        elif tok == "truncate":
            add(out.writes, i + 2 if i + 1 < len(lowered) and lowered[i + 1] == "table" else i + 1)
    return out


_OR_ACTIONS = frozenset({"rollback", "abort", "fail", "replace"})
"""SQLite's ``INSERT OR REPLACE INTO``."""


def _from_list(tokens: list[str], lowered: list[str], i: int, add: Callable[[int], None]) -> None:
    """``FROM a x, b AS y``: the tables after the first, until the next keyword."""
    while i < len(tokens):
        if lowered[i] == "as":
            i += 1
        if i < len(tokens) and lowered[i] not in _KEYWORDS and tokens[i] not in {",", "("}:
            i += 1  # alias
        if i < len(tokens) and tokens[i] == ",":
            add(i + 1)
            i += 2
        else:
            return


def _unquote(token: str) -> str:
    last = token.rsplit(".", 1)[-1] if not token.startswith(("`", '"', "[")) else token
    return last.strip('`"[]')


# -- method names --------------------------------------------------------------------------

_WRITES = (
    "save",
    "insert",
    "update",
    "delete",
    "remove",
    "create",
    "persist",
    "merge",
    "upsert",
    "bulkcreate",
    "bulkupdate",
    "bulkinsert",
    "add",
    "put",
    "destroy",
    "truncate",
    "clear",
    "increment",
    "decrement",
    "softdelete",
    "softremove",
    "restore",
    "getorcreate",
    "updateorcreate",
    "store",
)
_READS = (
    "find",
    "get",
    "read",
    "query",
    "count",
    "exists",
    "select",
    "filter",
    "all",
    "first",
    "last",
    "fetch",
    "search",
    "stream",
    "load",
    "list",
    "values",
    "aggregate",
    "exclude",
    "latest",
    "earliest",
    "inbulk",
    "scalar",
    "one",
    "iterate",
    "paginate",
)
_NEUTRAL = frozenset(
    {
        "getrepository",
        "getmongorepository",
        "gettreerepository",
        "getcustomrepository",
        "injectrepository",
        "withrepository",
        "createquerybuilder",
    }
)
"""Calls that only obtain a handle on the model: the rest of the chain decides."""


def classify(method: str) -> bool | None:
    """True for a write, False for a read, None if the name says neither."""
    name = method.replace("_", "").lower()
    if name in _NEUTRAL:
        return None
    if name.startswith(_WRITES):
        return True
    if name.startswith(_READS):
        return False
    return None


# -- languages -----------------------------------------------------------------------------

_STRINGS = frozenset(
    {
        "string",
        "template_string",
        "string_literal",
        "multiline_string_literal",
        "raw_string_literal",
        "interpreted_string_literal",
        "text_block",
    }
)
_CONTENT = frozenset(
    {
        "string_content",
        "string_fragment",
        "multiline_string_fragment",
        "raw_string_literal_content",
        "interpreted_string_literal_content",
        "escape_sequence",
    }
)
_CONCAT = frozenset({"binary_expression", "binary_operator", "concatenated_string"})
_CALLS = frozenset(
    {"call", "call_expression", "method_invocation", "new_expression", "object_creation_expression"}
)
_MEMBER = frozenset(
    {"attribute", "member_expression", "navigation_expression", "selector_expression"}
)
_PREFILTER = re.compile(
    rb"(?i)\b(select|insert|update|delete|merge|truncate)\b|objects|\.query\b|Repository"
    rb"|EntityManager|Entity|__tablename__|db_table|TableName|gorm|session|Model\b"
)


def _grammars() -> dict[str, Language]:
    from intensify.parser import go, java, kotlin, python, typescript  # noqa: PLC0415 - cycle

    return {
        "python": python.LANGUAGE,
        "java": java.LANGUAGE,
        "kotlin": kotlin.LANGUAGE,
        "go": go.LANGUAGE,
        **{ext: typescript._GRAMMARS[name] for ext, name in typescript._EXT_GRAMMAR.items()},
    }


# -- models --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    tables: frozenset[str]
    """Lower-cased table names (and entity names, for JPQL) the model owns."""
    explicit: bool


_PY_CLASS = re.compile(r"^class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:", re.MULTILINE)
_PY_TABLENAME = re.compile(r"""__tablename__\s*=\s*['"]([^'"]+)['"]""")
_PY_DB_TABLE = re.compile(r"""db_table\s*=\s*['"]([^'"]+)['"]""")
_JPA_ENTITY = re.compile(
    r"@(?:jakarta\.persistence\.|javax\.persistence\.)?Entity\b(?:\s*\(([^)]*)\))?"
    r"(?P<between>[\s\S]{0,400}?)\b(?:class|record)\s+(?P<name>\w+)"
)
_JPA_TABLE = re.compile(r"""@(?:\w+\.)*Table\s*\(\s*(?:name\s*=\s*)?"([^"]+)\"""")
_JPA_NAME = re.compile(r"""name\s*=\s*"([^"]+)\"""")
_TS_ENTITY = re.compile(
    r"""@Entity\s*\(\s*(?:['"](?P<table>[^'"]+)['"]|\{[^}]*?name\s*:\s*['"](?P<named>[^'"]+)['"][^}]*\})?"""
    r"""[^)]*\)[\s\S]{0,400}?\bclass\s+(?P<name>\w+)"""
)
_GO_GORM_MODEL = re.compile(r"type\s+(\w+)\s+struct\s*\{[^}]*\bgorm\.Model\b")
_GO_TABLE_NAME = re.compile(
    r"""func\s*\(\s*(?:\w+\s+)?\*?(\w+)\s*\)\s*TableName\s*\(\s*\)\s*string\s*\{\s*return\s*"([^"]+)\""""
)


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def _default_tables(name: str) -> frozenset[str]:
    """Table names an ORM may derive from a class name: snake case and lower case, plural."""
    names = {_snake(name), name.lower()}
    plural = {n[:-1] + "ies" if n.endswith("y") else n + "s" for n in names}
    return frozenset(names | plural)


def _python_models(source: str, path: str) -> Iterator[tuple[str, frozenset[str], bool]]:
    matches = list(_PY_CLASS.finditer(source))
    for i, match in enumerate(matches):
        body = source[match.end() : matches[i + 1].start() if i + 1 < len(matches) else None]
        bases = match.group(2) or ""
        name = match.group(1)
        if table := _PY_TABLENAME.search(body):
            yield name, frozenset({table.group(1).lower()}), True
        elif re.search(r"\btable\s*=\s*True\b", bases):  # SQLModel
            yield name, frozenset({name.lower()}), False
        elif re.search(r"(?:^|[\s,(])(?:models\.)?Model\b", bases) and "django" in source:
            if table := _PY_DB_TABLE.search(body):
                yield name, frozenset({table.group(1).lower()}), True
            else:
                parts = PurePosixPath(path).parent.parts
                app = parts[-2] if parts and parts[-1] == "models" and len(parts) > 1 else ""
                app = app or (parts[-1] if parts else "")
                yield name, frozenset({f"{app}_{name.lower()}", name.lower()}), False


def _jvm_models(source: str) -> Iterator[tuple[str, frozenset[str], bool]]:
    for match in _JPA_ENTITY.finditer(source):
        name = match.group("name")
        names = {name.lower()}
        if match.group(1) and (entity := _JPA_NAME.search(match.group(1))):
            names.add(entity.group(1).lower())  # JPQL uses the entity name
        if table := _JPA_TABLE.search(match.group("between")):
            yield name, frozenset({table.group(1).lower(), *names}), True
        else:
            yield name, frozenset(names | _default_tables(name)), False


def _ts_models(source: str) -> Iterator[tuple[str, frozenset[str], bool]]:
    for match in _TS_ENTITY.finditer(source):
        name = match.group("name")
        table = match.group("table") or match.group("named")
        if table:
            yield name, frozenset({table.lower(), name.lower()}), True
        else:
            yield name, frozenset(_default_tables(name)), False


def _go_models(source: str) -> Iterator[tuple[str, frozenset[str], bool]]:
    explicit = {m.group(1): m.group(2).lower() for m in _GO_TABLE_NAME.finditer(source)}
    for name, table in explicit.items():
        yield name, frozenset({table}), True
    for match in _GO_GORM_MODEL.finditer(source):
        if match.group(1) not in explicit:
            yield match.group(1), _default_tables(match.group(1)), False


def _models_in(language: str, source: str, path: str) -> Iterator[tuple[str, frozenset[str], bool]]:
    if language == "python":
        yield from _python_models(source, path)
    elif language in {"java", "kotlin"}:
        yield from _jvm_models(source)
    elif language in {"typescript", "javascript"}:
        yield from _ts_models(source)
    elif language == "go":
        yield from _go_models(source)


# -- repositories --------------------------------------------------------------------------

_SPRING_REPO = re.compile(
    r"\binterface\s+(?P<name>\w+)\s*(?:<[^>{]*>)?\s*(?:extends|:)\s*(?P<bases>[^{]*)"
)
_REPO_BASE = re.compile(
    r"\b(?:Jpa|Crud|PagingAndSorting|ListCrud|ListPagingAndSorting|Mongo|ReactiveCrud"
    r"|ReactiveMongo|CoroutineCrud|R2dbc|Elasticsearch|Neo4j)?Repository\s*<\s*(\w+)"
)
_GENERIC_REPOS = frozenset(
    {"Repository", "MongoRepository", "TreeRepository", "EntityRepository", "Repo"}
)
_DECLS = (
    # Java: ``OrderRepository orders`` / ``Repository<Order> repo``
    re.compile(r"\b(?P<type>[A-Z]\w*)(?:\s*<\s*(?P<arg>\w+)[^<>]*>)?\s+(?P<name>\w+)\s*[;=,)]"),
    # Kotlin and TypeScript: ``orders: OrderRepository`` / ``repo: Repository<Order>``
    re.compile(r"\b(?P<name>\w+)\s*:\s*(?P<type>[A-Z]\w*)(?:\s*<\s*(?P<arg>\w+)[^<>]*>)?"),
)
_REPO_ASSIGN = re.compile(
    r"\b(?P<name>\w+)\s*=\s*(?:await\s+)?[\w.]*get(?:Mongo|Tree)?Repository\s*\(\s*(?P<arg>\w+)"
)


def _repositories(source: str) -> dict[str, str]:
    """Spring Data repository interface name -> entity name."""
    out: dict[str, str] = {}
    for match in _SPRING_REPO.finditer(source):
        if base := _REPO_BASE.search(match.group("bases")):
            out[match.group("name")] = base.group(1)
    return out


def _typed_names(source: str, repositories: dict[str, str]) -> dict[str, str]:
    """Variable, field or parameter name -> the model its repository serves."""
    out: dict[str, str] = {}
    for pattern in _DECLS:
        for match in pattern.finditer(source):
            kind, arg = match.group("type"), match.group("arg")
            if kind in repositories:
                out[match.group("name")] = repositories[kind]
            elif kind in _GENERIC_REPOS and arg:
                out[match.group("name")] = arg
    for match in _REPO_ASSIGN.finditer(source):
        out[match.group("name")] = match.group("arg")
    return out


# -- detection -----------------------------------------------------------------------------


@dataclass
class _Access:
    path: str
    line: int
    write: bool
    table: str | None = None
    model: str | None = None


class _File:
    def __init__(self, path: str, language: str, source: bytes, tree: Node) -> None:
        self.path = path
        self.language = language
        self.source = source
        self.tree = tree
        self.text = source.decode("utf-8", errors="replace")


def detect(root: Path, result: AnalysisResult) -> None:
    """Add medium-confidence ``READS_FROM`` and ``WRITES_TO`` edges to ``result``."""
    grammars = _grammars()
    parsers: dict[str, Parser] = {}
    files: list[_File] = []
    for path, language in sorted(result.files.items()):
        key = (
            language if language not in {"typescript", "javascript"} else PurePosixPath(path).suffix
        )
        grammar = grammars.get(key)
        if grammar is None:
            continue
        source = (root / path).read_bytes()
        if not _PREFILTER.search(source):
            continue
        parser = parsers.setdefault(key, Parser(grammar))
        files.append(_File(path, language, source, parser.parse(source).root_node))
    if not files:
        return

    symbols_by_path: dict[str, list[Symbol]] = defaultdict(list)
    for sym in result.symbols:
        symbols_by_path[sym.path].append(sym)

    models = _find_models(files, symbols_by_path)
    if not models:
        return
    by_name: dict[str, list[Model]] = defaultdict(list)
    for model in models:
        by_name[model.name].append(model)
    repositories: dict[str, str] = {}
    for f in files:
        if f.language in {"java", "kotlin"}:
            repositories.update(_repositories(f.text))

    accesses: list[_Access] = []
    for f in files:
        typed = _typed_names(f.text, repositories) if f.language != "python" else {}
        accesses.extend(_scan(f, set(by_name), typed))

    tables = _table_index(models)
    seen: set[Dependency] = set()
    for access in accesses:
        source_sym = innermost(symbols_by_path[access.path], access.line)
        if source_sym is None or source_sym.is_test:
            continue
        if access.model is not None:
            candidates = by_name.get(access.model, [])
            target = candidates[0].id if len(candidates) == 1 else _nearest(candidates, access.path)
        else:
            target = tables.get((access.table or "").lower())
        if target is None or target == source_sym.id:
            continue
        edge = EdgeType.WRITES_TO if access.write else EdgeType.READS_FROM
        dep = Dependency(source_sym.id, target, edge, Confidence.medium)
        if dep not in seen:
            seen.add(dep)
            result.dependencies.append(dep)


def _find_models(files: list[_File], symbols_by_path: dict[str, list[Symbol]]) -> list[Model]:
    out: list[Model] = []
    for f in files:
        classes = {s.name: s for s in symbols_by_path[f.path] if s.kind is NodeKind.class_}
        for name, tables, explicit in _models_in(f.language, f.text, f.path):
            if (sym := classes.get(name)) is not None:
                out.append(Model(sym.id, name, tables, explicit))
    return out


def _table_index(models: list[Model]) -> dict[str, str]:
    """Lower-cased table name -> model ID. Explicit names win; other clashes are dropped."""
    index: dict[str, str] = {}
    explicit: dict[str, str] = {}
    clashes: set[str] = set()
    for model in models:
        for table in model.tables:
            if model.explicit:
                explicit.setdefault(table, model.id)
            elif table in index and index[table] != model.id:
                clashes.add(table)
            else:
                index.setdefault(table, model.id)
    for table in clashes:
        index.pop(table, None)
    index.update(explicit)
    return index


def _nearest(candidates: list[Model], path: str) -> str | None:
    """With several models of one name, the one whose ID shares most with the file's path."""
    if not candidates:
        return None
    parts = set(PurePosixPath(path).with_suffix("").parts)
    scores = sorted(((len(parts & set(m.id.split("."))), m.id) for m in candidates), reverse=True)
    if len(scores) > 1 and scores[0][0] == scores[1][0]:
        return None  # equally close: ambiguous
    return scores[0][1]


def _scan(f: _File, model_names: set[str], typed: dict[str, str]) -> Iterator[_Access]:
    consumed: set[int] = set()
    for node in walk(f.tree):
        if node.type in _STRINGS and node.id not in consumed:
            top = node
            while top.parent is not None and top.parent.type in _CONCAT and _is_concat(top.parent):
                top = top.parent
            for inner in [top, *walk(top)]:
                consumed.add(inner.id)
            sql = sql_tables(_string_value(top))
            if sql is not None:
                line = top.start_point.row + 1
                for table in sql.reads:
                    yield _Access(f.path, line, False, table=table)
                for table in sql.writes:
                    yield _Access(f.path, line, True, table=table)
        elif node.type in _CALLS:
            yield from _orm_call(f, node, model_names, typed)
        elif node.type in _MEMBER:
            yield from _manager(f, node, model_names)


def _is_concat(node: Node) -> bool:
    if node.type == "concatenated_string":
        return True
    return any(not c.is_named and c.type == "+" for c in node.children)


def _string_value(node: Node) -> str:
    """The text of a string literal, or of ``+``-joined literals; interpolations become ``?``."""
    if node.type in _STRINGS:
        parts = []
        for child in node.children:
            if child.type in _CONTENT:
                parts.append(text(child))
            elif child.type in {"interpolation", "template_substitution"}:
                parts.append("?")
        return "".join(parts)
    if node.type in _CONCAT:
        return " ".join(_string_value(c) for c in node.named_children)
    return "?"


def _call_parts(node: Node) -> tuple[Node | None, str, list[Node]] | None:
    """Receiver (None if unqualified), method name and arguments of a call."""
    if node.type == "method_invocation":
        arg_list = node.child_by_field_name("arguments")
        return (
            node.child_by_field_name("object"),
            text(node.child_by_field_name("name")),
            list(arg_list.named_children) if arg_list else [],
        )
    if node.type in {"new_expression", "object_creation_expression"}:
        return None
    callee = node.child_by_field_name("function") or (
        node.named_children[0] if node.named_children else None
    )
    args_node = node.child_by_field_name("arguments") or next(
        (c for c in node.named_children if c.type == "value_arguments"), None
    )
    args: list[Node] = [
        c.named_children[0] if c.type == "value_argument" and c.named_children else c
        for c in (args_node.named_children if args_node else [])
    ]
    if callee is None:
        return None
    if callee.type == "identifier":
        return None, text(callee), args
    if callee.type in _MEMBER and len(callee.named_children) >= 2:  # noqa: PLR2004
        return callee.named_children[0], text(callee.named_children[-1]), args
    return None


def _model_ref(node: Node, model_names: set[str]) -> tuple[str, bool] | None:
    """The model an argument names, and whether it constructs one (``Order(...)``)."""
    while node.type in {"unary_expression", "parenthesized_expression"} and node.named_children:
        node = node.named_children[-1]
    raw = text(node)
    if node.type in {"identifier", "type_identifier"} and raw in model_names:
        return raw, False
    # Order.class, Order::class, Order::class.java
    match = re.fullmatch(r"(\w+)(?:\.class|::class(?:\.java)?)", raw)
    if match and match.group(1) in model_names:
        return match.group(1), False
    if node.type == "composite_literal":  # Go: &Order{} marks a model in GORM
        name = text(node.child_by_field_name("type"))
        return (name, False) if name in model_names else None
    if node.type in {"new_expression", "object_creation_expression"}:
        name = text(node.child_by_field_name("constructor") or node.child_by_field_name("type"))
        return (name, True) if name in model_names else None
    if node.type in {"call", "call_expression"} and node.named_children:
        name = text(node.child_by_field_name("function") or node.named_children[0])
        return (name, True) if name in model_names else None
    return None


def _chain(node: Node) -> list[str]:
    """Method names called on the value of ``node``, outward: ``x.filter().delete()``."""
    out: list[str] = []
    current = node
    while (parent := current.parent) is not None:
        if parent.type == "method_invocation" and parent.child_by_field_name("object") == current:
            out.append(text(parent.child_by_field_name("name")))
            current = parent
            continue
        if parent.type in _MEMBER and parent.named_children and parent.named_children[0] == current:
            call = parent.parent
            name = text(parent.named_children[-1])
            if call is not None and call.type in _CALLS:
                out.append(name)
                current = call
                continue
            out.append(name)  # a property, such as Django's ``objects``
            current = parent
            continue
        break
    return out


def _decide(methods: list[str]) -> bool | None:
    kinds = [k for m in methods if (k := classify(m)) is not None]
    if not kinds:
        return None
    return any(kinds)


def _orm_call(
    f: _File, node: Node, model_names: set[str], typed: dict[str, str]
) -> Iterator[_Access]:
    parts = _call_parts(node)
    if parts is None:
        return
    receiver, method, args = parts
    line = node.start_point.row + 1
    # A call on a repository-typed variable: ``orders.save(o)``, ``this.repo.find()``.
    if receiver is not None:
        name = text(receiver).removeprefix("this.").removeprefix("self.")
        if name in typed and (write := _decide([method, *_chain(node)])) is not None:
            yield _Access(f.path, line, write, model=typed[name])
            return
    # A call that takes the model as its first argument: ``select(Order)``, ``em.find(...)``.
    if args and (ref := _model_ref(args[0], model_names)) is not None:
        model, constructs = ref
        write = _decide([method, *_chain(node)])
        if write is not None and (write or not constructs):
            yield _Access(f.path, line, write, model=model)


def _manager(f: _File, node: Node, model_names: set[str]) -> Iterator[_Access]:
    """Django's ``Order.objects...`` and Flask-SQLAlchemy's ``Order.query...``."""
    if f.language != "python" or len(node.named_children) < 2:  # noqa: PLR2004
        return
    owner, attr = node.named_children[0], text(node.named_children[-1])
    if attr not in {"objects", "query"} or text(owner) not in model_names:
        return
    if owner.type != "identifier":
        return
    write = _decide(_chain(node))
    yield _Access(f.path, node.start_point.row + 1, bool(write), model=text(owner))
