# Fixture repositories

Small sample projects the analyzers run against. They are not project code: ruff, mypy and pytest collection all skip this directory.

- `py_shop/`: a Python package with subpackages, relative imports, aliases, a private module, inheritance and a test file.
- `java_shop/`: Java in Maven layout with packages, single-type, wildcard and static imports, an interface, a nested enum and record, overloads, package-private classes and a JUnit test.
- `ts_shop/`: TypeScript and JavaScript with `tsconfig.json` path aliases (and comments), re-exports through `index.ts`, an interface, `.js` import specifiers and a test file.

Tests copy a fixture into a temporary directory before running `init` / `analyze` on it.
