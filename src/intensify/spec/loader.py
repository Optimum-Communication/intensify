"""Load ``intensify.yaml`` and its included fragment files.

Every file keeps its round-trip YAML data so later commands can write changes back to the file a
node came from without disturbing comments or formatting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml.error import MarkedYAMLError, YAMLError

from intensify.spec import schema, yamlio
from intensify.spec.constants import DEFAULT_FILENAME, SUPPORTED_SPEC_VERSIONS
from intensify.spec.diagnostics import Diagnostic, Severity
from intensify.spec.models import Edge, IntensifyDocument, IntensifyFragment, Node


@dataclass
class SpecFile:
    path: Path
    data: Any
    """Round-trip YAML data. Edit this, then write it back with ``yamlio.write``."""
    is_root: bool


@dataclass(frozen=True)
class NodeEntry:
    node: Node
    file: SpecFile
    index: int
    """Position in the file's ``nodes`` list."""


@dataclass(frozen=True)
class EdgeEntry:
    edge: Edge
    file: SpecFile
    index: int


@dataclass
class LoadedSpec:
    path: Path
    """The root ``intensify.yaml``."""
    document: IntensifyDocument | None = None
    """The root file's model. ``None`` if the root file failed to load or validate."""
    files: list[SpecFile] = field(default_factory=list)
    nodes: list[NodeEntry] = field(default_factory=list)
    """Nodes from the root file followed by each included file, in file order."""
    edges: list[EdgeEntry] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.error]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.warning]

    @property
    def ok(self) -> bool:
        return not self.errors


def resolve_path(path: Path) -> Path:
    """Accept either a spec file or a directory containing ``intensify.yaml``."""
    return path / DEFAULT_FILENAME if path.is_dir() else path


def load(path: Path) -> LoadedSpec:
    """Load and structurally validate a spec. Semantic checks live in ``spec.validate``."""
    root_path = resolve_path(path)
    result = LoadedSpec(path=root_path)

    root = _load_file(root_path, is_root=True, result=result)
    if root is None:
        return result
    try:
        result.document = IntensifyDocument.model_validate(yamlio.to_plain(root.data))
    except ValidationError as exc:
        result.diagnostics.extend(_model_errors(exc, root))
        return result
    _collect(result, root, result.document.nodes, result.document.edges)

    for fragment_path in _expand_includes(result.document.includes or [], root_path, result):
        fragment = _load_file(fragment_path, is_root=False, result=result)
        if fragment is None:
            continue
        try:
            model = IntensifyFragment.model_validate(yamlio.to_plain(fragment.data))
        except ValidationError as exc:
            result.diagnostics.extend(_model_errors(exc, fragment))
            continue
        _collect(result, fragment, model.nodes, model.edges)
    return result


def _load_file(path: Path, *, is_root: bool, result: LoadedSpec) -> SpecFile | None:
    def fail(code: str, message: str, line: int | None = None) -> None:
        result.diagnostics.append(Diagnostic(Severity.error, code, message, path, line=line))

    try:
        data = yamlio.read(path)
    except FileNotFoundError:
        hint = " (run `intensify init` to create one)" if is_root else ""
        fail("file-not-found", f"file not found{hint}")
        return None
    except (MarkedYAMLError, YAMLError) as exc:
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or str(exc)
        fail("yaml-syntax", f"invalid YAML: {problem}", mark.line + 1 if mark else None)
        return None

    if not isinstance(data, dict):
        fail("not-a-mapping", "expected a YAML mapping at the top level")
        return None

    version = data.get("intensify")
    if version is not None and str(version) not in SUPPORTED_SPEC_VERSIONS:
        supported = ", ".join(SUPPORTED_SPEC_VERSIONS)
        fail(
            "unsupported-version",
            f"spec version {version!r} is not supported by this CLI (supported: {supported})",
            yamlio.line_of(data, ["intensify"]),
        )
        return None

    errors = schema.check(data, path, fragment=not is_root)
    result.files.append(spec_file := SpecFile(path=path, data=data, is_root=is_root))
    if errors:
        result.diagnostics.extend(errors)
        return None
    return spec_file


def _expand_includes(patterns: list[str], root_path: Path, result: LoadedSpec) -> list[Path]:
    base = root_path.parent
    found: dict[Path, None] = {}
    for index, pattern in enumerate(patterns):
        location: dict[str, Any] = {"file": root_path, "path": ("includes", index)}
        location["line"] = yamlio.line_of(result.files[0].data, location["path"])
        if Path(pattern).is_absolute():
            result.diagnostics.append(
                Diagnostic(
                    Severity.error, "invalid-include", "include must be relative", **location
                )
            )
            continue
        matches = sorted(p for p in base.glob(pattern) if p.is_file())
        matches = [p for p in matches if p.resolve() != root_path.resolve()]
        if not matches:
            result.diagnostics.append(
                Diagnostic(
                    Severity.warning,
                    "include-no-match",
                    f"include pattern {pattern!r} matched no files",
                    **location,
                )
            )
        found.update(dict.fromkeys(matches))
    return list(found)


def _collect(
    result: LoadedSpec, file: SpecFile, nodes: list[Node] | None, edges: list[Edge] | None
) -> None:
    result.nodes.extend(NodeEntry(n, file, i) for i, n in enumerate(nodes or []))
    result.edges.extend(EdgeEntry(e, file, i) for i, e in enumerate(edges or []))


def _model_errors(exc: ValidationError, file: SpecFile) -> list[Diagnostic]:
    # Only reachable if the Pydantic models drift from the JSON Schema; reported, never raised.
    out = []
    for err in exc.errors():
        path = tuple(err["loc"])
        out.append(
            Diagnostic(
                Severity.error,
                "model",
                err["msg"],
                file.path,
                path,
                yamlio.line_of(file.data, path),
            )
        )
    return out
