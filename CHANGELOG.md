# Changelog

## 0.1.0 (unreleased)

First public release.

- **Spec v1.0:** JSON Schema, specification document and examples. Covers nodes from ecosystems to blocks, typed edges, lifecycle states, contracts, governance and machine-checkable invariants, and fragment files.
- **`intensify init` / `validate`:** starter specs, and validation with located, readable diagnostics.
- **`intensify analyze`:** Python, TypeScript and JavaScript analysis with import-resolved calls, inheritance and test links. The merge is comment-preserving and follows renames and moves, and it never touches human-owned fields.
- **`intensify drift`:** structural, contract and invariant drift, with `--base` to report only new drift. Reports as text, JSON, SARIF, Markdown and GitLab Code Quality. Comes with a GitHub Action and a GitLab CI template.
- **`intensify node`:** validated, comment-preserving reads and edits of single nodes.
- **`intensify context` / `verify`:** context bundles for coding agents, and verification that marks implemented nodes active.
- **AI skills** (`intensify-enrich`, `intensify-implement-node`) and **`intensify skills install`** for Claude Code, Cursor and plain Markdown.
- **`intensify mcp`:** MCP server with search, context, edit, analyze, verify and drift tools.
- **Intensify Cloud sync:**
  - `login` / `logout` / `whoami`, using the OAuth device flow.
  - `project create|link|status|token`.
  - `sync push|pull`. Pull is a three-way merge, and conflicts are reported.
  - The public sync API contract is in `spec/sync-api/v1/openapi.yaml`.
