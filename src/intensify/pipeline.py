"""Load a spec, analyze the code it describes, and merge the two in memory.

Shared by ``analyze`` (which writes the merged spec) and ``drift`` (which reports the difference).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from intensify.merge import MergeOptions, MergeReport, merge
from intensify.parser import AnalysisResult, AnalyzeOptions, analyze_repo
from intensify.spec import LoadedSpec, load_and_validate
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
