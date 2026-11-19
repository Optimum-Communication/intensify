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
| `make gen` | Regenerate the Pydantic models and packaged schema from `spec/` |
| `make check-gen` | Fail if generated files are stale (runs in CI) |

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
| `tests/fixtures/repos/` | Small sample repos the analyzers run against (not project code: excluded from ruff, mypy and pytest collection) |
| `tests/golden/` | Exact `intensify.yaml` output of `init --analyze` per fixture repo |

## Schema changes

`spec/vX.Y/schema.json` is the source of truth. `make gen` derives two files from it:

- `src/intensify/spec/models.py`: Pydantic models, generated with datamodel-code-generator.
- `src/intensify/spec/schemas/vX.Y.json`: a copy of the schema shipped inside the package for validation.

Never edit either by hand. Change the schema, run `make gen`, and commit all three. Keep `spec.md` and `examples/` in step: the tests validate every example. Published versions of the schema are immutable, so breaking changes need a new version directory.

## Golden files

`tests/unit/test_golden.py` compares `init --analyze` on each fixture repo with `tests/golden/`. After a deliberate change to analyzer output, regenerate them and review the diff:

```sh
INTENSIFY_UPDATE_GOLDEN=1 uv run pytest tests/unit/test_golden.py
git diff tests/golden/
```

## Commits and pull requests

Keep pull requests focused on one change. Add or update tests for behaviour changes. CI must pass before merge.
