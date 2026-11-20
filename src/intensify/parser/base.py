"""Language-neutral results of static analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from intensify.parser.ids import Allocator
from intensify.spec.models import Confidence, EdgeType, NodeKind


@dataclass(frozen=True)
class AnalyzeOptions:
    depth: NodeKind = NodeKind.function
    """Finest kind of node to emit. Finer symbols are still extracted, for matching."""
    public_only: bool = True


@dataclass
class Symbol:
    """A module, class or function found in code."""

    id: str
    """Derived node ID. Only used for new nodes; existing nodes keep their own IDs."""
    kind: NodeKind
    language: str
    path: str
    """POSIX path relative to the repository root."""
    symbol: str | None
    """Dotted path within the file (``Cart.add``), or None for a module."""
    parent: str | None
    """Derived ID of the containing symbol, or None for a top-level module."""
    line: int
    signature: str | None = None
    contract: dict[str, Any] | None = None
    """Plain data matching the spec's Contract schema."""
    complexity: dict[str, int] = field(default_factory=dict)
    fingerprint: str | None = None
    public: bool = True
    is_test: bool = False
    emit: bool = True
    """Whether a new node should be created. Non-emitted symbols can still match existing nodes."""

    @property
    def name(self) -> str:
        return (self.symbol or self.path).rsplit(".", 1)[-1]

    def technical(self) -> dict[str, Any]:
        location: dict[str, str] = {"path": self.path}
        if self.symbol:
            location["symbol"] = self.symbol
        out: dict[str, Any] = {"language": self.language, "location": location}
        if self.signature:
            out["signature"] = self.signature
        if self.complexity:
            out["complexity"] = dict(self.complexity)
        if self.fingerprint:
            out["fingerprint"] = self.fingerprint
        return out


@dataclass(frozen=True)
class Dependency:
    """An edge between two symbols, by derived ID."""

    source: str
    target: str
    type: EdgeType
    confidence: Confidence = Confidence.high


@dataclass
class AnalysisResult:
    symbols: list[Symbol] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    """Analyzed file path -> language."""
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: AnalysisResult) -> None:
        self.symbols.extend(other.symbols)
        self.dependencies.extend(other.dependencies)
        self.files.update(other.files)
        self.warnings.extend(other.warnings)


class Analyzer(Protocol):
    languages: tuple[str, ...]
    extensions: tuple[str, ...]

    def analyze(
        self, root: Path, paths: list[str], options: AnalyzeOptions, allocator: Allocator
    ) -> AnalysisResult:
        """Analyze ``paths`` (POSIX, relative to ``root``) together, resolving cross-file links.

        Every symbol ID must come from ``allocator`` so IDs stay unique across languages.
        """
        ...
