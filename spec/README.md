# Intensify Specification

Each version of the Intensify YAML specification lives in its own directory (`v1.0/`, `v1.1/`, ...) and contains:

- `schema.json`: the JSON Schema (draft 2020-12). This is the single source of truth for the CLI models and the cloud's TypeScript types.
- `spec.md`: the human-readable specification.
- `examples/`: valid example documents. The test suite checks that each one validates.

Published versions are immutable.

| Version | Status |
|---|---|
| [v1.0](v1.0/spec.md) | Draft |
