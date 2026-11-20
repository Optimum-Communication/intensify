"""Spec-wide constants."""

from typing import Final

SPEC_VERSION: Final = "1.0"
SUPPORTED_SPEC_VERSIONS: Final = ("1.0",)
DEFAULT_FILENAME: Final = "intensify.yaml"
SCHEMA_URL: Final = (
    "https://raw.githubusercontent.com/Optimum-Communication/intensify/main/spec/v1.0/schema.json"
)
SPEC_DOC_URL: Final = (
    "https://github.com/Optimum-Communication/intensify/blob/main/spec/v1.0/spec.md"
)

PLACEHOLDER_SUMMARY: Final = "TODO: describe intent"
"""Intent summary written for nodes that have not been described yet.

It passes validation so ``analyze`` can add nodes, and ``drift`` and the enrich skill treat it
as "needs enrichment".
"""
