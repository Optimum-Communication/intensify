from __future__ import annotations

from intensify.spec import yamlio

CANONICAL = """\
# header comment
intensify: "1.0"
project:
  id: demo  # trailing comment
  name: 'Demo'

nodes:
  # a node
  - id: demo
    kind: repo
    intent:
      summary: >-
        Folded text
        continues here.
      rationale: |
        Literal block.
edges:
  - {from: a, to: b, type: CALLS}
  - from: x
    to: y
    type: BLOCKS
"""


def test_canonical_style_round_trips_byte_for_byte() -> None:
    assert yamlio.dumps(yamlio.loads(CANONICAL)) == CANONICAL


def test_edit_keeps_comments_and_formatting_elsewhere() -> None:
    data = yamlio.loads(CANONICAL)
    data["nodes"][0]["kind"] = "module"
    out = yamlio.dumps(data)
    assert out == CANONICAL.replace("kind: repo", "kind: module")


def test_to_plain_strips_round_trip_types() -> None:
    plain = yamlio.to_plain(yamlio.loads(CANONICAL))
    assert type(plain) is dict
    assert type(plain["nodes"]) is list
    assert type(plain["project"]["name"]) is str
    assert plain["nodes"][0]["intent"]["summary"] == "Folded text continues here."


def test_line_of_points_at_keys_and_items() -> None:
    data = yamlio.loads(CANONICAL)
    assert yamlio.line_of(data, ["project", "id"]) == 4
    assert yamlio.line_of(data, ["nodes", 0]) == 9
    assert yamlio.line_of(data, ["edges", 1, "type"]) == 21
    # Missing keys fall back to the deepest existing ancestor.
    assert yamlio.line_of(data, ["nodes", 0, "missing"]) == 9
    assert yamlio.line_of(data, ["nope"]) is None
