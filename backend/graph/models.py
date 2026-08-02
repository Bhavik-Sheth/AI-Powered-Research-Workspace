"""Wire/value shapes for Knowledge Graph (Rules.md: names match the wire shape)."""

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
