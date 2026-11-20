# Contributing to Intensify

## Setup

```sh
uv sync
uv run pre-commit install
```

## Checks

`make check` runs everything CI runs:

| Command | What it does |
|---|---|
| `make lint` | `ruff check` and `ruff format --check` |
| `make typecheck` | `mypy` in strict mode over `src/` and `tests/` |
| `make test` | `pytest` |
| `make fmt` | Auto-fix lint issues and format code |

## Layout

| Path | Purpose |
|---|---|
| `spec/vX.Y/` | JSON Schema (single source of truth) and human-readable spec |
| `src/intensify/cli/` | Typer commands, one module per command |
| `src/intensify/spec/` | Models generated from the schema, YAML round-trip loader, validation |
| `src/intensify/graph/` | In-memory graph and traversal |
| `src/intensify/parser/` | Tree-sitter analyzers per language |
| `src/intensify/merge/` | Merges analyzed code into the spec without touching human-owned fields |
| `src/intensify/differ/` | Drift detection and reporters |
| `src/intensify/context/` | Context bundles for AI agents |
| `src/intensify/mcp/` | MCP server |
| `skills/` | AI agent skills |
| `action/` | CI integrations |
| `tests/fixtures/repos/` | Small sample repos used by golden tests |

## Schema changes

`spec/vX.Y/schema.json` is the source of truth. Never edit the generated Pydantic models by hand. Change the schema, regenerate, and commit both. Published versions of the schema are immutable, so breaking changes need a new version directory.

## Commits and pull requests

Keep pull requests focused on one change. Add or update tests for behaviour changes. CI must pass before merge.
