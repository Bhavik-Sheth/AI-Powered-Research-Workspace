"""Literature Matrix — projects existing extractive cards into a persisted
comparison grid (MODULES.md, D27). Standard columns are a pure projection of
Paper Pipeline's cards — opening a matrix never re-extracts them. A custom
column runs one scoped extractive query per paper, cached by
`(paper_id, column_key)`. Editing any cell — standard or custom — writes a
`source: user` row into the same `matrix_cells` table and is looked up
first, ahead of the projection; it never touches the extracted value
underneath (US10).
"""

import uuid

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import experiments
import papers
from db.models import Matrices, MatrixCells
from llm import LLMError, Message, complete_structured
from matrix.models import ColumnDef, Matrix, MatrixCell, MatrixRow, MatrixUpdate, MatrixView, RowKind
from provenance import validate_and_anchor

_SCOPED_EXTRACTION_PROMPT = (
    "Answer the following question strictly from this paper's own text — no outside knowledge, "
    "no inference. Return the VERBATIM quote (copied exactly, character for character) that "
    "answers it, plus a few words of the text immediately before (prefix) and after (suffix) the "
    "quote, for disambiguation. If the paper does not answer the question, leave quote empty — "
    "never paraphrase, never guess."
)

# Same bound `extract_card_job` uses to keep the scoped-extraction context sized (papers/__init__.py).
_MAX_EXTRACTION_CHARS = 60_000


class _ScopedExtraction(BaseModel):
    quote: str = ""
    prefix: str = ""
    suffix: str = ""


def _matrix_from_row(row: Matrices) -> Matrix:
    return Matrix(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        selected_paper_ids=list(row.selected_paper_ids),
        selected_experiment_ids=list(row.selected_experiment_ids),
        column_defs=[ColumnDef(**c) for c in row.column_defs],
    )


async def build_matrix(session: AsyncSession, project_id: uuid.UUID, name: str) -> Matrix:
    row = Matrices(project_id=project_id, name=name)
    session.add(row)
    await session.flush()
    return _matrix_from_row(row)


async def get_matrix(session: AsyncSession, matrix_id: uuid.UUID) -> Matrix | None:
    row = await session.get(Matrices, matrix_id)
    return _matrix_from_row(row) if row is not None else None


async def list_matrices(session: AsyncSession, project_id: uuid.UUID) -> list[Matrix]:
    rows = await session.scalars(select(Matrices).where(Matrices.project_id == project_id))
    return [_matrix_from_row(row) for row in rows]


async def update_matrix(session: AsyncSession, matrix_id: uuid.UUID, update: MatrixUpdate) -> Matrix | None:
    """One `PUT`-shaped write (MODULES.md) — row order in the two id arrays
    is part of the artifact, so a caller always sends the full array it
    wants, never a delta."""
    row = await session.get(Matrices, matrix_id)
    if row is None:
        return None
    if update.name is not None:
        row.name = update.name
    if update.selected_paper_ids is not None:
        row.selected_paper_ids = update.selected_paper_ids
    if update.selected_experiment_ids is not None:
        row.selected_experiment_ids = update.selected_experiment_ids
    if update.column_defs is not None:
        row.column_defs = [c.model_dump() for c in update.column_defs]
    await session.flush()
    return _matrix_from_row(row)


def _cell_from_row(row: MatrixCells, row_kind: RowKind) -> MatrixCell:
    return MatrixCell(
        row_id=row.paper_id if row.paper_id is not None else row.experiment_id,
        row_kind=row_kind,
        column_key=row.column_key,
        value=row.value,
        source=row.source,
        anchor_id=row.anchor_id,
    )


async def _cell_overrides(session: AsyncSession, matrix_id: uuid.UUID) -> dict[tuple[uuid.UUID, str], MatrixCells]:
    """Every stored cell for this matrix — a custom column's cache **and**
    any `source: user` override, keyed by row id (Schema.md's `matrix_cells`
    is both in one table). Loaded once per view rather than once per cell."""
    rows = await session.scalars(select(MatrixCells).where(MatrixCells.matrix_id == matrix_id))
    return {(row.paper_id if row.paper_id is not None else row.experiment_id, row.column_key): row for row in rows}


async def _run_scoped_extraction(
    session: AsyncSession, matrix_id: uuid.UUID, paper_id: uuid.UUID, column: ColumnDef
) -> MatrixCell | None:
    """A custom column's per-paper scoped extractive query (D27), validated
    through Provenance and cached before being returned. `None` — no
    `matrix_cells` row written — is a legal outcome: the cell renders "not
    stated" by absence, never a stuck spinner (Schema.md)."""
    content = await papers.get_paper_content(session, paper_id)
    if content is None or not column.query:
        return None
    try:
        extracted = await complete_structured(
            messages=[
                Message(role="system", content=_SCOPED_EXTRACTION_PROMPT),
                Message(role="user", content=f"Question: {column.query}\n\nPaper text:\n{content.full_text[:_MAX_EXTRACTION_CHARS]}"),
            ],
            schema=_ScopedExtraction,
            tier="auxiliary",
            timeout=60,
        )
    except (*LLMError, RuntimeError):
        return None
    if not extracted.quote:
        return None
    anchor = await validate_and_anchor(session, paper_id, extracted.quote, extracted.prefix, extracted.suffix)
    if anchor is None:
        return None
    row = MatrixCells(
        matrix_id=matrix_id,
        paper_id=paper_id,
        column_key=column.column_key,
        value=extracted.quote,
        source="extracted",
        anchor_id=anchor.id,
        cached_at=anchor.validated_at,
    )
    session.add(row)
    await session.flush()
    return _cell_from_row(row, "paper")


async def get_matrix_view(session: AsyncSession, matrix_id: uuid.UUID) -> MatrixView | None:
    matrix = await get_matrix(session, matrix_id)
    if matrix is None:
        return None
    overrides = await _cell_overrides(session, matrix_id)

    rows: list[MatrixRow] = []
    cells: list[MatrixCell] = []

    for paper_id in matrix.selected_paper_ids:
        paper = await papers.get_paper(session, paper_id)
        if paper is None:
            continue
        rows.append(MatrixRow(row_id=paper_id, row_kind="paper", label=paper.title))
        card_by_field = {field.field_key: field for field in await papers.get_paper_card(session, paper_id)}
        for column in matrix.column_defs:
            cached = overrides.get((paper_id, column.column_key))
            if cached is not None:
                cells.append(_cell_from_row(cached, "paper"))
            elif column.kind == "standard":
                field = card_by_field.get(column.column_key)
                if field is not None:
                    cells.append(
                        MatrixCell(
                            row_id=paper_id,
                            row_kind="paper",
                            column_key=column.column_key,
                            value=field.value,
                            source="extracted",
                            anchor_id=field.anchor_id,
                            section_heading=field.section_heading,
                            char_start=field.char_start,
                            char_end=field.char_end,
                        )
                    )
            elif column.kind == "custom":
                cell = await _run_scoped_extraction(session, matrix_id, paper_id, column)
                if cell is not None:
                    cells.append(cell)
            # kind == "user" with no override yet: no cell — "not stated".

    for experiment_id in matrix.selected_experiment_ids:
        experiment = await experiments.get_experiment(session, experiment_id)
        if experiment is None:
            continue
        rows.append(MatrixRow(row_id=experiment_id, row_kind="experiment", label=experiment.title))
        for column in matrix.column_defs:
            # Standard/custom columns are paper-scoped projections and
            # queries respectively — an experiment row only ever carries a
            # user-entered cell, same lookup as a paper row's override.
            cached = overrides.get((experiment_id, column.column_key))
            if cached is not None:
                cells.append(_cell_from_row(cached, "experiment"))

    return MatrixView(matrix=matrix, rows=rows, cells=cells)


async def update_cell(session: AsyncSession, matrix_id: uuid.UUID, row_id: uuid.UUID, column_key: str, value: str) -> MatrixCell | None:
    """Sets `source: user`; never touches an extracted value (MODULES.md) —
    the standard-column projection or the custom-column cache, if either
    exists for this cell, is left exactly as it was.

    Looks up any existing row by hand rather than `ON CONFLICT` on the
    table's unique constraint: `matrix_cells`'s key includes `paper_id` and
    `experiment_id`, exactly one of which is NULL for any given row, and
    Postgres never treats two NULLs as conflicting — an upsert against that
    constraint would insert a fresh duplicate on every edit instead of
    updating in place.
    """
    matrix = await session.get(Matrices, matrix_id)
    if matrix is None:
        return None
    if row_id in matrix.selected_paper_ids:
        row_kind: RowKind = "paper"
    elif row_id in matrix.selected_experiment_ids:
        row_kind = "experiment"
    else:
        return None

    row_column = MatrixCells.paper_id if row_kind == "paper" else MatrixCells.experiment_id
    existing = await session.scalar(
        select(MatrixCells).where(
            MatrixCells.matrix_id == matrix_id, row_column == row_id, MatrixCells.column_key == column_key
        )
    )
    if existing is not None:
        existing.value = value
        existing.source = "user"
        existing.anchor_id = None
        existing.cached_at = None
        cell_row = existing
    else:
        cell_row = MatrixCells(
            matrix_id=matrix_id,
            paper_id=row_id if row_kind == "paper" else None,
            experiment_id=row_id if row_kind == "experiment" else None,
            column_key=column_key,
            value=value,
            source="user",
        )
        session.add(cell_row)
    await session.flush()
    return _cell_from_row(cell_row, row_kind)
