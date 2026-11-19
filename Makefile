.PHONY: install fmt lint typecheck test check gen check-gen

install:
	uv sync

fmt:
	uv run ruff check --fix .
	uv run ruff format .

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest

check: lint typecheck test

SPEC_VERSION := 1.0
MODELS := src/intensify/spec/models.py

# Regenerate everything derived from the canonical schema. Never edit the outputs by hand.
gen:
	cp spec/v$(SPEC_VERSION)/schema.json src/intensify/spec/schemas/v$(SPEC_VERSION).json
	uv run datamodel-codegen \
		--input spec/v$(SPEC_VERSION)/schema.json --input-file-type jsonschema --output $(MODELS) \
		--output-model-type pydantic_v2.BaseModel --target-python-version 3.11 \
		--custom-file-header "# Generated from spec/v$(SPEC_VERSION)/schema.json by \`make gen\`. Do not edit." \
		--use-annotated --field-constraints --use-standard-collections --use-union-operator \
		--use-schema-description --use-field-description --use-title-as-name \
		--use-default --strict-nullable --deserialize-default-values enum \
		--collapse-root-models --collapse-constrained-root-models-as-annotated \
		--enum-field-as-literal none --use-subclass-enum --formatters builtin
	uv run ruff check --fix --quiet $(MODELS)
	uv run ruff format --quiet $(MODELS)

# Fails if generated files are out of date with the schema (used in CI).
check-gen: gen
	git diff --exit-code -- src/intensify/spec/models.py src/intensify/spec/schemas/
