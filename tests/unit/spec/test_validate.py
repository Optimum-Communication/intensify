from __future__ import annotations

from pathlib import Path

import pytest

from intensify.spec import Severity, load_and_validate
from intensify.spec.loader import resolve_path
from tests.conftest import WriteSpec

HEADER = """\
intensify: "1.0"
project:
  id: demo
"""

ROOT_NODE = """\
  - id: demo
    kind: repo
    lifecycle_state: active
    intent: {summary: Root}
"""


def spec(nodes: str = "", edges: str = "", extra: str = "") -> str:
    text = HEADER + extra + "nodes:\n" + ROOT_NODE + nodes
    if edges:
        text += "edges:\n" + edges
    return text


def codes(path: Path) -> list[str]:
    return [d.code for d in load_and_validate(path).diagnostics]


def test_valid_spec_has_no_diagnostics(write_spec: WriteSpec) -> None:
    loaded = load_and_validate(write_spec(spec()))
    assert loaded.ok
    assert loaded.diagnostics == []
    assert [e.node.id for e in loaded.nodes] == ["demo"]


def test_directory_resolves_to_intensify_yaml(tmp_path: Path) -> None:
    assert resolve_path(tmp_path) == tmp_path / "intensify.yaml"


def test_defaults_are_applied(write_spec: WriteSpec) -> None:
    loaded = load_and_validate(
        write_spec(
            spec(
                nodes="""\
  - id: demo.x
    kind: module
    parent_id: demo
    intent: {summary: X}
""",
                edges="  - {from: demo.x, to: demo, type: BLOCKS}\n",
            )
        )
    )
    node = loaded.nodes[1].node
    assert node.lifecycle_state == "planned"
    assert node.provenance == "manual"
    assert node.intent.author == "human"
    edge = loaded.edges[0].edge
    assert (edge.lifecycle_state, edge.confidence, edge.provenance) == ("active", "high", "manual")


@pytest.mark.parametrize(
    ("nodes", "edges", "expected"),
    [
        pytest.param(
            """\
  - id: demo
    kind: module
    intent: {summary: Again}
""",
            "",
            ["duplicate-node-id"],
            id="duplicate-node-id",
        ),
        pytest.param(
            """\
  - id: demo.x
    kind: module
    parent_id: demo.missing
    intent: {summary: X}
""",
            "",
            ["unknown-parent"],
            id="unknown-parent",
        ),
        pytest.param(
            """\
  - id: a
    kind: module
    parent_id: b
    intent: {summary: A}
  - id: b
    kind: module
    parent_id: c
    intent: {summary: B}
  - id: c
    kind: module
    parent_id: a
    intent: {summary: C}
""",
            "",
            ["containment-cycle"],
            id="containment-cycle-reported-once",
        ),
        pytest.param(
            """\
  - id: demo.fn
    kind: function
    parent_id: demo
    intent: {summary: F}
  - id: demo.fn.mod
    kind: module
    parent_id: demo.fn
    intent: {summary: M}
""",
            "",
            ["invalid-nesting"],
            id="invalid-nesting",
        ),
        pytest.param(
            """\
  - id: demo.a
    kind: module
    parent_id: demo
    intent: {summary: A}
  - id: demo.a.inner
    kind: module
    parent_id: demo.a
    intent: {summary: Same kind nesting is allowed}
""",
            "",
            [],
            id="same-kind-nesting-ok",
        ),
        pytest.param(
            """\
  - id: demo.x
    kind: function
    lifecycle_state: active
    intent: {summary: X}
""",
            "",
            ["missing-location"],
            id="missing-location",
        ),
        pytest.param(
            """\
  - id: demo.x
    kind: function
    lifecycle_state: active
    intent: {summary: X}
    technical:
      location: {path: src/x.py, symbol: x}
""",
            "",
            [],
            id="active-with-location-ok",
        ),
        pytest.param(
            """\
  - id: demo.x
    kind: module
    intent: {summary: X}
    governance:
      invariants:
        - {id: one, text: First}
        - {id: one, text: Second}
""",
            "",
            ["duplicate-invariant-id", "duplicate-invariant-id"],
            id="duplicate-invariant-id",
        ),
        pytest.param(
            "",
            "  - {from: demo, to: nope, type: CALLS}\n",
            ["unknown-edge-endpoint"],
            id="unknown-edge-endpoint",
        ),
        pytest.param(
            "",
            "  - {from: demo, to: 'demo:nope', type: CALLS}\n",
            ["unknown-edge-endpoint"],
            id="own-project-qualified-ref-is-local",
        ),
        pytest.param(
            "",
            "  - {from: demo, to: 'other:some.node', type: CALLS}\n",
            [],
            id="other-project-ref-not-checked",
        ),
        pytest.param(
            "",
            "  - {from: demo, to: demo, type: CALLS}\n",
            [],
            id="recursive-call-ok",
        ),
        pytest.param(
            "",
            "  - {from: demo, to: demo, type: BLOCKS}\n",
            ["self-edge"],
            id="self-edge",
        ),
        pytest.param(
            "",
            "  - {from: demo, to: demo, type: CALLS}\n  - {from: demo, to: demo, type: CALLS}\n",
            ["duplicate-edge"],
            id="duplicate-edge",
        ),
    ],
)
def test_semantic_rules(write_spec: WriteSpec, nodes: str, edges: str, expected: list[str]) -> None:
    assert codes(write_spec(spec(nodes, edges))) == expected


def test_diagnostic_points_at_offending_line(write_spec: WriteSpec) -> None:
    path = write_spec(
        spec(
            nodes="""\
  - id: demo.x
    kind: module
    parent_id: demo.missing
    intent: {summary: X}
"""
        )
    )
    [diag] = load_and_validate(path).diagnostics
    assert diag.line == 11
    assert diag.pointer == "nodes[1].parent_id"
    assert diag.file == path


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        pytest.param(
            "  - {id: 'bad id', kind: module, intent: {summary: X}}\n", "expected dot-separated"
        ),
        pytest.param("  - {id: x, kind: widget, intent: {summary: X}}\n", "is not one of"),
        pytest.param("  - {id: x, kind: module, intent: {summary: ''}}\n", "must not be empty"),
        pytest.param("  - {id: x, kind: module}\n", "'intent' is a required property"),
        pytest.param(
            "  - {id: x, kind: module, intent: {summary: X}, colour: blue}\n",
            "unknown field 'colour'",
        ),
        pytest.param(
            "  - {id: x, kind: module, intent: {summary: X}, technical: {fingerprint: abc}}\n",
            "'sha256:' followed by 64",
        ),
        pytest.param(
            "  - id: x\n    kind: module\n    intent: {summary: X}\n"
            "    governance: {invariants: [{id: i, text: t, rule: {}}]}\n",
            "must contain exactly one of",
        ),
    ],
)
def test_schema_errors_have_readable_messages(
    write_spec: WriteSpec, snippet: str, message: str
) -> None:
    loaded = load_and_validate(write_spec(spec(nodes=snippet)))
    assert not loaded.ok
    assert {d.code for d in loaded.diagnostics} == {"schema"}
    assert any(message in d.message for d in loaded.diagnostics), loaded.diagnostics


def test_unknown_field_points_at_the_field(write_spec: WriteSpec) -> None:
    loaded = load_and_validate(
        write_spec(
            spec(nodes="  - id: x\n    kind: module\n    intent: {summary: X}\n    colour: blue\n")
        )
    )
    [diag] = loaded.diagnostics
    assert diag.pointer == "nodes[1].colour"
    assert diag.line == 12


@pytest.mark.parametrize(
    ("text", "code"),
    [
        pytest.param('intensify: "2.0"\n', "unsupported-version", id="version"),
        pytest.param("a: [1,\n", "yaml-syntax", id="syntax"),
        pytest.param("- just\n- a list\n", "not-a-mapping", id="not-mapping"),
    ],
)
def test_file_level_errors(write_spec: WriteSpec, text: str, code: str) -> None:
    assert codes(write_spec(text)) == [code]


def test_missing_file(tmp_path: Path) -> None:
    [diag] = load_and_validate(tmp_path).diagnostics
    assert diag.code == "file-not-found"
    assert "intensify init" in diag.message


class TestIncludes:
    def test_fragments_are_merged_and_checked_together(self, write_spec: WriteSpec) -> None:
        root = write_spec(spec(extra='includes: ["parts/*.yaml"]\n'))
        write_spec(
            """\
            intensify: "1.0"
            nodes:
              - {id: demo.billing, kind: module, parent_id: demo, intent: {summary: B}}
            edges:
              - {from: demo.billing, to: demo.accounts, type: READS_FROM}
            """,
            "parts/billing.yaml",
        )
        write_spec(
            """\
            intensify: "1.0"
            nodes:
              - {id: demo.accounts, kind: module, parent_id: demo, intent: {summary: A}}
            """,
            "parts/accounts.yaml",
        )
        loaded = load_and_validate(root)
        assert loaded.diagnostics == []
        assert [e.node.id for e in loaded.nodes] == ["demo", "demo.accounts", "demo.billing"]
        assert {e.node.id: e.file.path.name for e in loaded.nodes}["demo.billing"] == "billing.yaml"
        assert len(loaded.files) == 3

    def test_duplicate_across_files_names_first_location(self, write_spec: WriteSpec) -> None:
        root = write_spec(spec(extra='includes: ["parts/*.yaml"]\n'))
        write_spec(
            'intensify: "1.0"\nnodes:\n  - {id: demo, kind: module, intent: {summary: Dup}}\n',
            "parts/dup.yaml",
        )
        [diag] = load_and_validate(root).diagnostics
        assert diag.code == "duplicate-node-id"
        assert diag.file.name == "dup.yaml"
        assert "intensify.yaml:6" in diag.message

    def test_fragment_cannot_set_project(self, write_spec: WriteSpec) -> None:
        root = write_spec(spec(extra='includes: ["parts/*.yaml"]\n'))
        write_spec('intensify: "1.0"\nproject: {id: other}\n', "parts/bad.yaml")
        [diag] = load_and_validate(root).diagnostics
        assert diag.code == "schema"
        assert "unknown field 'project'" in diag.message

    def test_unmatched_pattern_warns(self, write_spec: WriteSpec) -> None:
        loaded = load_and_validate(write_spec(spec(extra='includes: ["nothing/*.yaml"]\n')))
        [diag] = loaded.diagnostics
        assert (diag.code, diag.severity) == ("include-no-match", Severity.warning)
        assert loaded.ok

    def test_root_file_is_never_included_twice(self, write_spec: WriteSpec) -> None:
        loaded = load_and_validate(write_spec(spec(extra='includes: ["*.yaml"]\n')))
        assert len(loaded.files) == 1
