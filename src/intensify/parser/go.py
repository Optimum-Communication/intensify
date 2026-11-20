"""Go analyzer: files, named types, functions, methods, calls, embedding and interfaces.

Each file is a module named by its path (``cart/cart.go`` -> ``cart.cart``). Named types become
classes; methods sit under their receiver type even when declared in another file of the same
package. Import paths resolve through the nearest ``go.mod``. Calls on variables resolve through
declared types, composite literals, type assertions, the results of resolved functions, struct
fields and ``range`` over slices and maps. A type that has every method of an interface in the
analyzed code gets a medium-confidence ``IMPLEMENTS`` edge to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, cast

import tree_sitter_go
from tree_sitter import Language, Node, Parser

from intensify.parser import ids
from intensify.parser.base import AnalysisResult, AnalyzeOptions, Dependency, Symbol
from intensify.parser.treesitter import fingerprint, header, line_count, squash, text, walk
from intensify.spec.models import Confidence, EdgeType, NodeKind

LANGUAGE = Language(tree_sitter_go.language())

_IDENTIFIERS = frozenset(
    {"identifier", "field_identifier", "type_identifier", "package_identifier"}
)
_BRANCHES = frozenset(
    {"if_statement", "for_statement", "expression_case", "type_case", "communication_case"}
)
_LOGICAL = frozenset({"&&", "||"})
_CONTAINERS = frozenset({"slice_type", "array_type", "map_type", "channel_type"})
_SKIP_DIRS = frozenset({"vendor", "testdata"})
"""Vendored dependencies and test inputs, which the Go tool also ignores."""
_GENERATED = re.compile(rb"^// Code generated .* DO NOT EDIT\.$", re.MULTILINE)
_SENTINEL = re.compile(r"^[Ee]rr[A-Z0-9_]")
"""Go's naming convention for sentinel error variables (``ErrDeclined``)."""
_MAX_DEPTH = 8
_QUALIFIED = 2
"""Length of a ``pkg.Name`` chain."""
_MODULE_LINE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)


def is_test_path(path: str) -> bool:
    return path.endswith("_test.go")


def _skip_nested(node: Node) -> bool:
    """Function literals are not part of the enclosing function's own logic."""
    return node.type == "func_literal"


def exported(name: str) -> bool:
    return name[:1].isupper()


# -- type references ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Type:
    """A declared type: ``chain`` names it (``("pr", "Rule")``), ``depth`` counts the slices,
    arrays, maps and channels around it. Pointers are ignored."""

    chain: tuple[str, ...]
    depth: int = 0


@dataclass(frozen=True)
class _Value:
    """An expression's value: ``path`` read from ``base`` (or from the scope, if None).

    With ``call``, the last name in ``path`` is called and ``index`` picks the result.
    ``elem`` counts element steps taken by ``range``.
    """

    base: _Ref | None
    path: tuple[str, ...]
    call: bool = False
    index: int = 0
    elem: int = 0


_Ref = _Type | _Value


@dataclass
class _Call:
    scope: str
    base: _Ref | None
    path: tuple[str, ...]
    """Receiver path plus function name, or a type's chain for a composite literal."""
    construct: bool = False


@dataclass
class _Module:
    path: str
    id: str
    package: str
    """Package key: directory plus package name (``cart|cart_test``)."""
    is_test: bool
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[tuple[str | None, str]] = field(default_factory=list)
    """Alias (None for none) and import path of each named import."""
    dot_imports: list[str] = field(default_factory=list)
    calls: list[_Call] = field(default_factory=list)


@dataclass
class _TypeInfo:
    module: _Module
    interface: bool
    fields: dict[str, _Type] = field(default_factory=dict)
    embedded: list[_Type] = field(default_factory=list)
    methods: dict[str, str] = field(default_factory=dict)
    """Method name -> symbol ID."""


@dataclass
class _FuncInfo:
    module: _Module
    results: list[_Type | None]
    arity: tuple[int, int]
    error_types: list[tuple[str, _Type]] = field(default_factory=list)
    """Returned composite literals: written type (``*Error``) and its reference."""
    sentinels: list[str] = field(default_factory=list)


@dataclass
class _Package:
    dir: str
    name: str
    members: dict[str, str] = field(default_factory=dict)
    """Top-level function or type name -> symbol ID."""
    vars: dict[str, tuple[_Ref, _Module]] = field(default_factory=dict)


class GoAnalyzer:
    languages: tuple[str, ...] = ("go",)
    extensions: tuple[str, ...] = (".go",)

    def __init__(self) -> None:
        self._parser = Parser(LANGUAGE)

    def analyze(
        self, root: Path, paths: list[str], options: AnalyzeOptions, allocator: ids.Allocator
    ) -> AnalysisResult:
        result = AnalysisResult()
        trees: dict[str, Node] = {}
        for path in paths:
            if any(part in _SKIP_DIRS for part in PurePosixPath(path).parent.parts):
                continue
            source = (root / path).read_bytes()
            if _is_generated(source):
                continue
            program = self._parser.parse(source).root_node
            if program.has_error:
                result.warnings.append(f"{path}: syntax errors; results may be incomplete")
            trees[path] = program

        # Claim module IDs before any symbol IDs, so modules get the unsuffixed names.
        module_ids = {path: allocator.claim(ids.path_id(path)) for path in trees}
        state = _State(root, allocator)
        modules: list[tuple[_Module, Node]] = []
        for path, tree in trees.items():
            directory = str(PurePosixPath(path).parent)
            name = _package_name(tree)
            module = _Module(path, module_ids[path], f"{directory}|{name}", is_test_path(path))
            state.package(module.package, directory, name)
            modules.append((module, tree))
            result.files[path] = "go"
        # Types first, so methods in any file of the package find their receiver type.
        for module, tree in modules:
            state.declarations(module, tree)
        for module, tree in modules:
            state.functions(module, tree)
        for module, _ in modules:
            result.symbols.extend(module.symbols)

        resolver = _Resolver(state, [m for m, _ in modules])
        result.dependencies.extend(resolver.implements())
        test_ids = {s.id for m, _ in modules if m.is_test for s in m.symbols}
        for module, _ in modules:
            for call in module.calls:
                target = resolver.call(module, call)
                if target is None or target in test_ids:
                    continue
                if module.is_test:
                    result.dependencies.append(Dependency(module.id, target, EdgeType.VERIFIES))
                else:
                    result.dependencies.append(Dependency(call.scope, target, EdgeType.CALLS))
        resolver.add_errors(result.symbols)
        return result


def _is_generated(source: bytes) -> bool:
    """Go's convention for generated files: the marker comment before the package clause."""
    head = source.split(b"\npackage ", 1)[0] if not source.startswith(b"package ") else b""
    return bool(_GENERATED.search(head))


def _package_name(program: Node) -> str:
    for node in program.named_children:
        if node.type == "package_clause":
            return text(node.named_children[0]) if node.named_children else ""
    return ""


class _State:
    """Symbols and type facts gathered from every file, before resolution."""

    def __init__(self, root: Path, allocator: ids.Allocator) -> None:
        self.root = root
        self.allocator = allocator
        self.packages: dict[str, _Package] = {}
        self.types: dict[str, _TypeInfo] = {}
        self.funcs: dict[str, _FuncInfo] = {}
        self.symbols: dict[str, Symbol] = {}
        self.go_mods: dict[str, tuple[str, str] | None] = {}
        """Directory -> (directory of the nearest go.mod, its module path), cached."""

    def package(self, key: str, directory: str, name: str) -> None:
        self.packages.setdefault(key, _Package(directory, name))

    # -- import paths ----------------------------------------------------------------

    def import_path(self, directory: str) -> str | None:
        """Import path of the package in ``directory``, from the nearest go.mod."""
        found = self._go_mod(directory)
        if found is None:
            return None
        mod_dir, mod_path = found
        if directory == mod_dir:
            return mod_path
        rel = directory if mod_dir == "." else directory[len(mod_dir) + 1 :]
        return f"{mod_path}/{rel}"

    def _go_mod(self, directory: str) -> tuple[str, str] | None:
        if directory in self.go_mods:
            return self.go_mods[directory]
        found: tuple[str, str] | None = None
        go_mod = self.root / directory / "go.mod"
        if go_mod.is_file():
            match = _MODULE_LINE.search(go_mod.read_text(encoding="utf-8", errors="replace"))
            if match:
                found = (directory, match.group(1).strip('"'))
        if found is None and directory not in {".", ""}:
            found = self._go_mod(str(PurePosixPath(directory).parent))
        self.go_mods[directory] = found
        return found

    # -- per-file extraction ---------------------------------------------------------

    def declarations(self, module: _Module, program: Node) -> None:
        mod = Symbol(
            id=module.id,
            kind=NodeKind.module,
            language="go",
            path=module.path,
            symbol=None,
            parent=None,
            line=1,
            complexity={"loc": line_count(program)},
            fingerprint=fingerprint(program, _IDENTIFIERS),
            is_test=module.is_test,
        )
        module.symbols.append(mod)
        self.symbols[mod.id] = mod
        package = self.packages[module.package]
        for node in program.named_children:
            if node.type == "import_declaration":
                for spec in (n for n in walk(node) if n.type == "import_spec"):
                    self._import(module, spec)
            elif node.type == "type_declaration":
                for spec in node.named_children:
                    if spec.type == "type_spec":
                        self._type(module, spec, mod, package)
            elif node.type in {"var_declaration", "const_declaration"}:
                for spec in (n for n in walk(node) if n.type in {"var_spec", "const_spec"}):
                    for name, ref in _spec_refs(spec):
                        if ref is not None:
                            package.vars.setdefault(name, (ref, module))

    def _import(self, module: _Module, spec: Node) -> None:
        path = text(spec.child_by_field_name("path")).strip('"`')
        alias = spec.child_by_field_name("name")
        if alias is None:
            module.imports.append((None, path))
        elif alias.type == "dot":
            module.dot_imports.append(path)
        elif alias.type != "blank_identifier":
            module.imports.append((text(alias), path))

    def _type(self, module: _Module, spec: Node, mod: Symbol, package: _Package) -> None:
        name = text(spec.child_by_field_name("name"))
        underlying = spec.child_by_field_name("type")
        params = text(spec.child_by_field_name("type_parameters"))
        kind = underlying.type if underlying is not None else ""
        if kind == "struct_type":
            signature = f"type {name}{params} struct"
        elif kind == "interface_type":
            signature = f"type {name}{params} interface"
        else:
            signature = squash(f"type {name}{params} {text(underlying)}")
        sym = self._symbol(module, spec, mod, name, NodeKind.class_, signature)
        sym.public = exported(name)
        package.members.setdefault(name, sym.id)
        info = _TypeInfo(module, interface=kind == "interface_type")
        self.types[sym.id] = info

        if underlying is not None and kind == "struct_type":
            for decl in (n for n in walk(underlying) if n.type == "field_declaration"):
                ref = _type_ref(decl.child_by_field_name("type"))
                names = decl.children_by_field_name("name")
                if not names and ref is not None:
                    info.embedded.append(ref)
                    info.fields[ref.chain[-1]] = ref
                for field_name in names:
                    if ref is not None:
                        info.fields[text(field_name)] = ref
        elif underlying is not None and kind == "interface_type":
            for elem in underlying.named_children:
                if elem.type == "type_elem" and len(elem.named_children) == 1:
                    if (ref := _type_ref(elem.named_children[0])) is not None:
                        info.embedded.append(ref)
                elif elem.type == "method_elem":
                    method = text(elem.child_by_field_name("name"))
                    msym = self._symbol(
                        module, elem, sym, method, NodeKind.function, squash(text(elem))
                    )
                    msym.public = exported(method)
                    msym.contract = _contract(elem)
                    msym.complexity = {"cyclomatic": 1, "loc": line_count(elem)}
                    info.methods.setdefault(method, msym.id)
                    self.funcs[msym.id] = _FuncInfo(module, *_results_and_arity(elem))

    def functions(self, module: _Module, program: Node) -> None:
        mod = self.symbols[module.id]
        package = self.packages[module.package]
        for node in program.named_children:
            if node.type == "function_declaration":
                self._function(module, node, mod, package, receiver=None)
            elif node.type == "method_declaration":
                if (receiver := _receiver(node)) is not None:
                    self._function(module, node, mod, package, receiver=receiver)
            elif node.type in {"var_declaration", "const_declaration"}:
                self._collect_calls(module, node, mod.id, {}, set())

    def _function(
        self,
        module: _Module,
        node: Node,
        mod: Symbol,
        package: _Package,
        receiver: str | None,
    ) -> None:
        name = text(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        signature = header(node, body)
        parent = mod
        type_id: str | None = None
        if receiver is not None:
            type_id = package.members.get(receiver)
            if type_id is not None and type_id in self.types:
                parent = self.symbols[type_id]
                sym = self._symbol(module, node, parent, name, NodeKind.function, signature)
            else:
                # The receiver type isn't in the analyzed files (single-file analysis).
                type_id = None
                sym = self._symbol(
                    module, node, mod, f"{receiver}.{name}", NodeKind.function, signature
                )
        else:
            sym = self._symbol(module, node, mod, name, NodeKind.function, signature)
            package.members.setdefault(name, sym.id)
        sym.public = exported(name)
        sym.contract = _contract(node)
        if type_id is not None:
            self.types[type_id].methods.setdefault(name, sym.id)
        func = _FuncInfo(module, *_results_and_arity(node))
        self.funcs[sym.id] = func

        local_types, local_names = _parameter_locals(node)
        branches = 0
        if body is not None:
            for n in [body, *walk(body, prune=_skip_nested)]:
                if n.type in _BRANCHES:
                    branches += 1
                elif n.type == "binary_expression":
                    branches += text(n.child_by_field_name("operator")) in _LOGICAL
            # Calls in function literals belong to the enclosing function.
            self._collect_calls(module, body, sym.id, local_types, local_names)
            _collect_errors(body, func)
        sym.complexity = {"cyclomatic": 1 + branches, "loc": line_count(node)}

    def _symbol(
        self, module: _Module, node: Node, parent: Symbol, name: str, kind: NodeKind, signature: str
    ) -> Symbol:
        qualified = f"{parent.symbol}.{name}" if parent.symbol else name
        sym = Symbol(
            id=self.allocator.claim(ids.join(parent.id, name)),
            kind=kind,
            language="go",
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
        self.symbols[sym.id] = sym
        return sym

    def _collect_calls(
        self,
        module: _Module,
        node: Node,
        scope: str,
        local_types: dict[str, _Ref],
        local_names: set[str],
    ) -> None:
        # Declarations are visited in source order, so a variable's type is known before its uses.
        for n in [node, *walk(node)]:
            if n.type in {"short_var_declaration", "var_spec", "const_spec"}:
                for name, ref in _spec_refs(n, local_types):
                    local_names.add(name)
                    if ref is not None:
                        local_types[name] = ref
                    else:
                        local_types.pop(name, None)
            elif n.type == "range_clause":
                _declare_range(n, local_types, local_names)
            elif n.type == "type_switch_statement":
                for alias in n.children_by_field_name("alias"):
                    for ident in alias.named_children:
                        local_names.add(text(ident))
                        local_types.pop(text(ident), None)
            elif n.type == "composite_literal":
                ref = _type_ref(n.child_by_field_name("type"))
                if ref is not None and ref.depth == 0:
                    module.calls.append(_Call(scope, None, ref.chain, construct=True))
            elif n.type == "call_expression":
                path = _path(_unwrap_generic(n.child_by_field_name("function")))
                if path is None:
                    continue
                head = path[0]
                if head in local_names:
                    if head in local_types and len(path) > 1:
                        module.calls.append(_Call(scope, local_types[head], path[1:]))
                    continue
                module.calls.append(_Call(scope, None, path))


class _Resolver:
    def __init__(self, state: _State, modules: list[_Module]) -> None:
        self.state = state
        self.modules = modules
        self.by_import: dict[str, str] = {}
        """Import path -> package key (non-test package of the directory)."""
        dirs: dict[str, str] = {}
        for key, package in state.packages.items():
            if package.name.endswith("_test"):
                continue
            if package.dir not in dirs or package.name != "main":
                dirs[package.dir] = key
        for directory, key in dirs.items():
            if (path := state.import_path(directory)) is not None:
                self.by_import.setdefault(path, key)
        self.dirs = dirs
        self.import_names: dict[str, dict[str, _Package | None]] = {}
        """Module ID -> local import name -> imported package (None if outside the code)."""

    # -- packages and names ----------------------------------------------------------

    def _package_for_import(self, path: str) -> _Package | None:
        key = self.by_import.get(path)
        if key is None:
            # No go.mod: the longest directory that the import path ends with.
            matches = [d for d in self.dirs if d != "." and (path == d or path.endswith(f"/{d}"))]
            if matches:
                key = self.dirs[max(matches, key=len)]
        return self.state.packages.get(key) if key else None

    def _module_package(self, module: _Module, name: str) -> _Package | None:
        """The package an identifier names in ``module`` through an import."""
        names = self.import_names.get(module.id)
        if names is None:
            names = {}
            for alias, path in module.imports:
                package = self._package_for_import(path)
                # An unaliased import is named by the imported package clause, not its path.
                local = alias or (package.name if package else _default_import_name(path))
                names[local] = package
            self.import_names[module.id] = names
        return names.get(name)

    def _scope(self, module: _Module, name: str) -> tuple[str | None, tuple[_Ref, _Module] | None]:
        """A top-level name visible in ``module``: a member's ID or a package variable."""
        packages = [self.state.packages[module.package]]
        packages += [p for path in module.dot_imports if (p := self._package_for_import(path))]
        for package in packages:
            if name in package.members:
                return package.members[name], None
            if name in package.vars:
                return None, package.vars[name]
        return None, None

    # -- types -----------------------------------------------------------------------

    def type_id(self, module: _Module, ref: _Type) -> str | None:
        chain = ref.chain
        if len(chain) == 1:
            member, _ = self._scope(module, chain[0])
            return member if member in self.state.types else None
        if len(chain) == _QUALIFIED and (package := self._module_package(module, chain[0])):
            member = package.members.get(chain[1])
            return member if member in self.state.types else None
        return None

    def value_type(self, module: _Module, ref: _Ref, depth: int = 0) -> tuple[str, int] | None:
        """Type ID and container depth of a reference's value, if it can be followed."""
        if depth > _MAX_DEPTH:
            return None
        if isinstance(ref, _Type):
            found = self.type_id(module, ref)
            return (found, ref.depth) if found else None
        if ref.call:
            func = self.callee(module, ref.base, ref.path, depth + 1)
            info = self.state.funcs.get(func) if func else None
            if info is None or ref.index >= len(info.results):
                return None
            result = info.results[ref.index]
            current = self.value_type(info.module, result, depth + 1) if result else None
        else:
            current = self._walk(module, ref.base, ref.path, depth + 1)
        if current is None or current[1] < ref.elem:
            return None
        return current[0], current[1] - ref.elem

    def _walk(
        self, module: _Module, base: _Ref | None, path: tuple[str, ...], depth: int
    ) -> tuple[str, int] | None:
        """Type of the value at ``path`` (field reads only)."""
        rest = path
        if base is not None:
            current = self.value_type(module, base, depth)
        else:
            head, rest = path[0], path[1:]
            current = None
            if package := self._module_package(module, head):
                if not rest or rest[0] not in package.vars:
                    return None
                ref, owner = package.vars[rest[0]]
                current, rest = self.value_type(owner, ref, depth), rest[1:]
            else:
                _, var = self._scope(module, head)
                if var is not None:
                    current = self.value_type(var[1], var[0], depth)
        for name in rest:
            if current is None or current[1] != 0:
                return None
            current = self._field(current[0], name, depth)
        return current

    def _field(self, type_id: str, name: str, depth: int) -> tuple[str, int] | None:
        info = self.state.types.get(type_id)
        if info is None or depth > _MAX_DEPTH:
            return None
        if name in info.fields:
            return self.value_type(info.module, info.fields[name], depth + 1)
        for embedded in info.embedded:
            if (inner := self.type_id(info.module, embedded)) and (
                found := self._field(inner, name, depth + 1)
            ):
                return found
        return None

    def method(self, type_id: str, name: str, depth: int = 0) -> str | None:
        """A method of the type, including those promoted from embedded types."""
        info = self.state.types.get(type_id)
        if info is None or depth > _MAX_DEPTH:
            return None
        if name in info.methods:
            return info.methods[name]
        for embedded in info.embedded:
            if (inner := self.type_id(info.module, embedded)) and (
                found := self.method(inner, name, depth + 1)
            ):
                return found
        return None

    # -- calls -----------------------------------------------------------------------

    def callee(
        self, module: _Module, base: _Ref | None, path: tuple[str, ...], depth: int = 0
    ) -> str | None:
        """The function or method that calling ``path`` (from ``base``) runs."""
        *receiver, name = path
        if base is None and not receiver:
            member, _ = self._scope(module, name)
            return member if member in self.state.funcs else None
        if base is None and len(receiver) == 1 and (pkg := self._module_package(module, path[0])):
            member = pkg.members.get(name)
            return member if member in self.state.funcs else None
        owner = self._walk(module, base, tuple(receiver), depth) if receiver else None
        if base is not None and not receiver:
            owner = self.value_type(module, base, depth)
        if owner is None or owner[1] != 0:
            return None
        return self.method(owner[0], name)

    def call(self, module: _Module, call: _Call) -> str | None:
        if call.construct:
            return self.type_id(module, _Type(call.path))
        return self.callee(module, call.base, call.path)

    # -- edges and contracts ---------------------------------------------------------

    def implements(self) -> list[Dependency]:
        """Embedded types (high confidence), and interfaces a type satisfies (medium)."""
        out: list[Dependency] = []
        explicit: set[tuple[str, str]] = set()
        for type_id, info in self.state.types.items():
            for embedded in info.embedded:
                target = self.type_id(info.module, embedded)
                if target and target != type_id:
                    explicit.add((type_id, target))
                    out.append(Dependency(type_id, target, EdgeType.IMPLEMENTS))

        interfaces = {
            type_id: methods
            for type_id, info in self.state.types.items()
            if info.interface
            and not info.module.is_test
            and (methods := self._method_set(type_id)) is not None
            and methods
        }
        for type_id, info in self.state.types.items():
            if info.interface or info.module.is_test:
                continue
            methods = self._method_set(type_id)
            if not methods:
                continue
            for iface, wanted in interfaces.items():
                if (type_id, iface) in explicit:
                    continue
                if all(methods.get(name) == arity for name, arity in wanted.items()):
                    out.append(Dependency(type_id, iface, EdgeType.IMPLEMENTS, Confidence.medium))
        return out

    def _method_set(self, type_id: str, depth: int = 0) -> dict[str, tuple[int, int]] | None:
        """Method name -> (parameters, results), or None if part of it is outside the code."""
        info = self.state.types.get(type_id)
        if info is None or depth > _MAX_DEPTH:
            return None
        out: dict[str, tuple[int, int]] = {}
        for embedded in info.embedded:
            inner = self.type_id(info.module, embedded)
            inner_set = self._method_set(inner, depth + 1) if inner else None
            if inner_set is None:
                if info.interface:
                    return None  # embeds an interface we can't see, such as io.Reader
                continue
            out.update(inner_set)
        for name, method_id in info.methods.items():
            out[name] = self.state.funcs[method_id].arity
        return out

    def add_errors(self, symbols: list[Symbol]) -> None:
        """Contract errors: returned error types (with an ``Error`` method) and sentinels."""
        for sym in symbols:
            func = self.state.funcs.get(sym.id)
            if func is None:
                continue
            errors: dict[str, None] = {}
            for written, ref in func.error_types:
                found = self.type_id(func.module, ref)
                if found and self.method(found, "Error"):
                    errors[written] = None
            errors.update(dict.fromkeys(func.sentinels))
            if errors:
                sym.contract = {**(sym.contract or {}), "errors": [{"type": e} for e in errors]}


# -- syntax helpers ----------------------------------------------------------------------


def _default_import_name(path: str) -> str:
    """Name of an unaliased import: the last path element, minus ``go-`` and version suffixes."""
    parts = [p for p in path.split("/") if p]
    if len(parts) > 1 and re.fullmatch(r"v\d+", parts[-1]):
        parts.pop()
    name = parts[-1] if parts else path
    name = re.sub(r"\.v\d+$", "", name)
    name = name.removeprefix("go-").removesuffix("-go")
    return name.replace("-", "_").replace(".", "_")


def _parameter_locals(node: Node) -> tuple[dict[str, _Ref], set[str]]:
    """Names declared by a function's receiver, parameters and named results, with types."""
    local_types: dict[str, _Ref] = {}
    local_names: set[str] = set()
    result = node.child_by_field_name("result")
    lists = [node.child_by_field_name("receiver"), node.child_by_field_name("parameters")]
    if result is not None and result.type == "parameter_list":
        lists.append(result)
    for params in lists:
        for param in params.named_children if params else []:
            ref = _type_ref(param.child_by_field_name("type"))
            for name in param.children_by_field_name("name"):
                local_names.add(text(name))
                if ref is not None:
                    local_types[text(name)] = ref
    return local_types, local_names


def _receiver(node: Node) -> str | None:
    """Type name of a method's receiver."""
    params = node.child_by_field_name("receiver")
    param = params.named_children[0] if params and params.named_children else None
    if param is None:
        return None
    ref = _type_ref(param.child_by_field_name("type"))
    return ref.chain[0] if ref is not None and len(ref.chain) == 1 else None


def _type_ref(node: Node | None) -> _Type | None:
    """A named type, through pointers, generics and containers. None for other types."""
    depth = 0
    while node is not None:
        if node.type in {"pointer_type", "parenthesized_type"}:
            node = node.named_children[0] if node.named_children else None
        elif node.type in {"slice_type", "array_type", "channel_type"}:
            node, depth = (
                node.child_by_field_name("element") or node.child_by_field_name("value"),
                depth + 1,
            )
        elif node.type == "map_type":
            node, depth = node.child_by_field_name("value"), depth + 1
        elif node.type == "generic_type":
            node = node.child_by_field_name("type")
        elif node.type == "type_identifier":
            return _Type((text(node),), depth)
        elif node.type == "qualified_type":
            pkg, name = node.child_by_field_name("package"), node.child_by_field_name("name")
            return _Type((text(pkg), text(name)), depth)
        else:
            return None
    return None


def _unwrap_generic(node: Node | None) -> Node | None:
    """``Map[int]`` in ``Map[int](xs)`` is the function ``Map``."""
    while node is not None and node.type in {"index_expression", "generic_type"}:
        node = node.child_by_field_name("operand") or node.child_by_field_name("type")
    return node


def _path(node: Node | None) -> tuple[str, ...] | None:
    """``a.b.c`` as ``("a", "b", "c")``, or None if it isn't a plain dotted name."""
    if node is None:
        return None
    if node.type == "identifier":
        return (text(node),)
    if node.type == "selector_expression":
        operand = _path(node.child_by_field_name("operand"))
        name = node.child_by_field_name("field")
        return (*operand, text(name)) if operand is not None and name is not None else None
    if node.type == "parenthesized_expression" and node.named_children:
        return _path(node.named_children[0])
    return None


def _value_ref(node: Node | None, local_types: dict[str, _Ref], index: int = 0) -> _Ref | None:
    """What a variable assigned ``node`` holds, as far as it can be followed."""
    while node is not None and (
        (node.type == "unary_expression" and text(node.child_by_field_name("operator")) == "&")
        or node.type == "parenthesized_expression"
    ):
        node = node.child_by_field_name("operand") or (
            node.named_children[0] if node.named_children else None
        )
    ref: _Ref | None = None
    if node is None:
        pass
    elif node.type == "composite_literal" or (
        node.type == "type_assertion_expression" and index == 0
    ):
        ref = _type_ref(node.child_by_field_name("type"))
    elif node.type == "call_expression":
        ref = _call_ref(node, local_types, index)
    elif index == 0 and (path := _path(node)) is not None:
        if path[0] not in local_types:
            ref = _Value(None, path)
        elif len(path) == 1:
            ref = local_types[path[0]]
        else:
            ref = _Value(local_types[path[0]], path[1:])
    return ref


def _call_ref(node: Node, local_types: dict[str, _Ref], index: int) -> _Ref | None:
    """Result ``index`` of a call: ``new(T)``, or the result of a function or method."""
    function = _unwrap_generic(node.child_by_field_name("function"))
    args = node.child_by_field_name("arguments")
    if function is not None and text(function) == "new":
        return _type_ref(args.named_children[0]) if args and args.named_children else None
    path = _path(function)
    if path is None:
        return None
    if path[0] not in local_types:
        return _Value(None, path, call=True, index=index)
    if len(path) == 1:
        return None  # calling a function-typed variable
    return _Value(local_types[path[0]], path[1:], call=True, index=index)


def _spec_refs(
    spec: Node, local_types: dict[str, _Ref] | None = None
) -> list[tuple[str, _Ref | None]]:
    """Names declared by ``x := v``, ``var x T`` or ``var x = v``, with what each holds."""
    local_types = local_types or {}
    if spec.type == "short_var_declaration":
        left = spec.child_by_field_name("left")
        names = [n for n in (left.named_children if left else []) if n.type == "identifier"]
        values_node = spec.child_by_field_name("right")
        declared = None
    else:
        names = list(spec.children_by_field_name("name"))
        values_node = spec.child_by_field_name("value")
        declared = _type_ref(spec.child_by_field_name("type"))
    values = values_node.named_children if values_node else []
    out: list[tuple[str, _Ref | None]] = []
    for i, name in enumerate(names):
        if text(name) == "_":
            continue
        ref: _Ref | None = declared
        if ref is None and len(values) == len(names):
            ref = _value_ref(values[i], local_types)
        elif ref is None and len(values) == 1:
            ref = _value_ref(values[0], local_types, index=i)
        out.append((text(name), ref))
    return out


def _declare_range(clause: Node, local_types: dict[str, _Ref], local_names: set[str]) -> None:
    """``for k, v := range xs``: ``v`` gets the element type of ``xs`` when it is known."""
    left = clause.child_by_field_name("left")
    names = [n for n in (left.named_children if left else []) if n.type == "identifier"]
    for name in names:
        local_names.add(text(name))
        local_types.pop(text(name), None)
    if len(names) < _QUALIFIED or text(names[1]) == "_":  # no value variable
        return
    source = _value_ref(clause.child_by_field_name("right"), local_types)
    if isinstance(source, _Type) and source.depth > 0:
        local_types[text(names[1])] = _Type(source.chain, source.depth - 1)
    elif isinstance(source, _Value):
        local_types[text(names[1])] = _Value(
            source.base, source.path, source.call, source.index, source.elem + 1
        )


def _collect_errors(body: Node, func: _FuncInfo) -> None:
    """Returned composite literals (kept later if they are error types) and sentinel errors."""
    for n in walk(body, prune=_skip_nested):
        if n.type != "return_statement" or not n.named_children:
            continue
        for value in n.named_children[0].named_children:
            pointer, target = "", cast(Node | None, value)
            if (
                value.type == "unary_expression"
                and text(value.child_by_field_name("operator")) == "&"
            ):
                pointer, target = "*", value.child_by_field_name("operand")
            if target is not None and target.type == "composite_literal":
                type_node = target.child_by_field_name("type")
                if (ref := _type_ref(type_node)) is not None and ref.depth == 0:
                    func.error_types.append((pointer + squash(text(type_node)), ref))
            elif (
                not pointer
                and (path := _path(value)) is not None
                and len(path) <= _QUALIFIED
                and _SENTINEL.match(path[-1])
                and (sentinel := ".".join(path)) not in func.sentinels
            ):
                func.sentinels.append(sentinel)


def _results_and_arity(node: Node) -> tuple[list[_Type | None], tuple[int, int]]:
    params = node.child_by_field_name("parameters")
    n_params = sum(
        max(1, len(p.children_by_field_name("name")))
        for p in (params.named_children if params else [])
    )
    result = node.child_by_field_name("result")
    results: list[_Type | None] = []
    if result is not None and result.type == "parameter_list":
        for param in result.named_children:
            ref = _type_ref(param.child_by_field_name("type"))
            results.extend([ref] * max(1, len(param.children_by_field_name("name"))))
    elif result is not None:
        results.append(_type_ref(result))
    return results, (n_params, len(results))


def _contract(node: Node) -> dict[str, Any] | None:
    inputs: list[dict[str, Any]] = []
    params = node.child_by_field_name("parameters")
    for param in params.named_children if params else []:
        type_text = squash(text(param.child_by_field_name("type")))
        variadic = param.type == "variadic_parameter_declaration"
        names = [text(n) for n in param.children_by_field_name("name")] or ["_"]
        for name in names:
            entry: dict[str, Any] = {"name": name}
            if variadic:
                entry["type"] = f"...{type_text}"
                entry["required"] = False
            elif type_text:
                entry["type"] = type_text
            inputs.append(entry)

    outputs: list[dict[str, Any]] = []
    result = node.child_by_field_name("result")
    if result is not None and result.type == "parameter_list":
        for param in result.named_children:
            type_text = squash(text(param.child_by_field_name("type")))
            names = [text(n) for n in param.children_by_field_name("name")]
            for name in names or ["_"]:
                out: dict[str, Any] = {"name": name} if name != "_" else {}
                out["type"] = type_text
                outputs.append(out)
    elif result is not None:
        outputs.append({"type": squash(text(result))})

    contract: dict[str, Any] = {}
    if inputs:
        contract["inputs"] = inputs
    if outputs:
        contract["outputs"] = outputs
    return contract or None
