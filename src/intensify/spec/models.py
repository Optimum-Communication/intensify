# Generated from spec/v1.0/schema.json by `make gen`. Do not edit.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Project(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    id: Annotated[str, Field(pattern="^[a-z0-9][a-z0-9_-]*$")]
    """
    Identifier of a project (usually one repository). Lowercase, unique within an organization.
    """
    name: Annotated[str | None, Field(min_length=1)] = None
    description: str | None = None


class NodeKind(StrEnum):
    """
    Granularity of a node, from coarsest to finest.
    """

    ecosystem = "ecosystem"
    repo = "repo"
    module = "module"
    class_ = "class"
    function = "function"
    block = "block"


class LifecycleState(StrEnum):
    planned = "planned"
    in_progress = "in_progress"
    active = "active"
    deprecated = "deprecated"


class Provenance(StrEnum):
    """
    Who created the node or edge: the analyzer, a person editing YAML, or the cloud planning studio.
    """

    analyzed = "analyzed"
    manual = "manual"
    cloud = "cloud"


class EdgeType(StrEnum):
    """
    Relationship type. Containment is expressed with `parent_id`, never as an edge.
    """

    CALLS = "CALLS"
    READS_FROM = "READS_FROM"
    WRITES_TO = "WRITES_TO"
    IMPLEMENTS = "IMPLEMENTS"
    VERIFIES = "VERIFIES"
    BLOCKS = "BLOCKS"


class Confidence(StrEnum):
    """
    How sure the analyzer is that an edge is real. Hand-written edges are assumed high.
    """

    high = "high"
    medium = "medium"
    low = "low"


class Author(StrEnum):
    """
    Who wrote this intent. `ai` marks a draft that a person should review.
    """

    human = "human"
    ai = "ai"


class Intent(BaseModel):
    """
    Why the node exists. Required on every node, at every lifecycle state.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    summary: Annotated[str, Field(min_length=1)]
    """
    What the node does, in a sentence or two.
    """
    rationale: str | None = None
    """
    Why it exists or is built this way.
    """
    author: Author = Author.human
    """
    Who wrote this intent. `ai` marks a draft that a person should review.
    """


class Parameter(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str, Field(min_length=1)]
    type: str | None = None
    """
    Type in the node's own language, e.g. `Decimal | None`.
    """
    required: bool = True
    description: str | None = None


class Output(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str | None, Field(min_length=1)] = None
    type: str | None = None
    description: str | None = None


class ErrorMode(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Annotated[str, Field(min_length=1)]
    """
    Error or exception type.
    """
    when: str | None = None
    """
    Condition that triggers it.
    """


class Location(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    path: Annotated[str, Field(min_length=1)]
    """
    Path relative to the repository root, using `/` separators.
    """
    symbol: Annotated[str | None, Field(min_length=1)] = None
    """
    Symbol within the file, e.g. `RefundService.issue`.
    """


class Complexity(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    cyclomatic: Annotated[int | None, Field(ge=1)] = None
    loc: Annotated[int | None, Field(ge=0)] = None


class EdgePattern(BaseModel):
    """
    Matches outgoing edges of the node (and its descendants) by type and target.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    type: EdgeType | None = None
    to: Annotated[str, Field(min_length=1)]
    """
    Glob over target node refs, e.g. `db.*`.
    """


class DependentsPattern(BaseModel):
    """
    Only nodes matching these globs may have edges into the node (or its descendants).
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    type: EdgeType | None = None
    from_: Annotated[
        list[Annotated[str, StringConstraints(min_length=1)]], Field(alias="from", min_length=1)
    ]


class Edge(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    from_: Annotated[
        str,
        Field(
            alias="from",
            pattern="^([a-z0-9][a-z0-9_-]*:)?[A-Za-z0-9_$][A-Za-z0-9_$-]*(\\.[A-Za-z0-9_$][A-Za-z0-9_$-]*)*$",
        ),
    ]
    """
    Reference to a node: a local NodeId, or `<project-id>:<node-id>` for a node in another project.
    """
    to: Annotated[
        str,
        Field(
            pattern="^([a-z0-9][a-z0-9_-]*:)?[A-Za-z0-9_$][A-Za-z0-9_$-]*(\\.[A-Za-z0-9_$][A-Za-z0-9_$-]*)*$"
        ),
    ]
    """
    Reference to a node: a local NodeId, or `<project-id>:<node-id>` for a node in another project.
    """
    type: EdgeType
    description: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.active
    confidence: Confidence = Confidence.high
    provenance: Provenance = Provenance.manual


class DriftCategory(StrEnum):
    undocumented_node = "undocumented_node"
    missing_node = "missing_node"
    undocumented_edge = "undocumented_edge"
    contract_mismatch = "contract_mismatch"
    invariant_violation = "invariant_violation"
    moved_node = "moved_node"
    unenriched_node = "unenriched_node"


class Depth(StrEnum):
    """
    Finest node kind to create.
    """

    module = "module"
    class_ = "class"
    function = "function"


class AnalyzeConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    include: list[Annotated[str, StringConstraints(min_length=1)]] | None = None
    """
    Globs of files to analyze. Defaults to all files not ignored by git.
    """
    exclude: list[Annotated[str, StringConstraints(min_length=1)]] | None = None
    depth: Depth = Depth.function
    """
    Finest node kind to create.
    """
    public_only: bool = True
    """
    Skip private symbols (leading underscore, or not exported).
    """


class DriftConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    fail_on: list[DriftCategory] = [
        DriftCategory.undocumented_edge,
        DriftCategory.contract_mismatch,
        DriftCategory.invariant_violation,
        DriftCategory.missing_node,
    ]
    """
    Drift categories that fail the check. Others are reported as warnings.
    """
    ignore_low_confidence: bool = True


class VerifyConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    test_command: Annotated[str | None, Field(min_length=1)] = None
    """
    Command that runs tests. `{tests}` is replaced with the test files that verify the node.
    """


class Contract(BaseModel):
    """
    The node's interface. Optional while planning.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    inputs: list[Parameter] | None = None
    outputs: list[Output] | None = None
    errors: list[ErrorMode] | None = None


class Technical(BaseModel):
    """
    Implementation details. Usually maintained by `intensify analyze`.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    language: Annotated[str | None, Field(min_length=1)] = None
    location: Location | None = None
    signature: str | None = None
    complexity: Complexity | None = None
    fingerprint: Annotated[str | None, Field(pattern="^sha256:[0-9a-f]{64}$")] = None
    """
    Hash of the normalized code structure, used to track the node across moves and renames.
    """


class InvariantRule(BaseModel):
    """
    Machine-checkable form of an invariant. Exactly one rule type per invariant.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    forbid_edge: EdgePattern | None = None
    require_edge: EdgePattern | None = None
    allowed_dependents: DependentsPattern | None = None


class Config(BaseModel):
    """
    Settings for the CLI. All fields are optional.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    analyze: AnalyzeConfig | None = None
    drift: DriftConfig | None = None
    verify: VerifyConfig | None = None


class Invariant(BaseModel):
    """
    A rule the node must always satisfy. `text` is for people; the optional `rule` is checked by `intensify drift`.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    id: Annotated[str, Field(pattern="^[a-z0-9][a-z0-9_-]*$")]
    text: Annotated[str, Field(min_length=1)]
    rule: InvariantRule | None = None


class Governance(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    owners: list[Annotated[str, StringConstraints(min_length=1)]] | None = None
    tags: list[Annotated[str, StringConstraints(min_length=1)]] | None = None
    """
    Free-form tags. A `key:value` form such as `domain:billing` is conventional.
    """
    invariants: list[Invariant] | None = None


class Node(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    id: Annotated[
        str, Field(pattern="^[A-Za-z0-9_$][A-Za-z0-9_$-]*(\\.[A-Za-z0-9_$][A-Za-z0-9_$-]*)*$")
    ]
    """
    Stable, human-readable node identifier: dot-separated segments, unique within the project. Never regenerated once assigned, even when code moves.
    """
    kind: NodeKind
    name: Annotated[str | None, Field(min_length=1)] = None
    """
    Optional display name.
    """
    parent_id: Annotated[
        str | None,
        Field(pattern="^[A-Za-z0-9_$][A-Za-z0-9_$-]*(\\.[A-Za-z0-9_$][A-Za-z0-9_$-]*)*$"),
    ] = None
    """
    Stable, human-readable node identifier: dot-separated segments, unique within the project. Never regenerated once assigned, even when code moves.
    """
    lifecycle_state: LifecycleState = LifecycleState.planned
    intent: Intent
    contract: Contract | None = None
    technical: Technical | None = None
    governance: Governance | None = None
    provenance: Provenance = Provenance.manual


class IntensifyDocument(BaseModel):
    """
    The root intensify.yaml file of a repository: an intent graph of nodes and typed edges.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    intensify: Literal["1.0"]
    """
    Version of the Intensify specification this document follows.
    """
    project: Project
    config: Config | None = None
    includes: list[Annotated[str, StringConstraints(min_length=1)]] | None = None
    """
    Glob patterns, relative to this file, for fragment files holding more nodes and edges.
    """
    nodes: list[Node] | None = None
    edges: list[Edge] | None = None


class IntensifyFragment(BaseModel):
    """
    A file listed under `includes` in the root document. Holds only nodes and edges.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    intensify: Literal["1.0"]
    """
    Version of the Intensify specification this document follows.
    """
    nodes: list[Node] | None = None
    edges: list[Edge] | None = None
