"""Python analyzer: modules, classes, functions, calls and inheritance via tree-sitter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from intensify.parser import ids
from intensify.parser.base import AnalysisResult, AnalyzeOptions, Dependency, Symbol
from intensify.parser.treesitter import fingerprint, header, line_count, name_point, text, walk
from intensify.spec.models import EdgeType, NodeKind

LANGUAGE = Language(tree_sitter_python.language())

_IDENTIFIERS = frozenset({"identifier"})
_DEFINITIONS = frozenset({"function_definition", "class_definition", "decorated_definition"})
_BRANCHES = frozenset(
    {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "conditional_expression",
        "boolean_operator",
        "case_clause",
        "for_in_clause",
        "if_clause",
    }
)
_MAX_INHERITANCE_DEPTH = 5
_PUBLIC_DUNDERS = frozenset({"__init__", "__call__", "__main__"})


def is_private(name: str) -> bool:
    if name.startswith("__") and name.endswith("__"):
        return name not in _PUBLIC_DUNDERS
    return name.startswith("_")


def is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name
    in_test_dir = any(part in {"tests", "test"} for part in pure.parent.parts)
    return (
        in_test_dir
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name in {"tests.py", "test.py", "conftest.py"}  # Django's per-app tests, pytest
    )


@dataclass
class _Module:
    path: str
    id: str
    import_name: str
    is_package: bool
    is_test: bool
    root: Node
    symbols: list[Symbol] = field(default_factory=list)
    top: dict[str, str] = field(default_factory=dict)
    """Top-level name -> symbol ID."""
    imports: dict[str, str] = field(default_factory=dict)
    """Bound name -> fully qualified import name."""
    star_imports: list[str] = field(default_factory=list)
    calls: list[tuple[str, list[str], str | None, tuple[int, int]]] = field(default_factory=list)
    """Scope, callee chain, class context and the position of the called name."""
    sites: list[tuple[str, tuple[int, int]]] = field(default_factory=list)
    """Scope and position of calls with no static chain to resolve (``a.b().c()``)."""
    """(scope symbol ID, dotted callee chain, enclosing class ID)."""
    bases: list[tuple[str, list[str]]] = field(default_factory=list)
    """(class ID, base class chain)."""

    @property
    def package(self) -> list[str]:
        parts = self.import_name.split(".")
        return parts if self.is_package else parts[:-1]


class PythonAnalyzer:
    languages: tuple[str, ...] = ("python",)
    extensions: tuple[str, ...] = (".py",)

    def __init__(self) -> None:
        self._parser = Parser(LANGUAGE)
        self._allocator = ids.Allocator()

    def analyze(
        self, root: Path, paths: list[str], options: AnalyzeOptions, allocator: ids.Allocator
    ) -> AnalysisResult:
        self._allocator = allocator
        result = AnalysisResult()
        # Claim module IDs before any symbol IDs, so modules get the unsuffixed names.
        names = {path: _import_name(root, path) for path in paths}
        module_ids = {
            path: allocator.claim(ids.join(name) if in_package else ids.path_id(path))
            for path, (name, in_package) in names.items()
        }
        modules = []
        for path in paths:
            tree = self._parser.parse((root / path).read_bytes())
            if tree.root_node.has_error:
                result.warnings.append(f"{path}: syntax errors; results may be incomplete")
            modules.append(self._module(path, module_ids[path], names[path][0], tree.root_node))
            result.files[path] = "python"

        index: dict[str, str] = {}
        members: dict[str, dict[str, str]] = {}
        for module in modules:
            for sym in module.symbols:
                qual = module.import_name + (f".{sym.symbol}" if sym.symbol else "")
                index.setdefault(qual, sym.id)
                if sym.kind is NodeKind.function and sym.parent and "." in (sym.symbol or ""):
                    members.setdefault(sym.parent, {})[sym.name] = sym.id
            result.symbols.extend(module.symbols)

        resolver = _Resolver(index, members)
        test_ids = {s.id for m in modules if m.is_test for s in m.symbols}
        for module in modules:
            for class_id, chain in module.bases:
                resolver.bases.setdefault(class_id, [])
                if (target := resolver.resolve(module, chain, None)) and target != class_id:
                    resolver.bases[class_id].append(target)
                    result.dependencies.append(Dependency(class_id, target, EdgeType.IMPLEMENTS))
        for module in modules:
            for scope, point in module.sites:
                source = module.id if module.is_test else scope
                result.site(source, module.is_test, module.path, point)
            for scope, chain, class_ctx, point in module.calls:
                target = resolver.resolve(module, chain, class_ctx)
                if target is None:
                    source = module.id if module.is_test else scope
                    result.site(source, module.is_test, module.path, point)
                    continue
                if target in test_ids:
                    continue
                if module.is_test:
                    result.dependencies.append(Dependency(module.id, target, EdgeType.VERIFIES))
                else:
                    result.dependencies.append(Dependency(scope, target, EdgeType.CALLS))
        return result

    # -- per-file extraction -------------------------------------------------------------

    def _module(self, path: str, module_id: str, import_name: str, tree: Node) -> _Module:
        module = _Module(
            path=path,
            id=module_id,
            import_name=import_name,
            is_package=PurePosixPath(path).name == "__init__.py",
            is_test=is_test_path(path),
            root=tree,
        )
        module.symbols.append(
            Symbol(
                id=module_id,
                kind=NodeKind.module,
                language="python",
                path=path,
                symbol=None,
                parent=None,
                line=1,
                complexity={"loc": line_count(tree)},
                fingerprint=fingerprint(tree, _IDENTIFIERS),
                public=not any(is_private(p) for p in import_name.split(".")),
                is_test=module.is_test,
            )
        )
        self._imports(module)
        self._block(module, tree, parent=module.symbols[0], class_ctx=None)
        return module

    def _imports(self, module: _Module) -> None:
        for node in walk(module.root):
            if node.type == "import_statement":
                for name in node.children_by_field_name("name"):
                    if name.type == "aliased_import":
                        alias = text(name.child_by_field_name("alias"))
                        module.imports[alias] = text(name.child_by_field_name("name"))
                    else:
                        dotted = text(name)
                        module.imports[dotted.split(".")[0]] = dotted.split(".")[0]
            elif node.type == "import_from_statement":
                base = self._from_base(module, node.child_by_field_name("module_name"))
                if base is None:
                    continue
                if any(c.type == "wildcard_import" for c in node.children):
                    module.star_imports.append(base)
                for name in node.children_by_field_name("name"):
                    if name.type == "aliased_import":
                        bound = text(name.child_by_field_name("alias"))
                        target = text(name.child_by_field_name("name"))
                    else:
                        bound = target = text(name)
                    module.imports[bound] = f"{base}.{target}" if base else target

    def _from_base(self, module: _Module, node: Node | None) -> str | None:
        if node is None:
            return None
        if node.type != "relative_import":
            return text(node)
        prefix = next((c for c in node.children if c.type == "import_prefix"), None)
        level = len(text(prefix))
        package = module.package
        if level - 1 > len(package):
            return None
        parts = package[: len(package) - (level - 1)]
        dotted = next((c for c in node.children if c.type == "dotted_name"), None)
        if dotted is not None:
            parts = [*parts, *text(dotted).split(".")]
        return ".".join(parts)

    def _block(self, module: _Module, block: Node, parent: Symbol, class_ctx: str | None) -> None:
        """Extract definitions directly inside ``block``; attribute other calls to ``parent``."""
        for child in block.named_children:
            definition, decorators = child, []
            if child.type == "decorated_definition":
                definition = child.child_by_field_name("definition") or child
                decorators = [text(d).lstrip("@").split("(")[0] for d in child.children]
            if definition.type == "class_definition":
                self._class(module, definition, parent)
            elif definition.type == "function_definition":
                self._function(module, definition, parent, class_ctx, decorators)
            else:
                self._collect_calls(module, child, parent.id, class_ctx, prune_definitions=True)

    def _class(self, module: _Module, node: Node, parent: Symbol) -> None:
        name = text(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        sym = self._symbol(module, node, parent, name, NodeKind.class_, header(node, body))
        superclasses = node.child_by_field_name("superclasses")
        for base in superclasses.named_children if superclasses else []:
            if (chain := _chain(base)) and chain != ["object"]:
                module.bases.append((sym.id, chain))
        if body is not None:
            self._block(module, body, parent=sym, class_ctx=sym.id)

    def _function(
        self,
        module: _Module,
        node: Node,
        parent: Symbol,
        class_ctx: str | None,
        decorators: list[str],
    ) -> None:
        name = text(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        sym = self._symbol(module, node, parent, name, NodeKind.function, header(node, body))
        is_method = parent.kind is NodeKind.class_ and "staticmethod" not in decorators
        sym.contract = _contract(node, skip_first=is_method)
        branches = (
            sum(1 for n in walk(body, prune=_is_definition) if n.type in _BRANCHES) if body else 0
        )
        sym.complexity = {"cyclomatic": 1 + branches, "loc": line_count(node)}
        if body is not None:
            # Calls in nested functions and lambdas belong to the enclosing function.
            self._collect_calls(module, body, sym.id, class_ctx, prune_definitions=False)

    def _symbol(
        self, module: _Module, node: Node, parent: Symbol, name: str, kind: NodeKind, signature: str
    ) -> Symbol:
        qualified = f"{parent.symbol}.{name}" if parent.symbol else name
        sym = Symbol(
            id=self._allocator.claim(ids.join(module.id, qualified)),
            kind=kind,
            language="python",
            path=module.path,
            symbol=qualified,
            parent=parent.id,
            line=node.start_point.row + 1,
            signature=signature,
            complexity={"loc": line_count(node)},
            fingerprint=fingerprint(node, _IDENTIFIERS),
            public=not is_private(name),
            is_test=module.is_test,
        )
        module.symbols.append(sym)
        if parent.kind is NodeKind.module:
            module.top[name] = sym.id
        return sym

    def _collect_calls(
        self,
        module: _Module,
        node: Node,
        scope: str,
        class_ctx: str | None,
        *,
        prune_definitions: bool,
    ) -> None:
        if prune_definitions and _is_definition(node):
            return
        nodes = [node, *walk(node, prune=_is_definition if prune_definitions else None)]
        for n in nodes:
            if n.type != "call" or (callee := n.child_by_field_name("function")) is None:
                continue
            if chain := _chain(callee):
                module.calls.append((scope, chain, class_ctx, name_point(callee)))
            else:
                module.sites.append((scope, name_point(callee)))


class _Resolver:
    def __init__(self, index: dict[str, str], members: dict[str, dict[str, str]]) -> None:
        self.index = index
        self.members = members
        self.bases: dict[str, list[str]] = {}

    def member(self, class_id: str, name: str, depth: int = 0) -> str | None:
        if found := self.members.get(class_id, {}).get(name):
            return found
        if depth < _MAX_INHERITANCE_DEPTH:
            for base in self.bases.get(class_id, []):
                if found := self.member(base, name, depth + 1):
                    return found
        return None

    def resolve(self, module: _Module, chain: list[str], class_ctx: str | None) -> str | None:
        head, rest = chain[0], chain[1:]
        if head in {"self", "cls"}:
            return self.member(class_ctx, rest[0]) if class_ctx and len(rest) == 1 else None
        if head in module.top:
            target = module.top[head]
            if not rest:
                return target
            return self.member(target, rest[0]) if len(rest) == 1 else None
        if head in module.imports:
            return self.index.get(".".join([module.imports[head], *rest]))
        for star in module.star_imports:
            if found := self.index.get(".".join([star, *chain])):
                return found
        return None


def _is_definition(node: Node) -> bool:
    return node.type in _DEFINITIONS


def _chain(node: Node | None) -> list[str] | None:
    """``a.b.c`` as ``["a", "b", "c"]``, or None if it isn't a plain dotted name."""
    if node is None:
        return None
    if node.type == "identifier":
        return [text(node)]
    if node.type == "attribute":
        obj = _chain(node.child_by_field_name("object"))
        attr = node.child_by_field_name("attribute")
        return [*obj, text(attr)] if obj is not None and attr is not None else None
    return None


def _contract(node: Node, *, skip_first: bool) -> dict[str, Any] | None:
    inputs: list[dict[str, Any]] = []
    params = node.child_by_field_name("parameters")
    for param in params.named_children if params else []:
        entry = _parameter(param)
        if entry is not None:
            inputs.append(entry)
    if skip_first and inputs and not inputs[0]["name"].startswith("*"):
        inputs.pop(0)

    contract: dict[str, Any] = {}
    if inputs:
        contract["inputs"] = inputs
    returns = text(node.child_by_field_name("return_type"))
    if returns and returns != "None":
        contract["outputs"] = [{"type": returns}]
    errors = _raised(node.child_by_field_name("body"))
    if errors:
        contract["errors"] = [{"type": e} for e in errors]
    return contract or None


def _parameter(param: Node) -> dict[str, Any] | None:
    kind = param.type
    if kind in {"keyword_separator", "positional_separator"}:
        return None
    entry: dict[str, Any]
    if kind == "identifier":
        return {"name": text(param)}
    if kind in {"list_splat_pattern", "dictionary_splat_pattern"}:
        return {"name": text(param), "required": False}
    if kind in {"default_parameter", "typed_default_parameter"}:
        entry = {"name": text(param.child_by_field_name("name"))}
    elif kind == "typed_parameter":
        first = param.named_children[0] if param.named_children else None
        entry = {"name": text(first)}
    else:
        return None
    type_node = param.child_by_field_name("type")
    if type_node is not None:
        entry["type"] = text(type_node)
    # Parameters with defaults, and typed *args / **kwargs, are optional.
    if kind != "typed_parameter" or entry["name"].startswith("*"):
        entry["required"] = False
    return entry


def _raised(body: Node | None) -> list[str]:
    if body is None:
        return []
    found: dict[str, None] = {}
    for node in walk(body, prune=_is_definition):
        if node.type != "raise_statement" or not node.named_children:
            continue
        exc = node.named_children[0]
        if exc.type == "call":
            exc = exc.child_by_field_name("function") or exc
        chain = _chain(exc)
        if chain and chain[-1][:1].isupper():
            found[".".join(chain)] = None
    return list(found)


def _import_name(root: Path, path: str) -> tuple[str, bool]:
    """Dotted import name of a file, and whether it sits inside a package."""
    pure = PurePosixPath(path)
    stem = pure.name.removesuffix(".py")
    packages: list[str] = []
    directory = root / pure.parent
    while directory != root and (directory / "__init__.py").is_file():
        packages.insert(0, directory.name)
        directory = directory.parent
    if packages:
        return ".".join([*packages, *([] if stem == "__init__" else [stem])]), True
    return ids.path_id(path), False
