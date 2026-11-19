"""Tree-sitter analyzers that extract nodes and edges from source code, keyed by language."""

from __future__ import annotations

from pathlib import Path

from intensify.parser.base import AnalysisResult, AnalyzeOptions, Analyzer, Dependency, Symbol
from intensify.parser.discovery import discover
from intensify.parser.ids import Allocator
from intensify.parser.python import PythonAnalyzer
from intensify.parser.typescript import TypeScriptAnalyzer
from intensify.spec.models import NodeKind

__all__ = ["AnalysisResult", "AnalyzeOptions", "Dependency", "Symbol", "analyze_repo"]

_KIND_RANK = {kind: rank for rank, kind in enumerate(NodeKind)}


def analyzers() -> list[Analyzer]:
    return [PythonAnalyzer(), TypeScriptAnalyzer()]


def analyze_repo(
    root: Path,
    *,
    options: AnalyzeOptions | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> AnalysisResult:
    """Analyze every supported file under ``root``.

    The whole repository is always analyzed so cross-file references resolve; callers that only
    care about some paths filter the result.
    """
    options = options or AnalyzeOptions()
    available = analyzers()
    files = discover(
        root,
        extensions=[ext for a in available for ext in a.extensions],
        include=include,
        exclude=exclude,
    )
    allocator = Allocator()
    result = AnalysisResult()
    for analyzer in available:
        paths = [f for f in files if f.endswith(analyzer.extensions)]
        if paths:
            result.extend(analyzer.analyze(root, paths, options, allocator))
    _finalize(result, options)
    return result


def _finalize(result: AnalysisResult, options: AnalyzeOptions) -> None:
    by_id = {s.id: s for s in result.symbols}

    # A module's parent is the closest module whose ID is a prefix of its own (its package).
    module_ids = {s.id for s in result.symbols if s.kind is NodeKind.module}
    for sym in result.symbols:
        if sym.kind is NodeKind.module:
            parts = sym.id.split(".")
            sym.parent = next(
                (
                    ".".join(parts[:n])
                    for n in range(len(parts) - 1, 0, -1)
                    if ".".join(parts[:n]) in module_ids
                ),
                None,
            )

    max_rank = _KIND_RANK[options.depth]
    decided: dict[str, bool] = {}

    def emitted(sym: Symbol) -> bool:
        if sym.id in decided:
            return decided[sym.id]
        decided[sym.id] = False  # guards against parent cycles
        parent = by_id.get(sym.parent) if sym.parent else None
        ok = (
            _KIND_RANK[sym.kind] <= max_rank
            and (sym.public or not options.public_only)
            and (parent is None or emitted(parent))
            # Test modules are nodes so VERIFIES edges have a source; their contents are not.
            and not (parent is not None and parent.is_test and sym.kind is not NodeKind.module)
        )
        decided[sym.id] = ok
        return ok

    for sym in result.symbols:
        sym.emit = emitted(sym)

    unique = dict.fromkeys(d for d in result.dependencies if d.source != d.target)
    result.dependencies = list(unique)


def symbol_span(root: Path, path: str, symbol: str | None) -> tuple[int, int] | None:
    """First and last line of ``symbol`` in ``path`` (the whole file for None), if it exists.

    Only the one file is parsed, so this is cheap enough to call per node.
    """
    if not (root / path).is_file():
        return None
    for analyzer in analyzers():
        if not path.endswith(analyzer.extensions):
            continue
        result = analyzer.analyze(root, [path], AnalyzeOptions(), Allocator())
        for sym in result.symbols:
            if sym.symbol == symbol:
                return sym.line, sym.line + sym.complexity.get("loc", 1) - 1
    return None
