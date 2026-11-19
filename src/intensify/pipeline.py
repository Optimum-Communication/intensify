"""Load a spec, analyze the code it describes, and merge the two in memory.

Shared by ``analyze`` (which writes the merged spec) and ``drift`` (which reports the difference).
"""

from __future__ import annotations

import difflib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from intensify.merge import MergeOptions, MergeReport, merge
from intensify.parser import AnalysisResult, AnalyzeOptions, analyze_repo
from intensify.spec import LoadedSpec, load_and_validate, yamlio
from intensify.spec.models import NodeKind


class SpecInvalidError(Exception):
    """The spec has errors, so it is not safe to merge into."""

    def __init__(self, loaded: LoadedSpec) -> None:
        super().__init__(f"{loaded.path} has {len(loaded.errors)} error(s)")
        self.loaded = loaded


@dataclass
class Merged:
    loaded: LoadedSpec
    """The spec as loaded. Models reflect the file on disk; ``files[*].data`` holds the merge."""
    analysis: AnalysisResult
    report: MergeReport


def analyze_and_merge(
    spec_path: Path,
    *,
    depth: NodeKind | None = None,
    prune: bool = False,
    paths: tuple[str, ...] = (),
) -> Merged:
    loaded = load_and_validate(spec_path)
    if not loaded.ok or loaded.document is None:
        raise SpecInvalidError(loaded)
    config = loaded.document.config.analyze if loaded.document.config else None
    options = AnalyzeOptions(
        depth=depth or NodeKind((config.depth if config else None) or "function"),
        public_only=config.public_only if config else True,
    )
    analysis = analyze_repo(
        loaded.path.parent,
        options=options,
        include=config.include if config else None,
        exclude=config.exclude if config else None,
    )
    report = merge(loaded, analysis, MergeOptions(prune=prune, paths=paths))
    return Merged(loaded, analysis, report)


@dataclass
class AnalyzeOutcome:
    report: MergeReport
    analysis: AnalysisResult
    diffs: dict[Path, str] = field(default_factory=dict)
    """Unified diff per changed spec file."""
    seconds: float = 0.0


def analyze_and_write(
    spec_path: Path,
    *,
    depth: NodeKind | None = None,
    prune: bool = False,
    paths: tuple[str, ...] = (),
    dry_run: bool = False,
) -> AnalyzeOutcome:
    """Analyze and merge; write changed spec files unless ``dry_run``.

    Raises ``SpecInvalidError`` if the spec has errors.
    """
    started = time.perf_counter()
    merged = analyze_and_merge(spec_path, depth=depth, prune=prune, paths=paths)
    outcome = AnalyzeOutcome(merged.report, merged.analysis)
    for file in merged.loaded.files:
        before = file.path.read_text(encoding="utf-8")
        after = yamlio.dumps(file.data)
        if before == after:
            continue
        name = os.path.relpath(file.path)
        outcome.diffs[file.path] = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
            )
        )
        if not dry_run:
            file.path.write_text(after, encoding="utf-8")
    outcome.seconds = time.perf_counter() - started
    return outcome
