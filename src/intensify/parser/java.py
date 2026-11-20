"""Java analyzer: files, classes, interfaces, enums, records, methods, calls and inheritance.

Each file is a module whose ID is its package plus the file name (``com.shop.Cart``). Calls on
variables resolve through the declared type of the local variable, parameter or field; calls
through method chains, generics and lambdas' inferred types are not followed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import tree_sitter_java
from tree_sitter import Language, Node, Parser

from intensify.parser import ids
from intensify.parser.base import AnalysisResult, AnalyzeOptions, Dependency, Symbol
from intensify.parser.treesitter import fingerprint, line_count, name_point, squash, text, walk
from intensify.spec.models import EdgeType, NodeKind

LANGUAGE = Language(tree_sitter_java.language())

_IDENTIFIERS = frozenset({"identifier", "type_identifier"})
_TYPES = frozenset(
    {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
)
_METHODS = frozenset({"method_declaration", "constructor_declaration"})
_BRANCHES = frozenset(
    {
        "if_statement",
        "for_statement",
        "enhanced_for_statement",
        "while_statement",
        "do_statement",
        "catch_clause",
        "ternary_expression",
    }
)
_LOGICAL = frozenset({"&&", "||"})
_ANNOTATIONS = frozenset({"marker_annotation", "annotation"})
_MAX_INHERITANCE_DEPTH = 5
_DESCRIPTOR_FILES = frozenset({"package-info.java", "module-info.java"})
"""Package and module descriptors: annotations and directives, no code."""


def is_test_path(path: str) -> bool:
    """Maven/Gradle test source sets, and Surefire's default test class name patterns."""
    pure = PurePosixPath(path)
    stem = pure.stem
    in_test_dir = any(part in {"test", "tests"} for part in pure.parent.parts)
    return (
        in_test_dir
        or stem.endswith(("Test", "Tests", "TestCase", "IT"))
        or (stem.startswith("Test") and stem[4:5].isupper())
    )


def _skip_nested(node: Node) -> bool:
    """Lambdas and class bodies (anonymous and local classes) are not part of the outer logic."""
    return node.type in {"lambda_expression", "class_body"}


@dataclass
class _Call:
    scope: str
    class_ctx: str | None
    chain: list[str]
    """Receiver chain plus method name (``["Gateway", "charge"]``), or a type for ``new``."""
    point: tuple[int, int] = (0, 0)
    """Position of the called name, for a language server."""
    construct: bool = False
    is_super: bool = False


@dataclass
class _Module:
    path: str
    id: str
    package: str
    is_test: bool
    symbols: list[Symbol] = field(default_factory=list)
    types: dict[str, str] = field(default_factory=dict)
    """Simple name of every type declared in the file -> symbol ID (outermost wins)."""
    imports: dict[str, str] = field(default_factory=dict)
    """Simple name -> fully qualified type name."""
    star_imports: list[str] = field(default_factory=list)
    static_imports: dict[str, str] = field(default_factory=dict)
    """Member name -> fully qualified type name that declares it."""
    static_star_imports: list[str] = field(default_factory=list)
    calls: list[_Call] = field(default_factory=list)
    sites: list[tuple[str, tuple[int, int]]] = field(default_factory=list)
    """Scope and position of calls that can't be resolved statically at all."""
    heritage: list[tuple[str, list[str]]] = field(default_factory=list)


class JavaAnalyzer:
    languages: tuple[str, ...] = ("java",)
    extensions: tuple[str, ...] = (".java",)

    def __init__(self) -> None:
        self._parser = Parser(LANGUAGE)
        self._allocator = ids.Allocator()
        self._outer: dict[str, str] = {}
        """Nested type ID -> enclosing type ID."""
        self._fields: dict[str, dict[str, list[str]]] = {}
        """Type ID -> field name -> declared type chain."""

    def analyze(
        self, root: Path, paths: list[str], options: AnalyzeOptions, allocator: ids.Allocator
    ) -> AnalysisResult:
        self._allocator = allocator
        self._outer = {}
        self._fields = {}
        result = AnalysisResult()
        trees: dict[str, Node] = {}
        for path in paths:
            if PurePosixPath(path).name in _DESCRIPTOR_FILES:
                continue
            program = self._parser.parse((root / path).read_bytes()).root_node
            if program.has_error:
                result.warnings.append(f"{path}: syntax errors; results may be incomplete")
            trees[path] = program
        # Claim module IDs before any symbol IDs, so modules get the unsuffixed names.
        packages = {path: _package(tree) for path, tree in trees.items()}
        module_ids = {
            path: allocator.claim(
                ids.join(pkg, PurePosixPath(path).stem) if pkg else ids.path_id(path)
            )
            for path, pkg in packages.items()
        }
        modules = []
        for path, tree in trees.items():
            module = _Module(path, module_ids[path], packages[path], is_test_path(path))
            self._program(module, tree)
            modules.append(module)
            result.files[path] = "java"
            result.symbols.extend(module.symbols)

        resolver = _Resolver(modules, self._outer)
        test_ids = {s.id for m in modules if m.is_test for s in m.symbols}
        for module in modules:
            for class_id, chain in module.heritage:
                resolver.bases.setdefault(class_id, [])
                target = resolver.type(module, chain, self._outer.get(class_id, class_id))
                if target and target != class_id:
                    resolver.bases[class_id].append(target)
                    result.dependencies.append(Dependency(class_id, target, EdgeType.IMPLEMENTS))
        overloaded = overloaded_methods(result.symbols)
        for module in modules:
            for scope, point in module.sites:
                source = module.id if module.is_test else scope
                result.site(source, module.is_test, module.path, point)
            for call in module.calls:
                target = resolver.call(module, call)
                source = module.id if module.is_test else call.scope
                if target is None or target in overloaded:
                    # Unresolved, or the first of several overloads: a language server can tell.
                    if target is None or target not in test_ids:
                        result.site(source, module.is_test, module.path, call.point, target)
                    continue
                if target in test_ids:
                    continue
                if module.is_test:
                    result.dependencies.append(Dependency(module.id, target, EdgeType.VERIFIES))
                else:
                    result.dependencies.append(Dependency(call.scope, target, EdgeType.CALLS))
        return result

    # -- per-file extraction -------------------------------------------------------------

    def _program(self, module: _Module, program: Node) -> None:
        mod = Symbol(
            id=module.id,
            kind=NodeKind.module,
            language="java",
            path=module.path,
            symbol=None,
            parent=None,
            line=1,
            complexity={"loc": line_count(program)},
            fingerprint=fingerprint(program, _IDENTIFIERS),
            is_test=module.is_test,
        )
        module.symbols.append(mod)
        for node in program.named_children:
            if node.type == "import_declaration":
                self._import(module, node)
            elif node.type in _TYPES:
                sym = self._type(module, node, mod, container=None)
                sym.public = "public" in _modifiers(node)

    def _import(self, module: _Module, node: Node) -> None:
        name = next(
            (c for c in node.named_children if c.type in {"scoped_identifier", "identifier"}),
            None,
        )
        if name is None:
            return
        qualified = text(name)
        is_static = any(c.type == "static" for c in node.children)
        is_star = any(c.type == "asterisk" for c in node.children)
        if is_static and is_star:
            module.static_star_imports.append(qualified)
        elif is_static:
            owner, _, member = qualified.rpartition(".")
            module.static_imports[member] = owner
        elif is_star:
            module.star_imports.append(qualified)
        else:
            module.imports[qualified.rsplit(".", 1)[-1]] = qualified

    def _type(self, module: _Module, node: Node, parent: Symbol, container: Node | None) -> Symbol:
        name = text(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        sym = self._symbol(module, node, parent, name, NodeKind.class_, _signature(node, body))
        module.types.setdefault(name, sym.id)
        if parent.kind is NodeKind.class_:
            self._outer[sym.id] = parent.id
        if container is not None:
            sym.public = _member_public(node, container)

        module.heritage.extend((sym.id, chain) for chain in _supertypes(node))

        members = list(body.named_children) if body else []
        if body is not None and body.type == "enum_body":
            decls = next((c for c in members if c.type == "enum_body_declarations"), None)
            for constant in (c for c in members if c.type == "enum_constant"):
                self._collect_calls(module, constant, sym.id, sym.id, {})
            members = list(decls.named_children) if decls else []
        self._fields[sym.id] = _field_types(node, members)

        for member in members:
            if member.type in _TYPES:
                self._type(module, member, sym, container=node)
            elif member.type in _METHODS:
                self._method(module, member, sym, node)
            elif member.type in {
                "field_declaration",
                "constant_declaration",
                "static_initializer",
                "compact_constructor_declaration",
            }:
                self._collect_calls(module, member, sym.id, sym.id, {})
        return sym

    def _method(self, module: _Module, node: Node, parent: Symbol, container: Node) -> None:
        name = text(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        sym = self._symbol(module, node, parent, name, NodeKind.function, _signature(node, body))
        sym.public = _member_public(node, container)
        sym.contract = _contract(node)

        local_types: dict[str, list[str]] = {}
        params = node.child_by_field_name("parameters")
        for param in params.named_children if params else []:
            name_node = _parameter_name(param)
            if name_node is not None and (chain := _type_chain(_parameter_type(param))):
                local_types[text(name_node)] = chain
        branches = 0
        if body is not None:
            for n in [body, *walk(body, prune=_skip_nested)]:
                if n.type in _BRANCHES:
                    branches += 1
                elif n.type == "switch_label":
                    branches += any(c.type == "case" for c in n.children)
                elif n.type == "binary_expression":
                    branches += text(n.child_by_field_name("operator")) in _LOGICAL
            for n in walk(body):
                if n.type == "local_variable_declaration":
                    _declare(local_types, n.child_by_field_name("type"), n)
                elif n.type == "enhanced_for_statement" and (
                    chain := _type_chain(n.child_by_field_name("type"))
                ):
                    local_types[text(n.child_by_field_name("name"))] = chain
            # Calls in lambdas and anonymous classes belong to the enclosing method.
            self._collect_calls(module, body, sym.id, parent.id, local_types)
        sym.complexity = {"cyclomatic": 1 + branches, "loc": line_count(node)}

    def _symbol(
        self, module: _Module, node: Node, parent: Symbol, name: str, kind: NodeKind, signature: str
    ) -> Symbol:
        qualified = f"{parent.symbol}.{name}" if parent.symbol else name
        sym = Symbol(
            id=self._allocator.claim(ids.join(module.id, qualified)),
            kind=kind,
            language="java",
            path=module.path,
            symbol=qualified,
            parent=parent.id,
            line=node.start_point.row + 1,
            signature=signature,
            complexity={"loc": line_count(node)},
            fingerprint=fingerprint(node, _IDENTIFIERS),
            is_test=module.is_test,
        )
        module.symbols.append(sym)
        return sym

    def _collect_calls(
        self,
        module: _Module,
        node: Node,
        scope: str,
        class_ctx: str,
        local_types: dict[str, list[str]],
    ) -> None:
        fields = self._fields.get(class_ctx, {})
        for n in [node, *walk(node)]:
            if n.type == "object_creation_expression":
                type_node = n.child_by_field_name("type")
                if (chain := _type_chain(type_node)) and type_node is not None:
                    module.calls.append(
                        _Call(scope, class_ctx, chain, name_point(type_node), construct=True)
                    )
                continue
            if n.type != "method_invocation":
                continue
            name_node = n.child_by_field_name("name")
            method = text(name_node)
            point = name_point(name_node) if name_node is not None else name_point(n)
            receiver = n.child_by_field_name("object")
            if receiver is None:
                module.calls.append(_Call(scope, class_ctx, [method], point))
                continue
            if receiver.type == "super":
                module.calls.append(_Call(scope, class_ctx, [method], point, is_super=True))
                continue
            chain = _chain(receiver)
            if chain is None:
                module.sites.append((scope, point))  # a call on a call: a.b().c()
                continue
            # A variable's declared type stands in for the variable; variables shadow types.
            head, rest = chain[0], chain[1:]
            if head == "this":
                if rest and (len(rest) > 1 or rest[0] not in fields):
                    module.sites.append((scope, point))
                    continue
                chain = fields[rest[0]] if rest else ["this"]
            elif head in local_types or head in fields:
                if rest:
                    module.sites.append((scope, point))  # through a field of a variable
                    continue
                chain = local_types.get(head) or fields[head]
            module.calls.append(_Call(scope, class_ctx, [*chain, method], point))


class _Resolver:
    def __init__(self, modules: list[_Module], outer: dict[str, str]) -> None:
        self.outer = outer
        self.index: dict[str, str] = {}
        """Fully qualified type name -> symbol ID."""
        self.members: dict[str, dict[str, str]] = {}
        self.bases: dict[str, list[str]] = {}
        self.nested: dict[str, dict[str, str]] = {}
        """Type ID -> simple name -> nested type ID."""
        for module in modules:
            prefix = f"{module.package}." if module.package else ""
            for sym in module.symbols:
                if sym.kind is NodeKind.class_:
                    self.index.setdefault(f"{prefix}{sym.symbol}", sym.id)
                    if sym.id in outer:
                        self.nested.setdefault(outer[sym.id], {}).setdefault(sym.name, sym.id)
                elif sym.kind is NodeKind.function and sym.parent:
                    # Overloads share a name; calls go to the first one declared.
                    self.members.setdefault(sym.parent, {}).setdefault(sym.name, sym.id)

    def member(self, class_id: str, name: str, depth: int = 0) -> str | None:
        if found := self.members.get(class_id, {}).get(name):
            return found
        if depth < _MAX_INHERITANCE_DEPTH:
            for base in self.bases.get(class_id, []):
                if found := self.member(base, name, depth + 1):
                    return found
        return None

    def type(self, module: _Module, chain: list[str], class_ctx: str | None) -> str | None:
        """Symbol ID of the type named by ``chain`` as seen from ``class_ctx`` in ``module``."""
        head, rest = chain[0], chain[1:]
        found: str | None = None
        # Nested types of the enclosing classes (and their bases), innermost first.
        ctx = class_ctx
        while ctx is not None and found is None:
            found = self._nested(ctx, head)
            ctx = self.outer.get(ctx)
        if found is None and head in module.types:
            found = module.types[head]
        if found is None and head in module.imports:
            found = self.index.get(module.imports[head])
        if found is None:
            same_package = f"{module.package}.{head}" if module.package else head
            found = self.index.get(same_package)
        for star in module.star_imports:
            if found is None:
                found = self.index.get(f"{star}.{head}")
        if found is None:
            return self.index.get(".".join(chain))  # fully qualified
        for name in rest:
            if (found := self._nested(found, name)) is None:
                return None
        return found

    def _nested(self, class_id: str, name: str, depth: int = 0) -> str | None:
        if found := self.nested.get(class_id, {}).get(name):
            return found
        if depth < _MAX_INHERITANCE_DEPTH:
            for base in self.bases.get(class_id, []):
                if found := self._nested(base, name, depth + 1):
                    return found
        return None

    def call(self, module: _Module, call: _Call) -> str | None:
        if call.construct:
            return self.type(module, call.chain, call.class_ctx)
        *receiver, method = call.chain
        if call.is_super:
            bases = self.bases.get(call.class_ctx or "", [])
            return next((m for b in bases if (m := self.member(b, method))), None)
        if receiver == ["this"]:
            return self.member(call.class_ctx, method) if call.class_ctx else None
        if receiver:
            target = self.type(module, receiver, call.class_ctx)
            return self.member(target, method) if target else None
        return self._unqualified(module, method, call.class_ctx)

    def _unqualified(self, module: _Module, method: str, class_ctx: str | None) -> str | None:
        """The class, then the classes around it, then static imports."""
        ctx = class_ctx
        while ctx is not None:
            if found := self.member(ctx, method):
                return found
            ctx = self.outer.get(ctx)
        if method in module.static_imports:
            owner = self.index.get(module.static_imports[method])
            return self.member(owner, method) if owner else None
        for star in module.static_star_imports:
            if (owner := self.index.get(star)) and (found := self.member(owner, method)):
                return found
        return None


def overloaded_methods(symbols: list[Symbol]) -> set[str]:
    """IDs of methods that share their name with another method of the same class."""
    groups: dict[tuple[str | None, str], list[str]] = {}
    for sym in symbols:
        if sym.kind is NodeKind.function:
            groups.setdefault((sym.parent, sym.name), []).append(sym.id)
    return {i for group in groups.values() if len(group) > 1 for i in group}


def _package(program: Node) -> str:
    for node in program.named_children:
        if node.type == "package_declaration":
            name = next(
                (c for c in node.named_children if c.type in {"scoped_identifier", "identifier"}),
                None,
            )
            return text(name)
    return ""


def _supertypes(node: Node) -> list[list[str]]:
    """Type chains of a declaration's superclass and implemented or extended interfaces."""
    out: list[list[str]] = []
    for child in node.named_children:
        if child.type == "superclass":
            targets = child.named_children
        elif child.type in {"super_interfaces", "extends_interfaces"}:
            type_list = next((c for c in child.named_children if c.type == "type_list"), None)
            targets = type_list.named_children if type_list else []
        else:
            continue
        out.extend(chain for t in targets if (chain := _type_chain(t)))
    return out


def _field_types(node: Node, members: list[Node]) -> dict[str, list[str]]:
    """Field name -> declared type chain, including record components."""
    fields: dict[str, list[str]] = {}
    if node.type == "record_declaration":
        params = node.child_by_field_name("parameters")
        for param in params.named_children if params else []:
            if chain := _type_chain(param.child_by_field_name("type")):
                fields[text(param.child_by_field_name("name"))] = chain
    for member in members:
        if member.type not in {"field_declaration", "constant_declaration"}:
            continue
        if chain := _type_chain(member.child_by_field_name("type")):
            for declarator in member.children_by_field_name("declarator"):
                fields[text(declarator.child_by_field_name("name"))] = chain
    return fields


def _modifiers(node: Node) -> set[str]:
    mods = next((c for c in node.children if c.type == "modifiers"), None)
    return {c.type for c in mods.children if c.type not in _ANNOTATIONS} if mods else set()


def _member_public(node: Node, container: Node) -> bool:
    """Public API: ``public`` and ``protected`` members, and non-private interface members."""
    mods = _modifiers(node)
    if container.type == "interface_declaration":
        return "private" not in mods
    return bool(mods & {"public", "protected"})


def _signature(node: Node, body: Node | None) -> str:
    """Declaration up to its body, without annotations, on one line."""
    end = body.start_byte if body is not None else node.end_byte
    cuts = [
        (c.start_byte, c.end_byte)
        for mods in node.children
        if mods.type == "modifiers"
        for c in mods.children
        if c.type in _ANNOTATIONS or c.type in {"line_comment", "block_comment"}
    ]
    raw = node.text or b""
    out, pos = b"", node.start_byte
    for start, stop in cuts:
        out += raw[pos - node.start_byte : start - node.start_byte]
        pos = stop
    out += raw[pos - node.start_byte : end - node.start_byte]
    return squash(out.decode("utf-8", errors="replace")).rstrip(";{ ")


def _chain(node: Node | None) -> list[str] | None:
    """``a.b.c`` as ``["a", "b", "c"]``, or None if it isn't a plain dotted name."""
    if node is None:
        return None
    if node.type in {"identifier", "this"}:
        return [text(node)]
    if node.type == "field_access":
        obj = _chain(node.child_by_field_name("object"))
        name = node.child_by_field_name("field")
        return [*obj, text(name)] if obj is not None and name is not None else None
    if node.type == "scoped_identifier":
        return text(node).split(".")
    return None


def _type_chain(node: Node | None) -> list[str] | None:
    """Name of a class or interface type, without type arguments. None for other types."""
    if node is None:
        return None
    if node.type == "type_identifier":
        name = text(node)
        return None if name == "var" else [name]
    if node.type == "scoped_type_identifier":
        return [p for part in text(node).split(".") if (p := part.split("<")[0].strip())]
    if node.type == "generic_type":
        return _type_chain(node.named_children[0] if node.named_children else None)
    return None


def _declare(local_types: dict[str, list[str]], type_node: Node | None, decl: Node) -> None:
    """Record the types of the variables in a local declaration (``var`` from ``new T()``)."""
    declared = _type_chain(type_node)
    for declarator in decl.children_by_field_name("declarator"):
        chain = declared
        value = declarator.child_by_field_name("value")
        if chain is None and value is not None and value.type == "object_creation_expression":
            chain = _type_chain(value.child_by_field_name("type"))
        if chain:
            local_types[text(declarator.child_by_field_name("name"))] = chain


def _parameter_name(param: Node) -> Node | None:
    if param.type == "formal_parameter":
        return param.child_by_field_name("name")
    if param.type == "spread_parameter":
        declarator = next(
            (c for c in param.named_children if c.type == "variable_declarator"), None
        )
        return declarator.child_by_field_name("name") if declarator else None
    return None


def _parameter_type(param: Node) -> Node | None:
    if param.type == "formal_parameter":
        return param.child_by_field_name("type")
    return next(
        (c for c in param.named_children if c.type not in {"modifiers", "variable_declarator"}),
        None,
    )


def _contract(node: Node) -> dict[str, Any] | None:
    inputs: list[dict[str, Any]] = []
    params = node.child_by_field_name("parameters")
    for param in params.named_children if params else []:
        name = _parameter_name(param)
        if name is None:
            continue  # receiver parameter
        entry: dict[str, Any] = {"name": text(name)}
        type_text = squash(text(_parameter_type(param)))
        dims = text(param.child_by_field_name("dimensions"))
        if param.type == "spread_parameter":
            entry["type"] = f"{type_text}..."
            entry["required"] = False
        elif type_text:
            entry["type"] = type_text + dims
        inputs.append(entry)

    contract: dict[str, Any] = {}
    if inputs:
        contract["inputs"] = inputs
    returns = squash(text(node.child_by_field_name("type")))
    if node.type == "method_declaration" and returns and returns != "void":
        contract["outputs"] = [{"type": returns + text(node.child_by_field_name("dimensions"))}]

    errors: dict[str, None] = {}
    for child in node.children:
        if child.type == "throws":
            for thrown in child.named_children:
                if chain := _type_chain(thrown):
                    errors[".".join(chain)] = None
    body = node.child_by_field_name("body")
    for n in walk(body, prune=_skip_nested) if body is not None else []:
        if n.type == "throw_statement" and n.named_children:
            thrown = n.named_children[0]
            if thrown.type == "object_creation_expression" and (
                chain := _type_chain(thrown.child_by_field_name("type"))
            ):
                errors[".".join(chain)] = None
    if errors:
        contract["errors"] = [{"type": e} for e in errors]
    return contract or None
