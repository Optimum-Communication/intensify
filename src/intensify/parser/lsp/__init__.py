"""Resolve the calls static analysis couldn't, by asking each language's server.

The analyzers record a ``CallSite`` for every call they couldn't resolve, and for every call to
an overloaded method. For each one, the language server's ``textDocument/definition`` gives the
file and range of the target. A target becomes an edge when it lands on the name of a symbol in
the analyzed code. Targets outside the repository (the standard library, dependencies) are
dropped. Answers come from a type checker, so these edges are ``high`` confidence.
"""

from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from intensify.parser.base import AnalysisResult, CallSite, Dependency, Symbol, innermost
from intensify.parser.discovery import MAX_FILE_BYTES
from intensify.parser.lsp import servers
from intensify.parser.lsp.client import TIMEOUT, Client, LspError
from intensify.spec.models import NodeKind

__all__ = ["LspOptions", "resolve"]

_WINDOW = 64
"""Definition requests in flight at once."""
_NAME = re.compile(r"[\w$]+")
_CONSTRUCTORS = frozenset({"constructor", "__init__", "init"})


@dataclass(frozen=True)
class LspOptions:
    languages: frozenset[str] | None = None
    """Languages to look up (``python``, ``go``, ...), or None for every one with a server."""
    commands: Mapping[str, Sequence[str]] = field(default_factory=dict)
    """Server command per server language, instead of searching ``PATH``."""
    timeout: float = 120.0
    """Budget for the whole pass, including server start-up."""
    request_timeout: float = 15.0


def resolve(root: Path, result: AnalysisResult, options: LspOptions) -> set[CallSite]:
    """Add edges for the call sites a server resolves; returns the resolved sites."""
    deadline = time.monotonic() + options.timeout
    groups: dict[str, list[CallSite]] = defaultdict(list)
    for site in result.call_sites:
        language = result.files.get(site.path)
        if language is None or (
            options.languages is not None and language not in options.languages
        ):
            continue
        groups[servers.server_language(language)].append(site)

    lookup = _Symbols(root, result.symbols)
    resolved: set[CallSite] = set()
    for language, sites in sorted(groups.items()):
        command = servers.find(language, options.commands)
        if command is None:
            result.warnings.append(
                f"lsp: no server for {language} on PATH; using static resolution"
            )
            continue
        try:
            answers = _ask(root, command, sites, options, deadline)
        except LspError as exc:
            result.warnings.append(
                f"lsp: {language} server failed ({exc}); using static resolution"
            )
            continue
        answered = 0
        for site, answer in answers.items():
            if answer is TIMEOUT:
                continue
            answered += 1
            target = lookup.target(answer)
            if target is None or target.is_test or target.id == site.source:
                continue
            result.dependencies.append(Dependency(site.source, target.id, site.type))
            resolved.add(site)
        if answered < len(sites):
            result.warnings.append(
                f"lsp: {language} server answered {answered} of {len(sites)} calls in time"
            )
    return resolved


def _ask(
    root: Path,
    command: Sequence[str],
    sites: list[CallSite],
    options: LspOptions,
    deadline: float,
) -> dict[CallSite, Any]:
    """The server's answer for each site (``TIMEOUT`` for those it didn't answer)."""
    with Client(command, root) as client:
        client.initialize(root, min(options.request_timeout * 4, deadline - time.monotonic()))
        sources: dict[str, list[bytes]] = {}
        for path in sorted({s.path for s in sites}):
            file = root / path
            if not file.is_file() or file.stat().st_size > MAX_FILE_BYTES:
                continue
            data = file.read_bytes()
            sources[path] = data.splitlines()
            client.open(_uri(file), servers.language_id(path), data.decode("utf-8", "replace"))

        answers: dict[CallSite, Any] = {}
        queue = [s for s in sites if s.path in sources]
        pending: list[tuple[CallSite, int]] = []
        while queue or pending:
            while queue and len(pending) < _WINDOW:
                site = queue.pop(0)
                lines = sources[site.path]
                line = lines[site.line - 1] if site.line - 1 < len(lines) else b""
                request = client.definition(
                    _uri(root / site.path), site.line - 1, _utf16_column(line, site.column)
                )
                pending.append((site, request))
            site, request = pending.pop(0)
            until = min(time.monotonic() + options.request_timeout, deadline)
            answers[site] = client.wait(request, until)
            if time.monotonic() >= deadline:
                break
        for site in sites:
            answers.setdefault(site, TIMEOUT)
        return answers


class _Symbols:
    """Maps a definition location back to the symbol it names."""

    def __init__(self, root: Path, symbols: list[Symbol]) -> None:
        self.root = os.path.realpath(root)
        self.by_path: dict[str, list[Symbol]] = defaultdict(list)
        self.by_id = {s.id: s for s in symbols}
        for sym in symbols:
            self.by_path[sym.path].append(sym)
        self.lines: dict[str, list[str]] = {}

    def target(self, answer: Any) -> Symbol | None:
        for uri, line, character in _locations(answer):
            path = self._relative(uri)
            if path is None or path not in self.by_path:
                continue  # outside the repository, or not analyzed
            name = self._name_at(path, line, character)
            found = self._symbol(path, line + 1, name)
            if found is not None:
                return found
        return None

    def _relative(self, uri: str) -> str | None:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return None
        path = os.path.realpath(unquote(parsed.path))
        if not path.startswith(self.root + os.sep):
            return None
        return Path(path).relative_to(self.root).as_posix()

    def _name_at(self, path: str, line: int, character: int) -> str:
        if path not in self.lines:
            file = Path(self.root) / path
            source = file.read_text(encoding="utf-8", errors="replace") if file.is_file() else ""
            self.lines[path] = source.splitlines()
        lines = self.lines[path]
        if line >= len(lines):
            return ""
        text = lines[line]
        index = len(text.encode("utf-16-le")[: character * 2].decode("utf-16-le", "replace"))
        match = _NAME.match(text, index)
        return match.group(0) if match else ""

    def _symbol(self, path: str, line: int, name: str) -> Symbol | None:
        """The symbol declared by ``name`` at ``line``: the innermost one with that name."""
        candidates = list(self.by_path[path])
        while (sym := innermost(candidates, line)) is not None:
            parent = self.by_id.get(sym.parent) if sym.parent else None
            if sym.name == name:
                # A constructor stands for its class, as in static analysis: Java and Kotlin
                # name it after the class, TypeScript and Python use a fixed name.
                if (
                    sym.kind is NodeKind.function
                    and parent is not None
                    and parent.kind is NodeKind.class_
                    and name in {parent.name, *_CONSTRUCTORS}
                ):
                    return parent
                return sym
            if name in _CONSTRUCTORS and sym.kind is NodeKind.class_:
                return sym
            if sym.kind is NodeKind.function and name in _CONSTRUCTORS and parent is not None:
                return parent if parent.kind is NodeKind.class_ else None
            candidates.remove(sym)
        return None


def _locations(answer: Any) -> list[tuple[str, int, int]]:
    """``(uri, line, character)`` of a Location, a list of them, or LocationLinks."""
    items = answer if isinstance(answer, list) else [answer] if answer else []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = item.get("targetUri") or item.get("uri")
        span = item.get("targetSelectionRange") or item.get("range")
        if isinstance(uri, str) and isinstance(span, dict) and isinstance(span.get("start"), dict):
            start = span["start"]
            out.append((uri, int(start.get("line", 0)), int(start.get("character", 0))))
    return out


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def _utf16_column(line: bytes, byte_column: int) -> int:
    prefix = line[:byte_column].decode("utf-8", errors="replace")
    return len(prefix.encode("utf-16-le")) // 2
