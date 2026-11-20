"""Which language server handles which files, and how to start it."""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

COMMANDS: dict[str, list[list[str]]] = {
    "python": [["basedpyright-langserver", "--stdio"], ["pyright-langserver", "--stdio"]],
    "typescript": [["typescript-language-server", "--stdio"]],
    "go": [["gopls"]],
    "java": [["jdtls"]],
    "kotlin": [["kotlin-language-server"]],
}
"""Server commands to try, in order, per server language."""

SERVER_LANGUAGE = {"javascript": "typescript"}
"""Languages that share another language's server."""

LANGUAGE_IDS = {
    ".py": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascriptreact",
    ".java": "java",
    ".kt": "kotlin",
    ".go": "go",
}
"""LSP ``languageId`` of a file, by extension."""


def server_language(language: str) -> str:
    return SERVER_LANGUAGE.get(language, language)


def language_id(path: str) -> str:
    return LANGUAGE_IDS.get(PurePosixPath(path).suffix, "plaintext")


def find(language: str, overrides: Mapping[str, Sequence[str]]) -> list[str] | None:
    """The command for ``language``'s server: an override, or the first one on ``PATH``."""
    if language in overrides:
        return list(overrides[language])
    for command in COMMANDS.get(language, []):
        if shutil.which(command[0]) is not None:
            return command
    return None
