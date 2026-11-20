# Fixture repositories

Small sample projects the analyzers run against. They are not project code: ruff, mypy and pytest collection all skip this directory.

- `py_shop/`: a Python package with subpackages, relative imports, aliases, a private module, inheritance and a test file.
- `go_shop/`: a Go module with `go.mod`, an aliased import, an `internal/` package, a method declared in a different file from its type, struct fields and `range`, an embedded interface, an interface satisfied implicitly, a `_test` package, and a generated file, `vendor/` and `testdata/` that are skipped.
- `java_shop/`: Java in Maven layout with packages, single-type, wildcard and static imports, an interface, a nested enum and record, overloads, package-private classes and a JUnit test.
- `ts_shop/`: TypeScript and JavaScript with `tsconfig.json` path aliases (and comments), re-exports through `index.ts`, an interface, `.js` import specifiers and a test file.

Tests copy a fixture into a temporary directory before running `init` / `analyze` on it.
