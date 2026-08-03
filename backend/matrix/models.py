"""Wire/value shapes for Literature Matrix (Rules.md: names match the wire shape)."""

import uuid
from typing import Literal

from pydantic import BaseModel

ColumnKind = Literal["standard", "custom", "user"]
CellSource = Literal["extracted", "user"]
RowKind = Literal["paper", "experiment"]

# The standard columns are exactly `paper_cards.field_key` (Schema.md) — a
# standard column's `column_key` is one of these, and its cells are a pure
# projection of the matching `paper_cards` row, never a separate write.
STANDARD_FIELD_KEYS = ("problem", "method", "datasets", "results", "limitations")


class ColumnDef(BaseModel):
    column_key: str
    label: str
    kind: ColumnKind
    query: str | None = None


class Matrix(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    selected_paper_ids: list[uuid.UUID]
    selected_experiment_ids: list[uuid.UUID]
    column_defs: list[ColumnDef]


class MatrixUpdate(BaseModel):
    """What a caller submits to `update_matrix` — one `PUT`-shaped write
    (MODULES.md); every field optional so a partial update only touches
    what it names."""

    name: str | None = None
    selected_paper_ids: list[uuid.UUID] | None = None
    selected_experiment_ids: list[uuid.UUID] | None = None
    column_defs: list[ColumnDef] | None = None


class MatrixRow(BaseModel):
    row_id: uuid.UUID
    row_kind: RowKind
    label: str


class MatrixCell(BaseModel):
    row_id: uuid.UUID
    row_kind: RowKind
    column_key: str
    value: str
    source: CellSource
    anchor_id: uuid.UUID | None = None
    section_heading: str | None = None
    char_start: int | None = None
    char_end: int | None = None


class MatrixView(BaseModel):
    matrix: Matrix
    rows: list[MatrixRow]
    cells: list[MatrixCell]


class UpdateCellRequest(BaseModel):
    row_id: uuid.UUID
    column_key: str
    value: str
