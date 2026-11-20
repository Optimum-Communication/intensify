# Intensify

**Intensify** records the *intent* of a codebase as a typed graph in a single `intensify.yaml` file at the root of a repository. The graph covers everything from multi-repo ecosystems down to individual functions. Planned ("ghost") nodes sit in the same graph as production code, so you can design features before writing them, give AI coding agents precise scoped context, and catch architectural drift in CI.

This repository holds the open-source pieces:

- **Spec** (`spec/`): the versioned Intensify YAML specification and its JSON Schema.
- **CLI** (`src/intensify/`): `init`, `validate`, `analyze`, `drift`, `context`, `verify`, plus an MCP server.
- **AI skills** (`skills/`): enrichment (draft intent from code and git history) and node implementation for coding agents.
- **CI integration** (`action/`): a GitHub Action and a GitLab CI template for drift checks on pull requests.

> **Status:** alpha (v0.1). The spec, the CLI (`init`, `validate`, `analyze` for Python and TypeScript/JavaScript, `drift`, `node`, `context`, `verify`), CI integrations, AI skills and the MCP server all work. Intensify checks itself: see its own [`intensify.yaml`](intensify.yaml).

## Install

```sh
uv tool install intensify   # or: pipx install intensify
intensify --version
```

## Quick start

```sh
cd your-repo
intensify init --analyze   # writes intensify.yaml, populated from your code
intensify validate         # checks it against the spec
```

`analyze` adds a node for each module, class and function, with a placeholder intent for you (or the enrichment skill) to fill in. Re-run `intensify analyze` after code changes: it follows renames and moves and never overwrites what you wrote. See [docs/analyze.md](docs/analyze.md).

To keep the spec honest, run `intensify drift` in CI: it fails pull requests that add undocumented dependencies, break a contract or an invariant, or delete documented code. See [docs/drift.md](docs/drift.md) and the [GitHub Action](action/README.md).

To plan new work, add planned nodes. They need only an `id`, a `kind` and an `intent.summary`:

```yaml
nodes:
  - id: your-repo.billing
    kind: module
    parent_id: your-repo
    intent:
      summary: Subscription plans and invoicing.
```

Then point a coding agent at the node: `intensify context` gives it the node's intent, contract, invariants and surroundings, and `intensify verify` checks the result and marks the node active. The bundled skills and the MCP server package this up for Claude Code, Cursor and other agents. See [docs/agents.md](docs/agents.md).

```sh
intensify node add your-repo.billing.invoices --kind module --parent your-repo.billing \
  --summary "Generate monthly invoices."
intensify context your-repo.billing.invoices
intensify skills install          # agent skills for Claude Code (or --target cursor)
claude mcp add intensify -- intensify mcp
```

Read the [spec](spec/v1.0/spec.md) and its [examples](spec/v1.0/examples/).

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync                     # create .venv and install dev dependencies
uv run pre-commit install   # run checks on every commit
make check                  # lint, format check, type check, tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

[Apache-2.0](LICENSE)
