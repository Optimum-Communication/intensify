"""Machine-readable drift reports: JSON, SARIF 2.1.0, Markdown, and GitLab Code Quality."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from intensify import __version__
from intensify.differ import CATEGORY_DESCRIPTIONS, NOTE_CATEGORIES, DriftReport, Finding, Level
from intensify.differ.base import repo_prefix
from intensify.spec.models import DriftCategory

DOCS_URL = "https://github.com/Optimum-Communication/intensify/blob/main/docs/drift.md"
MARKER = "<!-- intensify-drift -->"
"""First line of the Markdown report; CI uses it to find and update its own PR comment."""


class _Paths:
    """Turns finding locations into paths relative to the repository root."""

    def __init__(self, report: DriftReport) -> None:
        self.spec_dir = report.spec_path.parent
        self.prefix = repo_prefix(self.spec_dir) or ""

    def code(self, path: str) -> str:
        return self.prefix + path

    def spec(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(self.spec_dir.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
        return self.prefix + relative

    def primary(self, finding: Finding) -> tuple[str, int | None]:
        if finding.code is not None:
            return self.code(finding.code[0]), finding.code[1]
        if finding.spec is not None:
            return self.spec(finding.spec[0]), finding.spec[1]
        return self.spec(self.spec_dir / "intensify.yaml"), None


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def to_json(report: DriftReport) -> str:
    paths = _Paths(report)
    payload = {
        "ok": report.ok,
        "base": report.base,
        "suppressed": report.suppressed,
        "files_analyzed": report.files_analyzed,
        "summary": {level.value: report.count(level) for level in Level},
        "findings": [
            {
                "category": f.category.value,
                "level": f.level.value,
                "message": f.message,
                "node": f.node,
                "code": {"path": paths.code(f.code[0]), "line": f.code[1]} if f.code else None,
                "spec": {"path": paths.spec(f.spec[0]), "line": f.spec[1]} if f.spec else None,
            }
            for f in report.findings
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def to_sarif(report: DriftReport) -> str:
    paths = _Paths(report)
    categories = list(DriftCategory)
    rules = [
        {
            "id": category.value,
            "name": "".join(part.title() for part in category.value.split("_")),
            "shortDescription": {"text": CATEGORY_DESCRIPTIONS[category]},
            "helpUri": f"{DOCS_URL}#{category.value}",
            "defaultConfiguration": {"level": _default_level(category, report).value},
        }
        for category in categories
    ]

    def location(uri: str, line: int | None) -> dict[str, Any]:
        physical: dict[str, Any] = {"artifactLocation": {"uri": uri, "uriBaseId": "%SRCROOT%"}}
        if line:
            physical["region"] = {"startLine": line}
        return {"physicalLocation": physical}

    results = []
    for f in report.findings:
        uri, line = paths.primary(f)
        result: dict[str, Any] = {
            "ruleId": f.category.value,
            "ruleIndex": categories.index(f.category),
            "level": f.level.value,
            "message": {"text": f.message},
            "locations": [location(uri, line)],
            "partialFingerprints": {"intensifyFindingKey/v1": _digest(f.key)},
        }
        if f.code is not None and f.spec is not None:
            result["relatedLocations"] = [
                {**location(paths.spec(f.spec[0]), f.spec[1]), "id": 1, "message": {"text": "spec"}}
            ]
        results.append(result)

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "intensify",
                        "version": __version__,
                        "informationUri": "https://github.com/Optimum-Communication/intensify",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2) + "\n"


def _default_level(category: DriftCategory, report: DriftReport) -> Level:
    if category in report.fail_on:
        return Level.error
    return Level.note if category in NOTE_CATEGORIES else Level.warning


def to_codequality(report: DriftReport) -> str:
    """GitLab Code Quality (Code Climate) format, shown in merge request widgets."""
    paths = _Paths(report)
    severity = {Level.error: "major", Level.warning: "minor", Level.note: "info"}
    issues = []
    for f in report.findings:
        uri, line = paths.primary(f)
        issues.append(
            {
                "type": "issue",
                "check_name": f"intensify/{f.category.value}",
                "description": f.message,
                "categories": ["Clarity"],
                "severity": severity[f.level],
                "fingerprint": _digest(f.key),
                "location": {"path": uri, "lines": {"begin": line or 1}},
            }
        )
    return json.dumps(issues, indent=2) + "\n"


_ICONS = {Level.error: "❌", Level.warning: "⚠️", Level.note: "ℹ️"}  # noqa: RUF001


def to_markdown(report: DriftReport) -> str:
    paths = _Paths(report)
    counts = {level: report.count(level) for level in Level}
    if not report.findings:
        headline = "✅ No drift"
    else:
        parts = [f"{n} {level.value}{'' if n == 1 else 's'}" for level, n in counts.items() if n]
        headline = ("❌ " if counts[Level.error] else "⚠️ ") + ", ".join(parts)
    lines = [MARKER, f"### Intensify drift: {headline}", ""]
    context = [f"{report.files_analyzed} files analyzed"]
    if report.base:
        context.append(f"compared with `{report.base}`")
    if report.suppressed:
        context.append(f"{report.suppressed} findings that already existed are hidden")
    lines += [f"<sub>{' · '.join(context)}</sub>", ""]

    by_category: dict[DriftCategory, list[Finding]] = {}
    for f in report.findings:
        by_category.setdefault(f.category, []).append(f)
    for category in DriftCategory:
        findings = by_category.get(category)
        if not findings:
            continue
        worst = min((f.level for f in findings), key=list(Level).index)
        is_open = " open" if worst is Level.error else ""
        lines.append(
            f"<details{is_open}><summary>{_ICONS[worst]} <b>{category.value}</b> "
            f"({len(findings)}): {CATEGORY_DESCRIPTIONS[category]}</summary>"
        )
        lines.append("")
        for f in findings:
            uri, line = paths.primary(f)
            where = f"{uri}:{line}" if line else uri
            lines.append(f"- `{where}` {_escape(f.message)}")
        lines += ["", "</details>", ""]
    if report.findings:
        lines.append(
            f"Run `intensify analyze` to update the spec. [About drift checks]({DOCS_URL})"
        )
    return "\n".join(lines).rstrip() + "\n"


def _escape(text: str) -> str:
    return text.replace("<", "&lt;").replace(">", "&gt;")
