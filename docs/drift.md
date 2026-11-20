# `intensify drift`

`drift` checks that the code still matches `intensify.yaml`. Run it in CI on every pull request: it fails when the code gains a dependency the spec doesn't record, a signature stops matching its contract, an invariant is broken, or documented code disappears.

```sh
intensify drift                         # check the working tree
intensify drift --base origin/main      # only report drift this branch introduced
intensify drift --fail-on missing_node  # override which categories fail
intensify drift -v                      # include notes
intensify drift --sarif drift.sarif --markdown drift.md   # also write reports
intensify drift --format json           # machine-readable output on stdout
```

| Exit code | Meaning |
|---|---|
| 0 | No blocking drift (warnings and notes may still be reported). |
| 1 | Blocking drift: a finding in a `fail_on` category. |
| 2 | The check couldn't run: the spec is invalid, or `--base` can't be read. |

`drift` runs the same analysis as [`intensify analyze`](analyze.md) but writes nothing. It reports what `analyze` would change, then checks contracts and invariants. Fixing most drift means running `intensify analyze` and committing the updated spec, then describing anything new.

## What counts as drift

These change with almost every edit and are **never** drift: `complexity`, `fingerprint`, `signature`, and line numbers. Drift is about structure, dependencies and contracts.

By default, `undocumented_edge`, `contract_mismatch`, `invariant_violation` and `missing_node` fail the check (`config.drift.fail_on`). `moved_node` and `unenriched_node` are notes, and every other category is a warning. Any category listed in `fail_on` fails the check, including the note categories.

### `undocumented_node`

Code exists that the spec has no node for, such as a new function or module. Only code that `analyze` would add counts, so depth and `public_only` apply. Edges to and from the new code are not reported separately. **Fix:** run `intensify analyze`, then describe the new node's intent.

### `missing_node`

A node's `technical.location` points at code that no longer exists. Planned nodes are exempt, because they may name a file that hasn't been written yet. Deprecated nodes are exempt too. **Fix:** run `intensify analyze --prune` to mark the node deprecated, or restore the code.

### `undocumented_edge`

The code has a dependency (`CALLS`, `IMPLEMENTS`, `VERIFIES`) between documented nodes that the spec doesn't record. This is the main signal of architectural drift: a new coupling nobody planned. **Fix:** if the dependency is intended, run `intensify analyze` to record it. If it isn't, remove it from the code.

### `stale_edge`

The spec records an `analyzed` edge that the code no longer has. Hand-written edges are never stale, because people may record dependencies the analyzer can't see. **Fix:** run `intensify analyze`.

### `contract_mismatch`

The code's signature differs from the node's `contract`:

- **Hand-written contracts** (nodes without `provenance: analyzed`) are compared on what they declare. That means input names and order, input types and whether each input is required, and the return type. Error modes are not compared, because the analyzer only sees exceptions raised directly in the function body. A field the contract leaves out is not compared.
- **Analyzed contracts** are compared in full. A mismatch means the signature changed and the spec wasn't refreshed. **Fix:** run `intensify analyze`.

### `invariant_violation`

An edge breaks a machine-checkable invariant (`governance.invariants[].rule`). Rules are checked against the spec's edges plus the edges the code has now, so new couplings are caught before they're recorded. Planned edges count too, which catches a *plan* that would break an invariant. See [spec §2.7](../spec/v1.0/spec.md#27-governance) for the rule types.

### `moved_node`

Code moved or was renamed, and the spec still has the old location. This is a note: the node keeps its ID and `analyze` updates the location.

### `unenriched_node`

A node still has the placeholder intent `"TODO: describe intent"`. This is a note by default. Add it to `fail_on` once a codebase is fully described.

## Only new drift: `--base`

On an existing codebase, the first drift check usually finds plenty. `--base <git-ref>` runs the check at that revision too and hides findings that already existed there, so a pull request is only blamed for what it introduces.

Each finding has a stable key (category plus the node, edge or code location involved), so a finding counts as "existing" even after unrelated lines move. If the spec didn't exist at the base revision, or was invalid there, nothing is hidden.

## Reports

| Option | Format | Use |
|---|---|---|
| `--sarif FILE` | SARIF 2.1.0 | GitHub code scanning: annotations on the pull request diff. |
| `--markdown FILE` | Markdown | Job summaries and pull request comments. Starts with `<!-- intensify-drift -->`. |
| `--codequality FILE` | GitLab Code Quality | The merge request Code Quality widget. |
| `--format json` | JSON | Scripts. |

Locations are relative to the repository root. Findings point at the code where there is code, such as the function that gained a call. Otherwise they point at the spec: the node, edge or invariant.

## GitHub Actions

```yaml
name: Intensify
on: pull_request

permissions:
  contents: read
  security-events: write   # code scanning annotations
  pull-requests: write     # only needed with `comment: true`

jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: Optimum-Communication/intensify/action@main
        with:
          comment: true
```

See [`action/README.md`](../action/README.md) for every input.

## GitLab CI

```yaml
include:
  - remote: https://raw.githubusercontent.com/Optimum-Communication/intensify/main/action/gitlab/intensify-drift.gitlab-ci.yml
```

Findings appear in the merge request's Code Quality widget.
