"""Wire/value shapes for Knowledge Graph (Rules.md: names match the wire shape)."""

import uuid
from typing import Literal

from pydantic import BaseModel

NodeType = Literal["paper", "author", "dataset", "repo", "topic", "method", "concept"]
Relation = Literal[
    "cites", "cited_by", "authored_by", "uses_dataset", "has_code", "has_topic", "method_of", "related_method"
]


class MetadataEdge(BaseModel):
    src_type: NodeType
    src_id: str
    dst_type: NodeType
    dst_id: str
    relation: Relation
    source_api: str


class LLMEdge(BaseModel):
    src_type: NodeType
    src_id: str
    dst_type: NodeType
    dst_id: str
    relation: Relation
    confidence: float | None = None


# The union `get_graph` (Phase 3) returns spans both `paper_edges` (global,
# `NodeType`/`Relation` above) and `idea_edges` (project-scoped) — a wider
# node/relation vocabulary than either table alone (Schema.md).
GraphNodeType = Literal["paper", "author", "dataset", "repo", "topic", "method", "concept", "note", "experiment", "highlight"]
GraphRelation = Literal[
    "cites",
    "cited_by",
    "authored_by",
    "uses_dataset",
    "has_code",
    "has_topic",
    "method_of",
    "related_method",
    "inspired_by",
    "references_note",
    "relates_to",
    "contradicts",
]
GraphProvenance = Literal["metadata", "llm", "user"]


class GraphEdge(BaseModel):
    id: uuid.UUID
    src_type: GraphNodeType
    src_id: str
    dst_type: GraphNodeType
    dst_id: str
    relation: GraphRelation
    provenance: GraphProvenance
    confidence: float | None = None


class Graph(BaseModel):
    edges: list[GraphEdge]
    # `paper`-type node ids are `papers.canonical_id` (D26's trust-split
    # identity), not `papers.id` — this resolves the ones actually present
    # in `edges` so a caller can open a paper node without a second
    # lookup round trip (MODULES.md: pull complexity downward).
    paper_ids: dict[str, uuid.UUID] = {}
    # A node label is otherwise just the raw canonical id — this lets a
    # caller show the paper's real title (e.g. a reader tab's label).
    paper_titles: dict[str, str] = {}
