"""Kotlin analyzer: files, classes, interfaces, objects, functions, extensions, calls, inheritance.

Each file is a module whose ID is its package plus the file name (``com.shop.Cart``), as in Java.
Companion object members sit directly under their class. Calls on values resolve through declared
types, constructor calls, ``as`` casts, the declared (or expression-body) result type of resolved
functions, properties, ``for`` over typed collections, and extension functions. Calls between
Kotlin and Java code are not followed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import tree_sitter_kotlin
from tree_sitter import Language, Node, Parser

from intensify.parser import ids
from intensify.parser.base import AnalysisResult, AnalyzeOptions, Dependency, Symbol
from intensify.parser.java import is_test_path
from intensify.parser.treesitter import fingerprint, line_count, squash, text, walk
from intensify.spec.models import EdgeType, NodeKind

LANGUAGE = Language(tree_sitter_kotlin.language())

__all__ = ["KotlinAnalyzer", "is_test_path"]

_IDENTIFIERS = frozenset({"identifier"})
_TYPES = frozenset({"class_declaration", "object_declaration"})
_TYPE_NODES = frozenset(
    {"user_type", "nullable_type", "function_type", "parenthesized_type", "non_nullable_type"}
)
_BRANCHES = frozenset(
    {"if_expression", "for_statement", "while_statement", "do_while_statement", "catch_block"}
)
_LOGICAL = frozenset({"&&", "||"})
_ANNOTATIONS = frozenset({"annotation", "file_annotation"})
_COMMENTS = frozenset({"line_comment", "block_comment"})
_COLLECTIONS = frozenset(
    {
        "List",
        "MutableList",
        "ArrayList",
        "Set",
        "MutableSet",
        "HashSet",
        "LinkedHashSet",
        "Collection",
        "MutableCollection",
        "Iterable",
        "Sequence",
        "Array",
    }
)
"""Types whose first type argument is the element type that ``for`` loops over."""
_BUILDERS = frozenset(
    {
        "listOf",
        "mutableListOf",
        "arrayListOf",
        "emptyList",
        "setOf",
        "mutableSetOf",
        "hashSetOf",
        "emptySet",
        "sequenceOf",
        "arrayOf",
    }
)
_MAX_DEPTH = 8


def _skip_nested(node: Node) -> bool:
    """Lambdas and object expressions are not part of the enclosing function's own logic."""
    return node.type in {"lambda_literal", "object_literal", "anonymous_function"}


# -- references --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Type:
    """A written type: its name chain, and the element type if it is a known collection."""

    chain: tuple[str, ...]
    elem: _Type | None = None


@dataclass(frozen=True)
class _Name:
    """A dotted name not bound to a local: a property, a type, an object or a qualified name."""

    chain: tuple[str, ...]


@dataclass(frozen=True)
class _This:
    pass


@dataclass(frozen=True)
class _Super:
    pass


@dataclass(frozen=True)
class _Member:
    base: _Ref
    name: str


@dataclass(frozen=True)
class _Result:
    """The value of calling ``name`` on ``base`` (or unqualified, if None)."""

    base: _Ref | None
    name: str


@dataclass(frozen=True)
class _Elem:
    """An element of the collection ``base``, as bound by ``for``."""

    base: _Ref


_Ref = _Type | _Name | _This | _Super | _Member | _Result | _Elem


@dataclass(frozen=True)
class _Ctx:
    """Where code runs: its module, enclosing class and extension receiver type."""

    module: _Module
    class_id: str | None = None
    receiver: _Type | None = None


@dataclass
class _Call:
    scope: str
    ctx: _Ctx
    base: _Ref | None
    name: str


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
    """Local name (the alias, if any) -> fully qualified name."""
    star_imports: list[str] = field(default_factory=list)
    calls: list[_Call] = field(default_factory=list)
    heritage: list[tuple[str, _Type]] = field(default_factory=list)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Module) and other.id == self.id


@dataclass
class _Class:
    ctx: _Ctx
    """The class's own context: module, and the class itself."""
    outer: str | None
    fields: dict[str, _Ref] = field(default_factory=dict)
    members: dict[str, str] = field(default_factory=dict)
    """Function name -> symbol ID (overloads: the first one declared)."""


@dataclass
class _Func:
    ctx: _Ctx
    result: _Ref | None


@dataclass
class _Extension:
    receiver: _Type
    ctx: _Ctx
    id: str


@dataclass
class _Package:
    functions: dict[str, str] = field(default_factory=dict)
    properties: dict[str, tuple[_Ref, _Ctx]] = field(default_factory=dict)
    extensions: dict[str, list[_Extension]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Value:
    """A resolved value: its type in the analyzed code (if any), and its element type."""

    type_id: str | None
    elem: tuple[_Type, _Ctx] | None = None


class KotlinAnalyzer:
    languages: tuple[str, ...] = ("kotlin",)
    extensions: tuple[str, ...] = (".kt",)

    def __init__(self) -> None:
        self._parser = Parser(LANGUAGE)
        self._allocator = ids.Allocator()
        self._classes: dict[str, _Class] = {}
        self._funcs: dict[str, _Func] = {}
        self._packages: dict[str, _Package] = {}

    def analyze(
        self, root: Path, paths: list[str], options: AnalyzeOptions, allocator: ids.Allocator
    ) -> AnalysisResult:
        self._allocator = allocator
        self._classes, self._funcs, self._packages = {}, {}, {}
        result = AnalysisResult()
        trees: dict[str, Node] = {}
        for path in paths:
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
            result.files[path] = "kotlin"
            result.symbols.extend(module.symbols)

        resolver = _Resolver(modules, self._classes, self._funcs, self._packages)
        test_ids = {s.id for m in modules if m.is_test for s in m.symbols}
        for module in modules:
            for class_id, written in module.heritage:
                info = self._classes[class_id]
                outer_ctx = _Ctx(module, info.outer)
                target = resolver.type(outer_ctx, written)
                resolver.bases.setdefault(class_id, [])
                if target and target != class_id:
                    resolver.bases[class_id].append(target)
                    result.dependencies.append(Dependency(class_id, target, EdgeType.IMPLEMENTS))
        for module in modules:
            for call in module.calls:
                target = resolver.call(call)
                if target is None or target in test_ids:
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
            language="kotlin",
            path=module.path,
            symbol=None,
            parent=None,
            line=1,
            complexity={"loc": line_count(program)},
            fingerprint=fingerprint(program, _IDENTIFIERS),
            is_test=module.is_test,
        )
        module.symbols.append(mod)
        package = self._packages.setdefault(module.package, _Package())
        ctx = _Ctx(module)
        for node in program.named_children:
            if node.type == "import":
                self._import(module, node)
            elif node.type in _TYPES:
                self._type(module, node, mod, outer=None, in_interface=False)
            elif node.type == "function_declaration":
                sym = self._function(module, node, mod, None, in_interface=False)
                receiver = _receiver_type(node)
                if receiver is not None:
                    package.extensions.setdefault(sym.name, []).append(
                        _Extension(receiver, ctx, sym.id)
                    )
                else:
                    package.functions.setdefault(sym.name, sym.id)
            elif node.type == "property_declaration":
                for name, ref in _property(node, {}, set()):
                    if ref is not None:
                        package.properties.setdefault(name, (ref, ctx))
                self._collect_calls(module, node, mod.id, ctx, {}, set())

    def _import(self, module: _Module, node: Node) -> None:
        name = next((c for c in node.named_children if c.type == "qualified_identifier"), None)
        if name is None:
            return
        qualified = text(name)
        alias = next((c for c in node.named_children if c.type == "identifier"), None)
        if any(c.type == "*" for c in node.children) or text(node).rstrip().endswith("*"):
            module.star_imports.append(qualified)
        else:
            module.imports[text(alias) if alias else qualified.rsplit(".", 1)[-1]] = qualified

    def _type(
        self, module: _Module, node: Node, parent: Symbol, outer: str | None, in_interface: bool
    ) -> Symbol:
        name = text(node.child_by_field_name("name"))
        body = _body(node)
        sym = self._symbol(module, node, parent, name, NodeKind.class_, _signature(node, body))
        sym.public = _public(node, in_interface)
        module.types.setdefault(name, sym.id)
        ctx = _Ctx(module, sym.id)
        info = _Class(ctx, outer)
        self._classes[sym.id] = info
        is_interface = any(c.type == "interface" for c in node.children)

        for spec in (c for c in node.named_children if c.type == "delegation_specifiers"):
            for written in _supertypes(spec):
                module.heritage.append((sym.id, written))
            self._collect_calls(module, spec, sym.id, ctx, {}, set())
        ctor = next((c for c in node.named_children if c.type == "primary_constructor"), None)
        params = (
            next((c for c in ctor.named_children if c.type == "class_parameters"), None)
            if ctor
            else None
        )
        ctor_types: dict[str, _Ref] = {}
        for param in params.named_children if params else []:
            pname = next((c for c in param.named_children if c.type == "identifier"), None)
            ptype = _type_ref(
                next((c for c in param.named_children if c.type in _TYPE_NODES), None)
            )
            if pname is None or ptype is None:
                continue
            ctor_types[text(pname)] = ptype
            if any(c.type in {"val", "var"} for c in param.children):
                info.fields[text(pname)] = ptype
        if params is not None:
            self._collect_calls(module, params, sym.id, ctx, {}, set())

        members = list(body.named_children) if body else []
        # Properties first, so methods declared above a property still see its type.
        for member in [*members, *_companion_members(members)]:
            if member.type == "property_declaration":
                for prop, ref in _property(member, ctor_types, set(ctor_types)):
                    if ref is not None:
                        info.fields.setdefault(prop, ref)
        self._members(module, members, sym, info, ctx, ctor_types, is_interface)
        return sym

    def _members(
        self,
        module: _Module,
        members: list[Node],
        sym: Symbol,
        info: _Class,
        ctx: _Ctx,
        ctor_types: dict[str, _Ref],
        is_interface: bool,
    ) -> None:
        for member in members:
            if member.type in _TYPES:
                self._type(module, member, sym, outer=sym.id, in_interface=is_interface)
            elif member.type == "companion_object":
                body = _body(member)
                inner = list(body.named_children) if body else []
                self._members(module, inner, sym, info, ctx, ctor_types, is_interface)
            elif member.type == "function_declaration":
                fsym = self._function(module, member, sym, ctx, in_interface=is_interface)
                info.members.setdefault(fsym.name, fsym.id)
            elif member.type in {"property_declaration", "anonymous_initializer", "enum_entry"}:
                # Initializers can use the primary constructor's parameters.
                self._collect_calls(module, member, sym.id, ctx, dict(ctor_types), set(ctor_types))
            elif member.type == "secondary_constructor":
                local_types, local_names = _parameters(member)
                self._collect_calls(module, member, sym.id, ctx, local_types, local_names)

    def _function(
        self, module: _Module, node: Node, parent: Symbol, ctx: _Ctx | None, in_interface: bool
    ) -> Symbol:
        name = text(node.child_by_field_name("name"))
        body = next((c for c in node.named_children if c.type == "function_body"), None)
        sym = self._symbol(module, node, parent, name, NodeKind.function, _signature(node, body))
        sym.public = _public(node, in_interface)
        sym.contract = _contract(node, body)
        receiver = _receiver_type(node)
        fctx = _Ctx(module, ctx.class_id if ctx else None, receiver)

        local_types, local_names = _parameters(node)
        declared = _type_ref(_result_type(node))
        result: _Ref | None = declared
        if (
            result is None
            and body is not None
            and not any(c.type == "block" for c in body.named_children)
        ):
            # Expression body: ``fun sandbox() = Gateway()``.
            expr = next(iter(body.named_children), None)
            result = _expr(expr, local_types, local_names)
        self._funcs[sym.id] = _Func(fctx, result)

        branches = 0
        if body is not None:
            for n in [body, *walk(body, prune=_skip_nested)]:
                if n.type in _BRANCHES:
                    branches += 1
                elif n.type == "when_entry":
                    branches += n.child_by_field_name("condition") is not None
                elif n.type == "binary_expression":
                    branches += any(c.type in _LOGICAL for c in n.children)
            # Calls in lambdas and object expressions belong to the enclosing function.
            self._collect_calls(module, body, sym.id, fctx, local_types, local_names)
        sym.complexity = {"cyclomatic": 1 + branches, "loc": line_count(node)}
        return sym

    def _symbol(
        self, module: _Module, node: Node, parent: Symbol, name: str, kind: NodeKind, signature: str
    ) -> Symbol:
        qualified = f"{parent.symbol}.{name}" if parent.symbol else name
        sym = Symbol(
            id=self._allocator.claim(ids.join(module.id, qualified)),
            kind=kind,
            language="kotlin",
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
        ctx: _Ctx,
        local_types: dict[str, _Ref],
        local_names: set[str],
    ) -> None:
        # Nodes come in source order, so a local's declaration is seen before its uses.
        for n in [node, *walk(node, prune=lambda n: n.type in _TYPES)]:
            if n.type == "property_declaration" and n is not node:
                for name, ref in _property(n, local_types, local_names):
                    local_names.add(name)
                    if ref is not None:
                        local_types[name] = ref
                    else:
                        local_types.pop(name, None)
            elif n.type == "for_statement":
                _declare_for(n, local_types, local_names)
            elif n.type in {"lambda_parameters", "catch_block"}:
                for ident in (c for c in walk(n) if c.type == "identifier"):
                    if ident.parent is not None and ident.parent.type in {
                        "variable_declaration",
                        "catch_block",
                    }:
                        local_names.add(text(ident))
                        local_types.pop(text(ident), None)
            elif n.type == "lambda_literal":
                local_names.add("it")
                local_types.pop("it", None)
            elif n.type == "call_expression":
                callee = n.named_children[0] if n.named_children else None
                if callee is None:
                    continue
                if callee.type == "identifier":
                    name = text(callee)
                    if name not in local_names:
                        module.calls.append(_Call(scope, ctx, None, name))
                elif callee.type == "navigation_expression":
                    parts = callee.named_children
                    base = _expr(parts[0], local_types, local_names) if len(parts) > 1 else None
                    if base is not None:
                        module.calls.append(_Call(scope, ctx, base, text(parts[-1])))


class _Resolver:
    def __init__(
        self,
        modules: list[_Module],
        classes: dict[str, _Class],
        funcs: dict[str, _Func],
        packages: dict[str, _Package],
    ) -> None:
        self.classes = classes
        self.funcs = funcs
        self.packages = packages
        self.bases: dict[str, list[str]] = {}
        self.index: dict[str, str] = {}
        """Fully qualified type or top-level function name -> symbol ID."""
        self.nested: dict[str, dict[str, str]] = {}
        for module in modules:
            prefix = f"{module.package}." if module.package else ""
            for sym in module.symbols:
                if sym.kind is NodeKind.class_:
                    self.index.setdefault(f"{prefix}{sym.symbol}", sym.id)
                    outer = classes[sym.id].outer
                    if outer is not None:
                        self.nested.setdefault(outer, {}).setdefault(sym.name, sym.id)
        for name, package in packages.items():
            prefix = f"{name}." if name else ""
            for fname, fid in package.functions.items():
                self.index.setdefault(f"{prefix}{fname}", fid)

    # -- types -----------------------------------------------------------------------

    def type(self, ctx: _Ctx, written: _Type) -> str | None:
        """Symbol ID of the type named by ``written`` as seen from ``ctx``."""
        found = self._simple_type(ctx, written.chain[0])
        if found is None:
            return self._qualified(written.chain)
        for name in written.chain[1:]:
            if (found := self._nested(found, name)) is None:
                return None
        return found

    def _simple_type(self, ctx: _Ctx, head: str) -> str | None:
        module = ctx.module
        current = ctx.class_id
        while current is not None and current in self.classes:
            if found := self._nested(current, head):
                return found
            current = self.classes[current].outer
        if head in module.types:
            return module.types[head]
        found = None
        if head in module.imports:
            found = self.index.get(module.imports[head])
        if found is None:
            found = self.index.get(f"{module.package}.{head}" if module.package else head)
        for star in module.star_imports:
            if found is None:
                found = self.index.get(f"{star}.{head}")
        return found if found in self.classes else None

    def _qualified(self, chain: tuple[str, ...]) -> str | None:
        found = self.index.get(".".join(chain))
        return found if found in self.classes else None

    def _nested(self, class_id: str, name: str, depth: int = 0) -> str | None:
        if found := self.nested.get(class_id, {}).get(name):
            return found
        if depth < _MAX_DEPTH:
            for base in self.bases.get(class_id, []):
                if found := self._nested(base, name, depth + 1):
                    return found
        return None

    def member(self, class_id: str, name: str, depth: int = 0) -> str | None:
        info = self.classes.get(class_id)
        if info is None:
            return None
        if found := info.members.get(name):
            return found
        if depth < _MAX_DEPTH:
            for base in self.bases.get(class_id, []):
                if found := self.member(base, name, depth + 1):
                    return found
        return None

    def _field(self, class_id: str, name: str, depth: int) -> _Value | None:
        info = self.classes.get(class_id)
        if info is None or depth > _MAX_DEPTH:
            return None
        if name in info.fields:
            return self.value(info.ctx, info.fields[name], depth + 1)
        for base in self.bases.get(class_id, []):
            if found := self._field(base, name, depth + 1):
                return found
        return None

    # -- values ----------------------------------------------------------------------

    def value(self, ctx: _Ctx, ref: _Ref, depth: int = 0) -> _Value | None:  # noqa: PLR0911
        if depth > _MAX_DEPTH:
            return None
        if isinstance(ref, _Type):
            elem = (ref.elem, ctx) if ref.elem is not None else None
            found = self.type(ctx, ref)
            return _Value(found, elem) if found or elem else None
        if isinstance(ref, _This):
            if ctx.class_id is not None:
                return _Value(ctx.class_id)
            return self.value(ctx, ctx.receiver, depth + 1) if ctx.receiver else None
        if isinstance(ref, _Name):
            return self._name(ctx, ref.chain, depth)
        if isinstance(ref, _Member):
            base = self.value(ctx, ref.base, depth + 1)
            return self._field(base.type_id, ref.name, depth) if base and base.type_id else None
        if isinstance(ref, _Result):
            target = self.callee(ctx, ref.base, ref.name, depth + 1)
            if target is None:
                return None
            if target in self.classes:
                return _Value(target)  # a constructor call
            func = self.funcs.get(target)
            if func is None or func.result is None:
                return None
            return self.value(func.ctx, func.result, depth + 1)
        if isinstance(ref, _Elem):
            base = self.value(ctx, ref.base, depth + 1)
            if base is None or base.elem is None:
                return None
            return self.value(base.elem[1], base.elem[0], depth + 1)
        return None

    def _this_types(self, ctx: _Ctx) -> list[str]:
        """The enclosing class, the classes around it, then the extension receiver type."""
        out: list[str] = []
        current = ctx.class_id
        while current is not None and current in self.classes:
            out.append(current)
            current = self.classes[current].outer
        if ctx.receiver is not None and (found := self.type(ctx, ctx.receiver)):
            out.append(found)
        return out

    def _name(self, ctx: _Ctx, chain: tuple[str, ...], depth: int) -> _Value | None:
        head, rest = chain[0], chain[1:]
        current: _Value | None = None
        for class_id in self._this_types(ctx):
            if current := self._field(class_id, head, depth + 1):
                break
        if current is None:
            for package in self._visible_packages(ctx.module, head):
                if head in package.properties:
                    ref, owner = package.properties[head]
                    current = self.value(owner, ref, depth + 1)
                    break
        if current is None and (found := self._simple_type(ctx, head)):
            current = _Value(found)  # an object, or a class's companion
        if current is None:
            # A qualified name: the longest prefix that is a type.
            for n in range(len(chain), 0, -1):
                if found := self._qualified(chain[:n]):
                    current, rest = _Value(found), chain[n:]
                    break
        for name in rest:
            if current is None or current.type_id is None:
                return None
            nested = self._nested(current.type_id, name)
            current = _Value(nested) if nested else self._field(current.type_id, name, depth + 1)
        return current

    def _visible_packages(self, module: _Module, name: str) -> list[_Package]:
        """Packages whose top-level ``name`` is visible: own, explicit import, star imports."""
        names = [module.package]
        if name in module.imports:
            names.append(module.imports[name].rpartition(".")[0])
        names.extend(module.star_imports)
        return [self.packages[n] for n in names if n in self.packages]

    # -- calls -----------------------------------------------------------------------

    def callee(self, ctx: _Ctx, base: _Ref | None, name: str, depth: int = 0) -> str | None:
        """The function (or, for a constructor call, the class) that a call runs."""
        if base is None:
            return self._unqualified(ctx, name)
        if isinstance(base, _Super):
            bases = self.bases.get(ctx.class_id or "", [])
            return next((m for b in bases if (m := self.member(b, name))), None)
        owner = self.value(ctx, base, depth)
        if owner is None or owner.type_id is None:
            return None
        if isinstance(base, _Name) and (nested := self._nested(owner.type_id, name)):
            return nested  # Outer.Inner(...)
        return self.member(owner.type_id, name) or self._extension(ctx, owner.type_id, name)

    def _unqualified(self, ctx: _Ctx, name: str) -> str | None:
        this_types = self._this_types(ctx)
        for class_id in this_types:
            if found := self.member(class_id, name):
                return found
        for this_type in this_types:
            if found := self._extension(ctx, this_type, name):
                return found
        module = ctx.module
        if name in module.imports and (found := self.index.get(module.imports[name])):
            return found
        for package in self._visible_packages(module, name):
            if name in package.functions:
                return package.functions[name]
        return self._simple_type(ctx, name)  # a constructor call

    def _extension(self, ctx: _Ctx, type_id: str, name: str) -> str | None:
        """A visible extension function ``name`` whose receiver is the type or one of its bases."""
        wanted = self._supertypes(type_id)
        for package in self._visible_packages(ctx.module, name):
            for ext in package.extensions.get(name, []):
                if self.type(ext.ctx, ext.receiver) in wanted:
                    return ext.id
        return None

    def _supertypes(self, type_id: str) -> set[str]:
        out: set[str] = set()
        stack = [type_id]
        while stack and len(out) < _MAX_DEPTH * 4:
            current = stack.pop()
            if current not in out:
                out.add(current)
                stack.extend(self.bases.get(current, []))
        return out

    def call(self, call: _Call) -> str | None:
        return self.callee(call.ctx, call.base, call.name)


# -- syntax helpers ----------------------------------------------------------------------


def _package(program: Node) -> str:
    for node in program.named_children:
        if node.type == "package_header":
            name = next((c for c in node.named_children if c.type == "qualified_identifier"), None)
            return text(name)
    return ""


def _body(node: Node) -> Node | None:
    return next(
        (c for c in node.named_children if c.type in {"class_body", "enum_class_body"}), None
    )


def _companion_members(members: list[Node]) -> list[Node]:
    out: list[Node] = []
    for member in members:
        if member.type == "companion_object" and (body := _body(member)) is not None:
            out.extend(body.named_children)
    return out


def _modifiers(node: Node) -> set[str]:
    mods = next((c for c in node.named_children if c.type == "modifiers"), None)
    if mods is None:
        return set()
    return {text(c) for c in mods.named_children if c.type not in _ANNOTATIONS}


def _public(node: Node, in_interface: bool) -> bool:
    """Kotlin's default is public. ``protected`` counts as public API; ``internal`` doesn't."""
    mods = _modifiers(node)
    if in_interface:
        return "private" not in mods
    return not mods & {"private", "internal"}


def _signature(node: Node, body: Node | None) -> str:
    """Declaration up to its body, without annotations or comments, on one line."""
    end = body.start_byte if body is not None else node.end_byte
    cuts = [
        (c.start_byte, c.end_byte)
        for mods in node.named_children
        if mods.type == "modifiers"
        for c in mods.named_children
        if c.type in _ANNOTATIONS
    ]
    cuts += [(c.start_byte, c.end_byte) for c in node.named_children if c.type in _COMMENTS]
    raw = node.text or b""
    out, pos = b"", node.start_byte
    for start, stop in sorted(cuts):
        if start >= end:
            break
        out += raw[pos - node.start_byte : start - node.start_byte]
        pos = stop
    out += raw[pos - node.start_byte : end - node.start_byte]
    return squash(out.decode("utf-8", errors="replace")).rstrip("={ ")


def _type_ref(node: Node | None) -> _Type | None:
    """A named type, without nullability. None for function types and others."""
    while node is not None and node.type in {
        "nullable_type",
        "parenthesized_type",
        "non_nullable_type",
    }:
        node = next((c for c in node.named_children if c.type in _TYPE_NODES), None)
    if node is None or node.type != "user_type":
        return None
    chain = tuple(text(c) for c in node.named_children if c.type == "identifier")
    if not chain:
        return None
    elem = None
    args = next((c for c in node.named_children if c.type == "type_arguments"), None)
    if chain[-1] in _COLLECTIONS and args is not None and args.named_children:
        projection = args.named_children[0]
        elem = _type_ref(
            next((c for c in projection.named_children if c.type in _TYPE_NODES), None)
        )
    return _Type(chain, elem)


def _receiver_type(node: Node) -> _Type | None:
    """The receiver type of an extension function (``fun Cart.halfTotal()``)."""
    name = node.child_by_field_name("name")
    for child in node.named_children:
        if name is not None and child.start_byte >= name.start_byte:
            break
        if child.type in _TYPE_NODES:
            return _type_ref(child)
    return None


def _result_type(node: Node) -> Node | None:
    params = next((c for c in node.named_children if c.type == "function_value_parameters"), None)
    if params is None:
        return None
    for child in node.named_children:
        if child.start_byte > params.start_byte and child.type in _TYPE_NODES:
            return child
        if child.type == "function_body":
            break
    return None


def _parameters(node: Node) -> tuple[dict[str, _Ref], set[str]]:
    local_types: dict[str, _Ref] = {}
    local_names: set[str] = set()
    params = next((c for c in node.named_children if c.type == "function_value_parameters"), None)
    for param in params.named_children if params else []:
        if param.type != "parameter":
            continue
        name = next((c for c in param.named_children if c.type == "identifier"), None)
        if name is None:
            continue
        local_names.add(text(name))
        ref = _type_ref(next((c for c in param.named_children if c.type in _TYPE_NODES), None))
        if ref is not None:
            local_types[text(name)] = ref
    return local_types, local_names


def _supertypes(spec: Node) -> list[_Type]:
    out: list[_Type] = []
    for item in (c for c in spec.named_children if c.type == "delegation_specifier"):
        node = item.named_children[0] if item.named_children else None
        if node is not None and node.type in {"constructor_invocation", "explicit_delegation"}:
            node = next((c for c in node.named_children if c.type == "user_type"), None)
        if (ref := _type_ref(node)) is not None:
            out.append(ref)
    return out


def _expr(node: Node | None, local_types: dict[str, _Ref], local_names: set[str]) -> _Ref | None:  # noqa: PLR0911
    """What an expression evaluates to, as a reference to resolve later."""
    while node is not None and (
        node.type == "parenthesized_expression"
        or (node.type == "postfix_expression" and text(node).endswith("!!"))
    ):
        node = node.named_children[0] if node.named_children else None
    if node is None:
        return None
    if node.type == "identifier":
        name = text(node)
        if name in local_names:
            return local_types.get(name)
        return _Name((name,))
    if node.type == "this_expression":
        return _This()
    if node.type == "super_expression":
        return _Super()
    if node.type == "as_expression":
        return _type_ref(node.child_by_field_name("right"))
    if node.type == "navigation_expression":
        parts = node.named_children
        if len(parts) < 2 or parts[-1].type != "identifier":  # noqa: PLR2004 - receiver and name
            return None
        base = _expr(parts[0], local_types, local_names)
        name = text(parts[-1])
        if isinstance(base, _Name):
            return _Name((*base.chain, name))
        return _Member(base, name) if base is not None else None
    if node.type == "call_expression":
        return _call_result(node, local_types, local_names)
    return None


def _call_result(node: Node, local_types: dict[str, _Ref], local_names: set[str]) -> _Ref | None:
    callee = node.named_children[0] if node.named_children else None
    if callee is None:
        return None
    if callee.type == "identifier":
        name = text(callee)
        if name in local_names:
            return None
        args = next((c for c in node.named_children if c.type == "type_arguments"), None)
        if name in _BUILDERS and args is not None and args.named_children:
            projection = args.named_children[0]
            elem = _type_ref(
                next((c for c in projection.named_children if c.type in _TYPE_NODES), None)
            )
            return _Type(("List",), elem)
        return _Result(None, name)
    if callee.type == "navigation_expression":
        parts = callee.named_children
        base = _expr(parts[0], local_types, local_names) if len(parts) > 1 else None
        return _Result(base, text(parts[-1])) if base is not None else None
    return None


def _property(
    node: Node, local_types: dict[str, _Ref], local_names: set[str]
) -> list[tuple[str, _Ref | None]]:
    """Names declared by a ``val`` or ``var``, with what each holds."""
    decl = next((c for c in node.named_children if c.type == "variable_declaration"), None)
    if decl is None:
        multi = next(
            (c for c in node.named_children if c.type == "multi_variable_declaration"), None
        )
        names = [text(c) for c in walk(multi) if c.type == "identifier"] if multi else []
        return [(n, None) for n in names if n != "_"]
    name = next((c for c in decl.named_children if c.type == "identifier"), None)
    if name is None:
        return []
    declared = _type_ref(next((c for c in decl.named_children if c.type in _TYPE_NODES), None))
    if declared is not None:
        return [(text(name), declared)]
    value = next(
        (
            c
            for c in node.named_children
            if c.start_byte > decl.end_byte
            and c.type not in {"getter", "setter", "property_delegate", "modifiers"}
            and c.type not in _COMMENTS
        ),
        None,
    )
    return [(text(name), _expr(value, local_types, local_names))]


def _declare_for(node: Node, local_types: dict[str, _Ref], local_names: set[str]) -> None:
    """``for (x in xs)``: ``x`` gets the element type of ``xs`` when it is known."""
    decl = next((c for c in node.named_children if c.type == "variable_declaration"), None)
    if decl is None:
        multi = next(
            (c for c in node.named_children if c.type == "multi_variable_declaration"), None
        )
        for ident in walk(multi) if multi else []:
            if ident.type == "identifier":
                local_names.add(text(ident))
                local_types.pop(text(ident), None)
        return
    name = next((c for c in decl.named_children if c.type == "identifier"), None)
    if name is None:
        return
    local_names.add(text(name))
    declared = _type_ref(next((c for c in decl.named_children if c.type in _TYPE_NODES), None))
    iterable = next(
        (c for c in node.named_children if c.start_byte > decl.end_byte and c.type != "block"),
        None,
    )
    source = _expr(iterable, local_types, local_names) if iterable is not None else None
    ref: _Ref | None = declared or (_Elem(source) if source is not None else None)
    if ref is not None:
        local_types[text(name)] = ref
    else:
        local_types.pop(text(name), None)


def _contract(node: Node, body: Node | None) -> dict[str, Any] | None:
    inputs: list[dict[str, Any]] = []
    params = next((c for c in node.named_children if c.type == "function_value_parameters"), None)
    children = list(params.named_children) if params else []
    vararg = False
    for i, child in enumerate(children):
        if child.type == "parameter_modifiers":
            vararg = "vararg" in text(child)
            continue
        if child.type != "parameter":
            continue
        name = next((c for c in child.named_children if c.type == "identifier"), None)
        type_node = next((c for c in child.named_children if c.type in _TYPE_NODES), None)
        entry: dict[str, Any] = {"name": text(name)}
        if type_node is not None:
            entry["type"] = ("vararg " if vararg else "") + squash(text(type_node))
        following = children[i + 1] if i + 1 < len(children) else None
        if vararg or (
            following is not None and following.type not in {"parameter", "parameter_modifiers"}
        ):
            entry["required"] = False  # vararg, or has a default value
        inputs.append(entry)
        vararg = False

    contract: dict[str, Any] = {}
    if inputs:
        contract["inputs"] = inputs
    result = _result_type(node)
    if result is not None and text(result) != "Unit":
        contract["outputs"] = [{"type": squash(text(result))}]

    errors: dict[str, None] = {}
    mods = next((c for c in node.named_children if c.type == "modifiers"), None)
    for annotation in (c for c in (mods.named_children if mods else []) if c.type == "annotation"):
        if text(annotation).startswith("@Throws"):
            for ref in walk(annotation):
                if ref.type == "navigation_expression" and text(ref).endswith("::class"):
                    errors[text(ref).removesuffix("::class")] = None
    for n in walk(body, prune=_skip_nested) if body is not None else []:
        if n.type == "throw_expression" and n.named_children:
            thrown = n.named_children[0]
            callee = thrown.named_children[0] if thrown.named_children else None
            if thrown.type == "call_expression" and callee is not None:
                thrown_type = text(callee)
                if thrown_type[:1].isupper() and callee.type in {
                    "identifier",
                    "navigation_expression",
                }:
                    errors[thrown_type] = None
    if errors:
        contract["errors"] = [{"type": e} for e in errors]
    return contract or None
