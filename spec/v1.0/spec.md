# Intensify Specification v1.0

An Intensify spec records the **intent** of a codebase as a graph. **Nodes** are the parts of the system, from whole ecosystems down to blocks of code. **Edges** are typed relationships between them. Planned nodes ("ghost nodes") sit in the same graph as running code, so one file describes both what exists and what is coming.

The machine-readable definition is [`schema.json`](schema.json) (JSON Schema draft 2020-12). If this document and the schema disagree, the schema wins. Worked examples are in [`examples/`](examples/).

## 1. The file

Each repository has one root spec file, `intensify.yaml`, at its root. Large repositories can split nodes and edges into [fragment files](#6-fragment-files).

To get autocomplete and inline validation in editors that use the YAML language server (VS Code, JetBrains, Neovim), start the file with:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Optimum-Communication/intensify/main/spec/v1.0/schema.json
```

`intensify init` writes this line for you.

### Top-level fields

| Field | Required | Description |
|---|---|---|
| `intensify` | yes | Spec version. Must be `"1.0"` (quoted, so YAML doesn't read it as a number). |
| `project` | yes | `id` (required), `name`, `description`. The `id` is lowercase letters, digits, `_` and `-`, and must be unique within your organization. |
| `config` | no | CLI settings. See [Config](#7-config). |
| `includes` | no | Glob patterns for [fragment files](#6-fragment-files). |
| `nodes` | no | List of [nodes](#2-nodes). |
| `edges` | no | List of [edges](#4-edges). |

Unknown fields are errors at every level. This catches typos such as `parnet_id`.

## 2. Nodes

```yaml
- id: payments.refunds.issue_refund
  kind: function
  parent_id: payments.refunds
  lifecycle_state: active
  intent:
    summary: Issue a full or partial refund against a settled charge.
    rationale: Must be idempotent because the payment provider retries webhooks.
  contract: {...}
  technical: {...}
  governance: {...}
  provenance: analyzed
```

| Field | Required | Default | Description |
|---|---|---|---|
| `id` | yes | | Stable identifier. See [Node IDs](#21-node-ids). |
| `kind` | yes | | `ecosystem`, `repo`, `module`, `class`, `function` or `block`. |
| `intent` | yes | | Why the node exists. See [Intent](#23-intent). |
| `name` | no | | Display name, if the ID isn't readable enough. |
| `parent_id` | no | | ID of the containing node. |
| `lifecycle_state` | no | `planned` | `planned`, `in_progress`, `active` or `deprecated`. |
| `contract` | no | | Interface: inputs, outputs and error modes. |
| `technical` | no | | Language, location, signature, complexity, fingerprint. |
| `governance` | no | | Owners, tags and invariants. |
| `provenance` | no | `manual` | Who created the node: `analyzed`, `manual` or `cloud`. |

Only `id`, `kind` and `intent.summary` are required. A planned node can be one line of intent, and you add detail as the design firms up.

### 2.1 Node IDs

An ID is one or more dot-separated segments. Each segment is made of letters, digits, `_`, `$` or `-`, for example `payments.refunds.issue_refund`. IDs are unique within a project.

**IDs are stable.** Once assigned, an ID never changes, even when the code is moved, renamed or rewritten in another language. The node's current place in the code is recorded in `technical.location`, and only that changes. This lets history, links and cross-repo references survive refactoring.

The analyzer derives an ID from the module path and symbol name when it first sees a symbol, then never regenerates it.

### 2.2 Kinds and containment

Kinds go from coarsest to finest:

`ecosystem` → `repo` → `module` → `class` → `function` → `block`

`parent_id` expresses containment. A node's parent must be the **same kind or coarser**, so a module can contain modules (packages) and a function can contain functions (closures). A function can never contain a module.

Containment is only expressed through `parent_id`. There is no `CONTAINS` edge type.

### 2.3 Intent

| Field | Required | Description |
|---|---|---|
| `summary` | yes | What the node does, in a sentence or two. |
| `rationale` | no | Why it exists, or why it's built this way. Constraints, history, trade-offs. |
| `author` | no | `human` (default) or `ai`. `ai` marks a draft that a person should review. |

The summary `"TODO: describe intent"` is the placeholder that `intensify analyze` writes for newly discovered code. It's valid. `intensify drift` reports these nodes as `unenriched_node`, and the enrichment skill fills them in.

### 2.4 Lifecycle states

| State | Meaning |
|---|---|
| `planned` | Designed but not built. The default, so hand-written nodes start as plans. |
| `in_progress` | Code exists but hasn't been verified against the contract yet. |
| `active` | Implemented and verified. |
| `deprecated` | Still present, but being phased out. Don't build new dependencies on it. |

`active` nodes of kind `module`, `class`, `function` or `block` must have `technical.location`, because an active node has to point at real code. `ecosystem` and `repo` nodes don't need one.

### 2.5 Contract

Optional while planning. It describes the node's interface in the node's own language.

```yaml
contract:
  inputs:
    - {name: charge_id, type: str}
    - {name: amount, type: "Decimal | None", required: false, description: Omit for a full refund.}
  outputs:
    - {type: Refund}
  errors:
    - {type: ChargeNotSettled, when: The charge has not settled yet.}
```

- **`inputs`:** `name` (required), `type`, `required` (default `true`), `description`.
- **`outputs`:** `name`, `type`, `description`. All are optional, because most functions return one unnamed value.
- **`errors`:** `type` (required), `when`.

### 2.6 Technical

Usually maintained by `intensify analyze`.

| Field | Description |
|---|---|
| `language` | e.g. `python`, `typescript`, `java`. |
| `location.path` | File path relative to the repository root, with `/` separators. Required inside `location`. |
| `location.symbol` | Symbol in the file, e.g. `RefundRepository.save`. Omit for module nodes. |
| `signature` | The declaration as written in code. |
| `complexity` | `cyclomatic` (≥ 1) and `loc` (≥ 0). |
| `fingerprint` | `sha256:` followed by 64 hex characters. A hash of the normalized code structure, used to recognize a node after it moves or is renamed. |

### 2.7 Governance

```yaml
governance:
  owners: ["@payments-team"]
  tags: [domain:billing, tier:1]
  invariants:
    - id: no-direct-db
      text: Refund code goes through RefundRepository and never writes to the database directly.
      rule:
        forbid_edge: {type: WRITES_TO, to: "payments.db.*"}
```

- **`owners`:** free-form strings, usually team or user handles.
- **`tags`:** free-form. The convention is `key:value`.
- **`invariants`:** rules the node must always satisfy. `id` (lowercase, unique on the node) and `text` (for people) are required. The optional `rule` is a machine-checkable form that `intensify drift` enforces. It has exactly one of:

| Rule | Fields | Fails when |
|---|---|---|
| `forbid_edge` | `to` (glob over target refs), `type` (optional) | The node or any descendant has a matching outgoing edge. |
| `require_edge` | `to`, `type` (optional) | Neither the node nor any descendant has a matching outgoing edge. |
| `allowed_dependents` | `from` (list of globs), `type` (optional) | A node outside `from` has an edge into the node or a descendant. |

Globs use `*` for any characters (including dots) and `?` for one character.

## 3. Provenance

`provenance` records who created a node or edge:

- **`analyzed`:** `intensify analyze` created it from code. The analyzer may update or remove it.
- **`manual`:** written by a person (the default).
- **`cloud`:** created in the Intensify Cloud planning studio and pulled down with `intensify sync pull`.

### Field ownership

`intensify analyze` updates the spec from code without ever overwriting what people wrote:

| Field | Owner | What `analyze` does |
|---|---|---|
| `id`, `intent`, `governance` | People | Never changes them. |
| `lifecycle_state` | People | Only moves `planned` to `in_progress` when matching code appears. `verify` moves nodes to `active`. |
| `technical` | Analyzer | Overwrites it freely. |
| `contract` on `analyzed` nodes | Analyzer | Overwrites it freely. |
| `contract` on other nodes | People | Leaves it alone. A mismatch with the code is reported as drift. |
| `analyzed` edges | Analyzer | Adds and removes them to match the code. |
| Other edges | People | Never changes them. |

When code disappears, `analyze` reports the node and leaves it in place. With `--prune`, it marks the node `deprecated`. It never deletes nodes. Planned nodes are exempt, because their `technical.location` may name a file that doesn't exist yet.

## 4. Edges

```yaml
edges:
  - {from: payments.refunds.issue_refund, to: payments.refunds.repository, type: CALLS}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `from` | yes | | Source node ref. |
| `to` | yes | | Target node ref. |
| `type` | yes | | See below. |
| `description` | no | | Free text. |
| `lifecycle_state` | no | `active` | A planned edge is a dependency that doesn't exist yet. |
| `confidence` | no | `high` | `high`, `medium` or `low`. The analyzer marks call edges it can't fully resolve as `low`. |
| `provenance` | no | `manual` | As for nodes. |

### Edge types

| Type | Meaning (`from` → `to`) |
|---|---|
| `CALLS` | `from` invokes `to` at runtime. A node may call itself (recursion). |
| `READS_FROM` | `from` reads data owned by `to` (a table, bucket, topic, or the module that owns it). |
| `WRITES_TO` | `from` writes data owned by `to`. |
| `IMPLEMENTS` | `from` implements the interface or abstraction `to`. |
| `VERIFIES` | `from` (a test) verifies `to`. |
| `BLOCKS` | `to` can't be built until `from` exists. A planning dependency. |

Each `(from, to, type)` combination may appear only once. Apart from `CALLS`, a node can't have an edge to itself.

### Cross-project references

A ref is either a local node ID, or `<project-id>:<node-id>` for a node in another project:

```yaml
- {from: payments.refunds.issue_refund, to: "ledger:ledger.entries.post", type: CALLS}
```

Quote refs that contain `:` to keep YAML happy. Local validation checks that local endpoints exist and skips references to other projects. Intensify Cloud resolves those across repositories. A ref that names your own project, such as `payments:payments.refunds`, is treated as local.

## 5. Planning

Planned nodes and edges live alongside active ones. A typical progression:

1. Add a node with `id`, `kind`, `parent_id` and `intent.summary`. It's `planned` by default.
2. Add `rationale`, a `contract`, invariants and `BLOCKS` edges as the design firms up.
3. Implement it. `intensify analyze` matches the new code and moves the node to `in_progress`.
4. `intensify verify` checks the code against the contract and moves the node to `active`.

See [`examples/planning.yaml`](examples/planning.yaml).

## 6. Fragment files

`includes` lists glob patterns relative to the root file, for example `[".intensify/*.yaml"]`. Each matched file is a **fragment**: it has `intensify` (the version) and optionally `nodes` and `edges`, and nothing else. Fragments can't include other files.

The root file and every fragment are validated as one graph. IDs must be unique across all of them, and edges and parents may cross files. Patterns that match nothing produce a warning, and the root file is never included in itself. See [`examples/split/`](examples/split/).

## 7. Config

All settings are optional.

```yaml
config:
  analyze:
    include: ["src/**"]          # default: all files not ignored by git
    exclude: ["src/**/migrations/**"]
    depth: function              # finest kind to create: module | class | function
    public_only: true            # skip private symbols
  drift:
    fail_on: [undocumented_edge, contract_mismatch, invariant_violation, missing_node]
    ignore_low_confidence: true  # ignore analyzer edges marked confidence: low
  verify:
    test_command: "pytest {tests}"  # {tests} is replaced with the node's VERIFIES test files
```

Drift categories are `undocumented_node`, `missing_node`, `undocumented_edge`, `stale_edge`, `contract_mismatch`, `invariant_violation`, `moved_node` and `unenriched_node`. Categories not in `fail_on` are reported as warnings, except `moved_node` and `unenriched_node`, which are notes. See [`docs/drift.md`](../../docs/drift.md).

## 8. Validation

`intensify validate` checks the schema first, then these rules across all files:

| Code | Rule |
|---|---|
| `duplicate-node-id` | Node IDs are unique across the root file and all fragments. |
| `unknown-parent` | `parent_id` refers to an existing node. |
| `containment-cycle` | `parent_id` links don't form a cycle. |
| `invalid-nesting` | A parent is the same kind as its child, or coarser. |
| `missing-location` | Active `module`, `class`, `function` and `block` nodes have `technical.location`. |
| `duplicate-invariant-id` | Invariant IDs are unique within a node. |
| `unknown-edge-endpoint` | Local edge endpoints exist. |
| `self-edge` | Only `CALLS` edges may point from a node to itself. |
| `duplicate-edge` | Each `(from, to, type)` appears once. |

Schema violations are reported with the code `schema`. File-level problems are `file-not-found`, `yaml-syntax`, `not-a-mapping` and `unsupported-version`. Include problems are `invalid-include` (error) and `include-no-match` (warning).

## 9. Formatting conventions

Tools edit `intensify.yaml` with a round-trip YAML library, which keeps comments, key order and quoting. Files in this style come back from a tool edit byte-for-byte unchanged:

- 2-space indentation for mappings.
- List items indented 2 spaces under their key (`nodes:` then `  - id: ...`).
- Flow collections without inner padding: `{from: a, to: b}`, not `{ from: a, to: b }`.

Other styles are still valid, but a tool edit may normalize them.

## 10. Versioning

The spec version is `MAJOR.MINOR`. A minor version only adds optional fields, so a v1.0 document is a valid v1.1 document. A major version can make breaking changes, and `intensify migrate` will convert documents between them. Published versions of `schema.json` never change.
