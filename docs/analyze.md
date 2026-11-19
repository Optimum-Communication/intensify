# `intensify analyze`

`analyze` reads your source code and keeps `intensify.yaml` in step with it. It adds nodes for new code, refreshes technical details, follows code that moves, and records dependencies as edges. It never changes what people wrote.

```sh
intensify init --analyze           # new spec, populated from code
intensify analyze                  # refresh an existing spec
intensify analyze --dry-run        # show the diff, write nothing
intensify analyze --paths src/api  # only touch nodes for these files or directories
intensify analyze --depth module   # create module nodes only
intensify analyze --prune          # mark nodes whose code is gone as deprecated
```

Run it as often as you like. A second run with no code changes writes nothing.

## What it extracts

| Language | Files | Nodes | Edges |
|---|---|---|---|
| Python | `.py` | modules (packages from `__init__.py`), classes, functions and methods | `CALLS`, `IMPLEMENTS` (base classes), `VERIFIES` |
| TypeScript / JavaScript | `.ts` `.tsx` `.mts` `.cts` `.js` `.jsx` `.mjs` `.cjs` | modules, classes, interfaces (as `class`), functions, `const` arrow functions, methods | `CALLS` (including `new`), `IMPLEMENTS` (`extends` / `implements`), `VERIFIES` |

Each node gets `technical.language`, `location`, `signature`, `complexity` (cyclomatic complexity and lines) and a `fingerprint`. Functions also get a `contract` built from their parameters, return type and the exception types they raise or throw.

`READS_FROM` and `WRITES_TO` aren't inferred, because static analysis can't detect them reliably. Add them by hand.

### Which files

`analyze` reads every file git doesn't ignore. Outside a git repository it reads the root `.gitignore` instead. It always skips `node_modules`, virtualenvs, build output, `.d.ts` files, minified files and anything over 1 MB. Narrow it further with `config.analyze.include` and `exclude` (gitignore-style globs).

### Which symbols become nodes

- **Depth:** `config.analyze.depth` (or `--depth`) sets the finest kind to create: `module`, `class` or `function`.
- **Public only:** with `config.analyze.public_only: true` (the default), private symbols are skipped. In Python that means names starting with `_`, except `__init__` and `__call__`, and anything inside a private module. In TypeScript and JavaScript it means declarations that aren't exported, plus `private` and `#private` members.
- **Tests:** test files (`test_*.py`, `*_test.py`, `*.test.ts`, `*.spec.ts`, and anything under `tests/`, `test/` or `__tests__/`) become module nodes, but their functions don't. A `VERIFIES` edge goes from the test module to each symbol it calls.

Skipped symbols are still analyzed. If you document one by hand, `analyze` keeps its technical details up to date.

### How calls are resolved

Calls are resolved through the import graph, so every edge points at a real symbol:

- **Python:** `import`, `from ... import` (including relative imports and aliases), module attributes (`pricing.subtotal()`), `self.method()` and `cls.method()` (including inherited methods), and class instantiation.
- **TypeScript and JavaScript:** named, default and namespace imports. Re-exports (`export { x } from`, `export * from`), `index` files, `.js` specifiers that point at `.ts` files, and `tsconfig.json` / `jsconfig.json` `baseUrl` and `paths` aliases. Also `this.method()` and `new Class()`.

Calls that can't be resolved this way produce no edge. That includes calls on objects whose type is only known at runtime, calls into third-party packages, and CommonJS `require`. A call into private code attaches to the nearest node in the same file. It is dropped if there's no such node, or if the result would be an edge between a node and its own ancestor.

## IDs

A new node's ID comes from its module path and symbol name:

| Code | ID |
|---|---|
| `src/shop/cart.py` (package `shop`), `class Cart`, `def add` | `shop.cart.Cart.add` |
| `tests/test_cart.py` (not in a package) | `tests.test_cart` |
| `src/util/index.ts` | `util` |
| `src/cart.test.ts` | `cart-test` |

A leading `src/` or `lib/` is dropped. Characters an ID can't contain become `-`. Collisions get a numeric suffix (`shop.cart-2`).

**After that, the ID never changes.** Renaming or moving code updates `technical.location`, never the ID.

## Matching code to existing nodes

For each symbol in the code, `analyze` looks for the node that already describes it, in this order:

1. **Same location:** the same file and symbol.
2. **Same ID:** for example, a `planned` node whose ID matches newly written code.
3. **Same fingerprint:** a hash of the code's structure with names and comments removed. It finds code that was renamed or moved without other changes. It only matches when exactly one node and one symbol share the hash.
4. **Same name and kind, similar signature:** finds code that was moved *and* edited. If several candidates qualify, `analyze` reports the ambiguity and matches none of them.

Matches from steps 2–4 that change the location are reported as **moved**.

## What changes and what doesn't

| | |
|---|---|
| Never changed | `id`, `intent`, `governance`, hand-written edges, comments, formatting |
| Always refreshed | `technical` |
| Refreshed on `provenance: analyzed` nodes | `contract`, `parent_id` |
| `lifecycle_state` | `planned` → `in_progress` when the code appears; → `deprecated` with `--prune` |
| Analyzed edges | Added when found, removed when the call is gone |
| New nodes | `lifecycle_state: active`, `provenance: analyzed`, placeholder intent `"TODO: describe intent"`, written to the same file as their parent |

Nodes are never deleted. When code disappears, the node is reported as **missing**, and `--prune` marks it `deprecated`. A node is only considered missing if its file is gone, or the file is analyzed and the symbol isn't in it. Nodes pointing at files in other languages are left alone.

`--paths` limits all of this to nodes and edges whose code is under the given paths. The whole repository is still parsed, so calls into other directories resolve correctly.

## Volatile fields

`complexity` and `fingerprint` change with almost any edit to the code, so most code changes also change `intensify.yaml`. Commit the refreshed spec with the code. `intensify drift` (coming next) compares structure and contracts, and does not fail on these fields.
