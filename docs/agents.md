# Working with AI coding agents

Intensify gives coding agents what they usually lack: what a piece of code is *for*, the rules it must keep, the code around it, and the work planned nearby. It also gives them a way to prove they built the right thing.

The loop:

1. **Plan:** add a `planned` node with an intent, and optionally a contract and invariants.
2. **Context:** `intensify context <id>` gives the agent everything relevant to that node.
3. **Build:** the agent writes the code and its tests.
4. **Record:** `intensify analyze` adds the code to the spec.
5. **Verify:** `intensify verify <id>` checks the contract, invariants and tests, then marks the node `active`.

Agents can drive this through the CLI (any agent that runs shell commands), the bundled [skills](#skills), or the [MCP server](#mcp-server).

## Editing nodes: `intensify node`

Agents (and people) should never hand-edit `intensify.yaml`. These commands keep comments and formatting, validate every change, and roll the change back if it would make the spec invalid.

```sh
intensify node list --state planned             # work waiting to be done
intensify node list refund webhook              # search IDs, names and intent
intensify node list --unenriched                # nodes still needing an intent
intensify node get payments.refunds             # one node, as YAML (or --format json)

intensify node add payments.refunds.audit_log --kind function \
  --parent payments.refunds --summary "Record every refund decision for finance."

intensify node set payments.refunds --author ai \
  --summary "Full and partial refunds of settled charges." \
  --rationale "Kept separate from charges so refunds can be audited on their own."
intensify node set payments.refunds --invariant 'idempotent=Refunding twice creates one refund.'
intensify node set payments.refunds --json '{"contract": {"inputs": [{"name": "charge_id", "type": "str"}]}}'
```

`--json` deep-merges an object into the node. Mappings merge, anything else replaces, and `null` removes a field. `--author ai` marks intent as a draft for a person to review.

## Context: `intensify context`

```sh
intensify context payments.refunds.issue_refund                  # Markdown
intensify context payments.refunds.issue_refund --include-source # with the code
intensify context payments.refunds.issue_refund --budget 2000    # fit ~2000 tokens
intensify context payments.refunds.issue_refund --format json
```

The bundle contains:

| Section | What's in it |
|---|---|
| Intent, Contract, Location | What to build, its interface, and where it lives (or where it should go). These are never trimmed. |
| Invariants | Rules the node must keep, including those inherited from parent modules, and the machine-checked form if there is one. |
| Depends on / Used by | Direct dependencies and dependents, with signatures and intent. |
| Future capabilities | Planned and in-progress work connected to the node: planned children and siblings, nodes it `BLOCKS`, prerequisites that block it, and planned edges. The agent is told to leave room for them but not to build them. |
| Where this fits | The chain of containing nodes, up to the repository. |
| Tests | Tests that verify the node, and `config.verify.test_command`. |
| Code | With `--include-source`: the node's code and its dependencies' code. |
| When you're done | The `analyze` and `verify` steps. |

With `--budget`, the lowest-priority sections are dropped first: dependency code, then the node's own code, then tests, ancestors, future work, dependents and dependencies.

## Verify: `intensify verify`

```sh
intensify verify payments.refunds.issue_refund
intensify verify payments.refunds.issue_refund --dry-run      # check only
intensify verify payments.refunds.issue_refund --skip-tests   # don't run the test command
intensify verify payments.refunds.issue_refund --allow-untested
```

Checks, in order:

1. **Code exists:** `analyze` can find code for the node, by location, ID or a move.
2. **Contract matches:** the same comparison as [drift's `contract_mismatch`](drift.md#contract_mismatch).
3. **Invariants hold:** no checked invariant is broken by an edge into or out of the node or its children.
4. **Has tests:** at least one test module has a `VERIFIES` edge to the node or one of its children. Tests get one by importing and calling the code directly.
5. **Tests pass:** if `config.verify.test_command` is set, it runs with `{tests}` replaced by the test files. The command runs directly, not through a shell, so set environment variables with `env`:

   ```yaml
   config:
     verify:
       test_command: "env PYTHONPATH=src pytest -q {tests}"
   ```

When every check passes, `verify` writes the analysis of the node's file to the spec and marks the node `active`. Exit codes: 0 verified, 1 a check failed, 2 the spec is invalid or the node doesn't exist.

## Skills

Two skills ship with the CLI:

- **`intensify-enrich`** drafts intent for nodes `analyze` added. It works top-down, reads code, tests and git history, cites its evidence, records invariants, and marks everything `author: ai` for review.
- **`intensify-implement-node`** builds a planned node from its context, tests it, then runs `analyze` and `verify` until it passes.

```sh
intensify skills install                   # Claude Code: .claude/skills/
intensify skills install --target cursor   # Cursor: .cursor/rules/
intensify skills install --target plain --dir prompts/
```

Run this from the repository root and commit the result, so everyone's agent gets the skills.

## MCP server

`intensify mcp` serves the graph over the Model Context Protocol (stdio). Start it from the repository root, or pass `--spec path/to/intensify.yaml`.

| Tool | Does |
|---|---|
| `search_nodes` | Search by words, kind, state and tag. |
| `get_node` | One node, with its children and edges. |
| `get_context` | The `intensify context` bundle as Markdown (with source by default). |
| `list_planned` | Planned and in-progress nodes. |
| `update_intent` | Set summary and rationale (author `ai` by default). |
| `add_planned_node` | Add a planned node, optionally with a contract. |
| `analyze_code` | Run `intensify analyze`, optionally for some paths only. |
| `verify_node` | Run `intensify verify`. |
| `drift_report` | Drift findings as JSON. |

The resource `intensify://spec` is the root `intensify.yaml`.

**Claude Code:**

```sh
claude mcp add --scope project intensify -- intensify mcp
```

**Cursor** (`.cursor/mcp.json`) and **Windsurf** (`~/.codeium/windsurf/mcp_config.json`):

```json
{ "mcpServers": { "intensify": { "command": "intensify", "args": ["mcp"] } } }
```

**VS Code** (`.vscode/mcp.json`):

```json
{ "servers": { "intensify": { "type": "stdio", "command": "intensify", "args": ["mcp", "--spec", "${workspaceFolder}"] } } }
```
