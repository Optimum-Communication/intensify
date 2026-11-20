"""Structural validation against the packaged JSON Schema.

The schema in ``schemas/`` is a copy of ``spec/vX.Y/schema.json`` kept in sync by ``make gen``.
"""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from intensify.spec import yamlio
from intensify.spec.diagnostics import Diagnostic, Severity

_PATTERN_HINTS: dict[str, str] = {
    "NodeId": "dot-separated segments of letters, digits, '_', '$' or '-' "
    "(e.g. payments.refunds.issue_refund)",
    "NodeRef": "a node ID, optionally prefixed with '<project-id>:'",
    "ProjectId": "lowercase letters, digits, '_' or '-', starting with a letter or digit",
}
_FINGERPRINT_HINT = "'sha256:' followed by 64 lowercase hex characters"


@cache
def load_schema(version: str = "1.0") -> dict[str, Any]:
    text = files("intensify.spec").joinpath("schemas", f"v{version}.json").read_text("utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


@cache
def _validators(version: str) -> tuple[Draft202012Validator, Draft202012Validator]:
    schema = load_schema(version)
    registry: Registry[Any] = Registry().with_resource(
        schema["$id"], Resource.from_contents(schema)
    )
    document = Draft202012Validator(schema, registry=registry)
    fragment = Draft202012Validator({"$ref": f"{schema['$id']}#/$defs/Fragment"}, registry=registry)
    return document, fragment


@cache
def _pattern_hints(version: str) -> dict[str, str]:
    defs = load_schema(version)["$defs"]
    hints = {defs[name]["pattern"]: hint for name, hint in _PATTERN_HINTS.items()}
    hints[defs["Technical"]["properties"]["fingerprint"]["pattern"]] = _FINGERPRINT_HINT
    return hints


def check(data: Any, file: Path, *, fragment: bool, version: str = "1.0") -> list[Diagnostic]:
    """Validate round-trip ``data`` (one file) against the document or fragment schema."""
    document_validator, fragment_validator = _validators(version)
    validator = fragment_validator if fragment else document_validator
    plain = yamlio.to_plain(data)
    diagnostics = []
    for error in validator.iter_errors(plain):
        path, message = _describe(error, version)
        diagnostics.append(
            Diagnostic(
                severity=Severity.error,
                code="schema",
                message=message,
                file=file,
                path=path,
                line=yamlio.line_of(data, path),
            )
        )
    return sorted(diagnostics, key=lambda d: (d.line or 0, d.pointer))


def _describe(error: ValidationError, version: str) -> tuple[tuple[str | int, ...], str]:
    path: tuple[str | int, ...] = tuple(error.absolute_path)
    instance = error.instance
    schema = error.schema if isinstance(error.schema, dict) else {}

    match error.validator:
        case "additionalProperties" if isinstance(instance, dict):
            allowed = set(schema.get("properties", {}))
            extras = sorted(k for k in instance if k not in allowed)
            if extras:
                known = ", ".join(sorted(allowed))
                names = ", ".join(repr(k) for k in extras)
                return (*path, extras[0]), f"unknown field {names} (allowed: {known})"
        case "pattern":
            hint = _pattern_hints(version).get(str(error.validator_value))
            if hint:
                return path, f"{instance!r} is not valid: expected {hint}"
        case "minLength" if error.validator_value == 1:
            return path, "must not be empty"
        case "minProperties" | "maxProperties" if "properties" in schema:
            options = ", ".join(schema["properties"])
            return path, f"must contain exactly one of: {options}"
    return path, error.message
