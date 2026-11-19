# Intensify

**Intensify** records the *intent* of a codebase as a typed graph in a single `intensify.yaml` file at the root of a repository. The graph covers everything from multi-repo ecosystems down to individual functions. Planned ("ghost") nodes sit in the same graph as production code, so you can design features before writing them, give AI coding agents precise scoped context, and catch architectural drift in CI.

This repository holds the open-source pieces:

- **Spec** (`spec/`): the versioned Intensify YAML specification and its JSON Schema.
- **CLI** (`src/intensify/`): `init`, `validate`, `analyze`, `drift`, `context`, `verify`, plus an MCP server.
- **AI skills** (`skills/`): enrichment (draft intent from code and git history) and node implementation for coding agents.
- **CI integration** (`action/`): a GitHub Action and a GitLab CI template for drift checks on pull requests.

> **Status:** pre-alpha. Spec v1.0, `init` and `validate` work. Everything else is in progress.

## Install

```sh
uv tool install intensify   # or: pipx install intensify
intensify --version
```

## Quick start

```sh
cd your-repo
intensify init       # writes intensify.yaml with a root node
intensify validate   # checks it against the spec
```

Then describe your system by adding nodes. Planned nodes need only an `id`, a `kind` and an `intent.summary`:

```yaml
nodes:
  - id: your-repo.billing
    kind: module
    parent_id: your-repo
    intent:
      summary: Subscription plans and invoicing.
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
