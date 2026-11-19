"""Bundled AI skills, and installing them where agents look for them."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path


class Target(StrEnum):
    claude = "claude"
    cursor = "cursor"
    plain = "plain"


DEFAULT_DIRS = {
    Target.claude: Path(".claude/skills"),
    Target.cursor: Path(".cursor/rules"),
    Target.plain: Path("intensify-skills"),
}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    """Markdown without the front matter."""
    source: str
    """The full SKILL.md text."""


def _skills_dir() -> Traversable:
    packaged = files("intensify").joinpath("_skills")  # wheels: force-included at build time
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "skills"  # editable install: the repository


def bundled() -> list[Skill]:
    out = []
    for entry in sorted(_skills_dir().iterdir(), key=lambda e: e.name):
        skill_file = entry.joinpath("SKILL.md")
        if entry.is_dir() and skill_file.is_file():
            out.append(parse(skill_file.read_text(encoding="utf-8")))
    return out


def parse(text: str) -> Skill:
    """Split ``---``-delimited front matter (``name:``, ``description:``) from the body."""
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        header, _, body = text[4:].partition("\n---\n")
        for line in header.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.strip()
    return Skill(meta.get("name", ""), meta.get("description", ""), body.lstrip("\n"), text)


def render(skill: Skill, target: Target) -> tuple[Path, str]:
    """(path relative to the target directory, file content)."""
    match target:
        case Target.claude:
            return Path(skill.name) / "SKILL.md", skill.source
        case Target.cursor:
            header = f"---\ndescription: {skill.description}\nalwaysApply: false\n---\n\n"
            return Path(f"{skill.name}.mdc"), header + skill.body
        case Target.plain:
            return Path(f"{skill.name}.md"), skill.body


def install(target: Target, directory: Path, *, force: bool = False) -> list[tuple[Path, str]]:
    """Write every skill. Returns (path, status) with status ``created``, ``updated``,
    ``unchanged`` or ``skipped`` (exists with other content and ``force`` is off)."""
    results = []
    for skill in bundled():
        relative, content = render(skill, target)
        path = directory / relative
        if path.exists():
            if path.read_text(encoding="utf-8") == content:
                results.append((path, "unchanged"))
                continue
            if not force:
                results.append((path, "skipped"))
                continue
            status = "updated"
        else:
            status = "created"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        results.append((path, status))
    return results
