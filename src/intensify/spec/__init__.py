"""Intensify YAML spec: models generated from the schema, a round-trip loader, and validation."""

from intensify.spec.constants import (
    DEFAULT_FILENAME,
    PLACEHOLDER_SUMMARY,
    SCHEMA_URL,
    SPEC_VERSION,
)
from intensify.spec.diagnostics import Diagnostic, Severity
from intensify.spec.loader import EdgeEntry, LoadedSpec, NodeEntry, SpecFile, load
from intensify.spec.validate import load_and_validate, validate

__all__ = [
    "DEFAULT_FILENAME",
    "PLACEHOLDER_SUMMARY",
    "SCHEMA_URL",
    "SPEC_VERSION",
    "Diagnostic",
    "EdgeEntry",
    "LoadedSpec",
    "NodeEntry",
    "Severity",
    "SpecFile",
    "load",
    "load_and_validate",
    "validate",
]
