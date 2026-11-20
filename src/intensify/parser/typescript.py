"""TypeScript and JavaScript analyzer: ES modules, classes, interfaces, functions and calls.

CommonJS (``require`` / ``module.exports``) is not resolved: such files yield module nodes, and
their symbols count as private.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from intensify.parser import ids
from intensify.parser.base import AnalysisResult, AnalyzeOptions, Dependency, Symbol
from intensify.parser.treesitter import fingerprint, header, line_count, squash, text, walk
from intensify.spec.models import EdgeType, NodeKind

_GRAMMARS = {
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
    "javascript": Language(tree_sitter_javascript.language()),
}
_EXT_GRAMMAR = {
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}
_RESOLVE_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs")

_IDENTIFIERS = frozenset(
    {
        "identifier",
        "property_identifier",
        "type_identifier",
        "private_property_identifier",
        "shorthand_property_identifier",
        "shorthand_property_identifier_pattern",
    }
)
_FUNCTIONS = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "abstract_class_declaration",
        "class",
    }
)
_BRANCHES = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "catch_clause",
        "ternary_expression",
        "switch_case",
    }
)
_LOGICAL = frozenset({"&&", "||", "??"})
_DECLARATIONS = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "lexical_declaration",
        "variable_declaration",
    }
)

# An export resolves to a symbol ID, a local name, or a name re-exported from another file.
_Export = tuple[str, ...]


def is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name
    in_test_dir = any(p in {"__tests__", "tests", "test"} for p in pure.parent.parts)
    return in_test_dir or ".test." in name or ".spec." in name


@dataclass
class _Module:
    path: str
    id: str
    language: str
    is_test: bool
    symbols: list[Symbol] = field(default_factory=list)
    local: dict[str, str] = field(default_factory=dict)
    """Top-level declared name -> symbol ID."""
    exports: dict[str, _Export] = field(default_factory=dict)
    star_exports: list[str] = field(default_factory=list)
    """Module paths re-exported with ``export * from``."""
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Bound name -> (module path, imported name | "default" | "*")."""
    calls: list[tuple[str, list[str], str | None]] = field(default_factory=list)
    heritage: list[tuple[str, list[str]]] = field(default_factory=list)


class TypeScriptAnalyzer:
    languages: tuple[str, ...] = ("typescript", "javascript")
    extensions: tuple[str, ...] = _RESOLVE_EXTS

    def __init__(self) -> None:
        self._parsers = {name: Parser(lang) for name, lang in _GRAMMARS.items()}
        self._allocator = ids.Allocator()
        self._files: set[str] = set()
        self._tsconfig = _TsConfig()

    def analyze(
        self, root: Path, paths: list[str], options: AnalyzeOptions, allocator: ids.Allocator
    ) -> AnalysisResult:
        self._allocator = allocator
        self._files = set(paths)
        self._tsconfig = _TsConfig.load(root)
        result = AnalysisResult()
        if self._tsconfig.error:
            result.warnings.append(self._tsconfig.error)

        module_ids = {
            p: allocator.claim(ids.path_id(p, index_names=frozenset({"index"}))) for p in paths
        }
        modules: dict[str, _Module] = {}
        for path in paths:
            grammar = _EXT_GRAMMAR[PurePosixPath(path).suffix]
            tree = self._parsers[grammar].parse((root / path).read_bytes())
            if tree.root_node.has_error:
                result.warnings.append(f"{path}: syntax errors; results may be incomplete")
            language = "javascript" if grammar == "javascript" else "typescript"
            module = _Module(path, module_ids[path], language, is_test_path(path))
            self._program(module, tree.root_node)
            modules[path] = module
            result.files[path] = language
            result.symbols.extend(module.symbols)

        resolver = _Resolver(modules)
        test_ids = {s.id for m in modules.values() if m.is_test for s in m.symbols}
        for module in modules.values():
            for class_id, chain in module.heritage:
                target = resolver.resolve(module, chain, None)
                if target and target != class_id:
                    result.dependencies.append(Dependency(class_id, target, EdgeType.IMPLEMENTS))
            for scope, chain, class_ctx in module.calls:
                target = resolver.resolve(module, chain, class_ctx)
                if target is None or target in test_ids:
                    continue
                if module.is_test:
                    result.dependencies.append(Dependency(module.id, target, EdgeType.VERIFIES))
                else:
                    result.dependencies.append(Dependency(scope, target, EdgeType.CALLS))
        return result

    # -- per-file extraction -------------------------------------------------------------

    def _program(self, module: _Module, program: Node) -> None:
        mod = Symbol(
            id=module.id,
            kind=NodeKind.module,
            language=module.language,
            path=module.path,
            symbol=None,
            parent=None,
            line=1,
            complexity={"loc": line_count(program)},
            fingerprint=fingerprint(program, _IDENTIFIERS),
            is_test=module.is_test,
        )
        module.symbols.append(mod)
        exported_locals: dict[str, str] = {}

        for stmt in program.named_children:
            if stmt.type == "import_statement":
                self._import(module, stmt)
            elif stmt.type == "export_statement":
                self._export(module, stmt, mod, exported_locals)
            elif stmt.type in _DECLARATIONS:
                self._declaration(module, stmt, mod, exported=False)
            else:
                self._collect_calls(module, stmt, mod.id, None)

        # `export { local }` and `export default local` mark earlier declarations public.
        for exported_as, local in exported_locals.items():
            module.exports[exported_as] = ("local", local)
            if (sym_id := module.local.get(local)) is not None:
                self._mark_public(module, sym_id)

    def _mark_public(self, module: _Module, sym_id: str) -> None:
        for sym in module.symbols:
            if sym.id == sym_id:
                sym.public = True

    def _import(self, module: _Module, stmt: Node) -> None:
        source = self._resolve_specifier(module.path, _string(stmt.child_by_field_name("source")))
        if source is None:
            return
        clause = next((c for c in stmt.named_children if c.type == "import_clause"), None)
        for child in clause.named_children if clause else []:
            if child.type == "identifier":
                module.imports[text(child)] = (source, "default")
            elif child.type == "namespace_import":
                ident = next((c for c in child.named_children if c.type == "identifier"), None)
                if ident is not None:
                    module.imports[text(ident)] = (source, "*")
            elif child.type == "named_imports":
                for spec in child.named_children:
                    if spec.type == "import_specifier":
                        name = text(spec.child_by_field_name("name"))
                        alias = text(spec.child_by_field_name("alias")) or name
                        module.imports[alias] = (source, name)

    def _export(
        self, module: _Module, stmt: Node, mod: Symbol, exported_locals: dict[str, str]
    ) -> None:
        is_default = any(c.type == "default" for c in stmt.children)
        source_node = stmt.child_by_field_name("source")
        declaration = stmt.child_by_field_name("declaration")
        value = stmt.child_by_field_name("value")

        if declaration is not None:
            for name, sym_id in self._declaration(module, declaration, mod, exported=True):
                module.exports["default" if is_default else name] = ("id", sym_id)
            return
        if value is not None:
            if value.type == "identifier":
                exported_locals["default"] = text(value)
            elif created := self._declaration(module, value, mod, exported=True, name="default"):
                module.exports["default"] = ("id", created[0][1])
            else:
                self._collect_calls(module, value, mod.id, None)
            return

        source = None
        if source_node is not None:
            source = self._resolve_specifier(module.path, _string(source_node))
            if source is None:
                return
        clause = next((c for c in stmt.named_children if c.type == "export_clause"), None)
        if clause is None:
            if source is not None:  # export * from "./x"
                module.star_exports.append(source)
            return
        for spec in clause.named_children:
            if spec.type != "export_specifier":
                continue
            name = text(spec.child_by_field_name("name"))
            alias = text(spec.child_by_field_name("alias")) or name
            if source is not None:
                module.exports[alias] = ("reexport", source, name)
            else:
                exported_locals[alias] = name

    def _declaration(
        self,
        module: _Module,
        node: Node,
        mod: Symbol,
        *,
        exported: bool,
        name: str | None = None,
    ) -> list[tuple[str, str]]:
        """Create symbols for a top-level declaration. Returns (declared name, symbol ID) pairs."""
        kind = node.type
        if kind in {
            "function_declaration",
            "generator_function_declaration",
            "function_expression",
        }:
            sym = self._function(module, node, mod, name or text(node.child_by_field_name("name")))
            sym.public = exported
            return [(sym.name, sym.id)]
        if kind == "arrow_function" and name:
            sym = self._function(module, node, mod, name)
            sym.public = exported
            return [(name, sym.id)]
        if kind in {"class_declaration", "abstract_class_declaration", "class"}:
            # Members keep their own visibility; they are only emitted if the class is.
            sym = self._class(module, node, mod, name or text(node.child_by_field_name("name")))
            sym.public = exported
            return [(sym.name, sym.id)]
        if kind == "interface_declaration":
            sym = self._symbol(
                module,
                node,
                mod,
                text(node.child_by_field_name("name")),
                NodeKind.class_,
                header(node, node.child_by_field_name("body")),
            )
            sym.public = exported
            for clause in node.named_children:
                if clause.type == "extends_type_clause":
                    for base in clause.named_children:
                        if chain := _type_chain(base):
                            module.heritage.append((sym.id, chain))
            return [(sym.name, sym.id)]
        if kind in {"lexical_declaration", "variable_declaration"}:
            created = []
            for declarator in node.named_children:
                if declarator.type != "variable_declarator":
                    continue
                value = declarator.child_by_field_name("value")
                var_name = text(declarator.child_by_field_name("name"))
                if value is not None and value.type in {
                    "arrow_function",
                    "function_expression",
                    "generator_function",
                }:
                    keyword = text(node.child_by_field_name("kind")) or text(node.children[0])
                    sym = self._function(module, value, mod, var_name, signature_node=declarator)
                    sym.signature = f"{keyword} {sym.signature}"
                    sym.public = exported
                    created.append((var_name, sym.id))
                elif value is not None:
                    self._collect_calls(module, value, mod.id, None)
            return created
        return []

    def _class(self, module: _Module, node: Node, parent: Symbol, name: str) -> Symbol:
        body = node.child_by_field_name("body")
        sym = self._symbol(module, node, parent, name, NodeKind.class_, header(node, body))
        for heritage in node.named_children:
            if heritage.type != "class_heritage":
                continue
            for clause in heritage.named_children:
                if clause.type == "extends_clause":
                    targets = [clause.child_by_field_name("value")]
                elif clause.type == "implements_clause":
                    targets = list(clause.named_children)
                else:  # JavaScript: `class A extends B` has the expression directly
                    targets = [clause]
                for target in targets:
                    if chain := _type_chain(target):
                        module.heritage.append((sym.id, chain))
        for member in body.named_children if body else []:
            if member.type != "method_definition":
                continue
            if any(c.type == "set" for c in member.children):
                continue  # a setter shares its getter's name
            method_name = text(member.child_by_field_name("name"))
            method = self._function(module, member, sym, method_name, class_ctx=sym.id)
            modifiers = {text(c) for c in member.children if c.type == "accessibility_modifier"}
            method.public = not method_name.startswith("#") and "private" not in modifiers
        return sym

    def _function(
        self,
        module: _Module,
        node: Node,
        parent: Symbol,
        name: str,
        *,
        class_ctx: str | None = None,
        signature_node: Node | None = None,
    ) -> Symbol:
        body = node.child_by_field_name("body")
        signature = header(signature_node or node, body)
        sym = self._symbol(module, node, parent, name, NodeKind.function, signature)
        sym.contract = _contract(node)
        branches = 0
        if body is not None:
            for n in [body, *walk(body, prune=lambda x: x.type in _FUNCTIONS)]:
                if n.type in _BRANCHES:
                    branches += 1
                elif n.type == "binary_expression":
                    branches += text(n.child_by_field_name("operator")) in _LOGICAL
            self._collect_calls(module, body, sym.id, class_ctx)
        sym.complexity = {"cyclomatic": 1 + branches, "loc": line_count(node)}
        return sym

    def _symbol(
        self, module: _Module, node: Node, parent: Symbol, name: str, kind: NodeKind, signature: str
    ) -> Symbol:
        qualified = f"{parent.symbol}.{name}" if parent.symbol else name
        sym = Symbol(
            id=self._allocator.claim(ids.join(module.id, qualified)),
            kind=kind,
            language=module.language,
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
        if parent.kind is NodeKind.module:
            module.local[name] = sym.id
        return sym

    def _collect_calls(
        self, module: _Module, node: Node, scope: str, class_ctx: str | None
    ) -> None:
        for n in [node, *walk(node)]:
            if n.type == "call_expression":
                callee = n.child_by_field_name("function")
            elif n.type == "new_expression":
                callee = n.child_by_field_name("constructor")
            else:
                continue
            if chain := _chain(callee):
                module.calls.append((scope, chain, class_ctx))

    # -- module resolution ---------------------------------------------------------------

    def _resolve_specifier(self, from_path: str, spec: str) -> str | None:
        if not spec:
            return None
        if spec.startswith((".", "/")):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
            return self._find_file(base)
        for candidate in self._tsconfig.candidates(spec):
            if found := self._find_file(candidate):
                return found
        return None  # a package from node_modules, or unresolvable

    def _find_file(self, base: str) -> str | None:
        base = base.removeprefix("./")
        if base.startswith("..") or base.startswith("/"):
            return None
        stems = [base]
        if base.endswith(_JS_EXTS):  # TS projects import "./x.js" for "./x.ts"
            stems.append(base.rsplit(".", 1)[0])
        for stem in stems:
            for candidate in [stem, *(stem + e for e in _RESOLVE_EXTS)]:
                if candidate in self._files:
                    return candidate
            for ext in _RESOLVE_EXTS:
                if (candidate := f"{stem}/index{ext}") in self._files:
                    return candidate
        return None


class _Resolver:
    def __init__(self, modules: dict[str, _Module]) -> None:
        self.modules = modules
        self.members: dict[str, dict[str, str]] = {}
        for module in modules.values():
            for sym in module.symbols:
                if sym.kind is NodeKind.function and sym.parent and "." in (sym.symbol or ""):
                    self.members.setdefault(sym.parent, {})[sym.name] = sym.id

    def export(self, path: str, name: str, seen: frozenset[str] = frozenset()) -> str | None:
        module = self.modules.get(path)
        if module is None or path in seen:
            return None
        seen = seen | {path}
        entry = module.exports.get(name)
        if entry is not None:
            match entry:
                case ("id", sym_id):
                    return sym_id
                case ("local", local):
                    return module.local.get(local)
                case ("reexport", source, original):
                    return self.export(source, original, seen)
        if name != "default":
            for star in module.star_exports:
                if found := self.export(star, name, seen):
                    return found
        return None

    def resolve(self, module: _Module, chain: list[str], class_ctx: str | None) -> str | None:
        head, rest = chain[0], chain[1:]
        target: str | None
        if head == "this":
            if class_ctx and len(rest) == 1:
                return self.members.get(class_ctx, {}).get(rest[0])
            return None
        if head in module.local:
            target = module.local[head]
        elif head in module.imports:
            source, name = module.imports[head]
            if name == "*":
                if not rest:
                    return None
                target, rest = self.export(source, rest[0]), rest[1:]
            else:
                target = self.export(source, name)
        else:
            return None
        if target is None or not rest:
            return target
        return self.members.get(target, {}).get(rest[0]) if len(rest) == 1 else None


@dataclass
class _TsConfig:
    base_url: str | None = None
    paths: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def load(cls, root: Path) -> _TsConfig:
        for name in ("tsconfig.json", "jsconfig.json"):
            file = root / name
            if not file.is_file():
                continue
            try:
                data = json.loads(_strip_jsonc(file.read_text(encoding="utf-8")))
            except (ValueError, OSError) as exc:
                return cls(error=f"{name}: could not parse ({exc}); path aliases are ignored")
            options = data.get("compilerOptions", {}) if isinstance(data, dict) else {}
            base_url = options.get("baseUrl")
            paths = options.get("paths") or {}
            return cls(
                base_url=posixpath.normpath(base_url) if isinstance(base_url, str) else None,
                paths={k: list(v) for k, v in paths.items() if isinstance(v, list)},
            )
        return cls()

    def candidates(self, spec: str) -> list[str]:
        base = self.base_url or "."
        out: list[str] = []
        for pattern, targets in self.paths.items():
            prefix, star, suffix = pattern.partition("*")
            if star and spec.startswith(prefix) and spec.endswith(suffix):
                captured = spec[len(prefix) : len(spec) - len(suffix) if suffix else None]
                out.extend(posixpath.join(base, t.replace("*", captured)) for t in targets)
            elif not star and spec == pattern:
                out.extend(posixpath.join(base, t) for t in targets)
        if self.base_url is not None:
            out.append(posixpath.join(base, spec))
        return [posixpath.normpath(c) for c in out]


_JSONC = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.DOTALL)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _strip_jsonc(source: str) -> str:
    """tsconfig files allow comments and trailing commas; plain JSON does not."""
    no_comments = _JSONC.sub(lambda m: m.group(1) or "", source)
    return _TRAILING_COMMA.sub(r"\1", no_comments)


def _string(node: Node | None) -> str:
    if node is None:
        return ""
    fragment = next((c for c in node.named_children if c.type == "string_fragment"), None)
    return text(fragment)


def _chain(node: Node | None) -> list[str] | None:
    if node is None:
        return None
    if node.type in {"identifier", "this"}:
        return [text(node)]
    if node.type == "member_expression":
        obj = _chain(node.child_by_field_name("object"))
        prop = node.child_by_field_name("property")
        return [*obj, text(prop)] if obj is not None and prop is not None else None
    return None


def _type_chain(node: Node | None) -> list[str] | None:
    if node is None:
        return None
    if node.type == "type_identifier":
        return [text(node)]
    if node.type == "nested_type_identifier":
        return text(node).split(".")
    if node.type == "generic_type":
        return _type_chain(node.child_by_field_name("name"))
    return _chain(node)


def _contract(node: Node) -> dict[str, Any] | None:
    params = node.child_by_field_name("parameters")
    if params is None and (single := node.child_by_field_name("parameter")) is not None:
        inputs = [{"name": text(single)}]  # `x => ...`
    else:
        inputs = [p for c in (params.named_children if params else []) if (p := _parameter(c))]
    contract: dict[str, Any] = {}
    if inputs:
        contract["inputs"] = inputs
    returns = text(node.child_by_field_name("return_type")).lstrip(":").strip()
    if returns and returns != "void":
        contract["outputs"] = [{"type": returns}]
    body = node.child_by_field_name("body")
    errors: dict[str, None] = {}
    for n in walk(body, prune=lambda x: x.type in _FUNCTIONS) if body is not None else []:
        if n.type == "throw_statement" and n.named_children:
            thrown = n.named_children[0]
            if thrown.type == "new_expression":
                thrown = thrown.child_by_field_name("constructor") or thrown
            chain = _chain(thrown)
            if chain and chain[-1][:1].isupper():
                errors[".".join(chain)] = None
    if errors:
        contract["errors"] = [{"type": e} for e in errors]
    return contract or None


def _parameter(param: Node) -> dict[str, Any] | None:
    kind = param.type
    entry: dict[str, Any]
    if kind in {"required_parameter", "optional_parameter"}:
        pattern = param.child_by_field_name("pattern")
        name = squash(text(pattern))
        if name == "this":
            return None
        entry = {"name": name}
        type_text = text(param.child_by_field_name("type")).lstrip(":").strip()
        if type_text:
            entry["type"] = type_text
        optional = (
            kind == "optional_parameter"
            or param.child_by_field_name("value") is not None
            or (pattern is not None and pattern.type == "rest_pattern")
        )
    elif kind == "identifier":
        return {"name": text(param)}
    elif kind == "assignment_pattern":
        entry = {"name": squash(text(param.child_by_field_name("left")))}
        optional = True
    elif kind in {"rest_pattern", "object_pattern", "array_pattern"}:
        entry = {"name": squash(text(param))}
        optional = kind == "rest_pattern"
    else:
        return None
    if optional:
        entry["required"] = False
    return entry
