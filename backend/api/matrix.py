"""`GET/POST /api/projects/:id/matrix`, `GET/PUT /api/projects/:id/matrix/:matrixId`,
`PATCH /api/projects/:id/matrix/:matrixId/cells` — backs Literature Matrix (TRD §4.2, US10).
"""

import uuid

from fastapi import APIRouter, HTTPException

import db
import matrix
from matrix.models import Matrix, MatrixCell, MatrixUpdate, MatrixView, UpdateCellRequest

router = APIRouter()


@router.get("/api/projects/{project_id}/matrix", response_model=list[Matrix])
async def list_matrices(project_id: uuid.UUID) -> list[Matrix]:
    async with db.session() as session:
        return await matrix.list_matrices(session, project_id)


@router.post("/api/projects/{project_id}/matrix", response_model=Matrix)
async def create_matrix(project_id: uuid.UUID, body: MatrixUpdate) -> Matrix:
    async with db.session() as session:
        name = body.name or "Untitled matrix"
        created = await matrix.build_matrix(session, project_id, name)
        if body.selected_paper_ids is not None or body.selected_experiment_ids is not None or body.column_defs is not None:
            updated = await matrix.update_matrix(session, created.id, body)
            return updated if updated is not None else created
        return created


@router.get("/api/projects/{project_id}/matrix/{matrix_id}", response_model=MatrixView)
async def get_matrix_view(project_id: uuid.UUID, matrix_id: uuid.UUID) -> MatrixView:
    async with db.session() as session:
        view = await matrix.get_matrix_view(session, matrix_id)
        if view is None:
            raise HTTPException(status_code=404, detail="matrix not found")
        return view


@router.put("/api/projects/{project_id}/matrix/{matrix_id}", response_model=Matrix)
async def put_matrix(project_id: uuid.UUID, matrix_id: uuid.UUID, body: MatrixUpdate) -> Matrix:
    async with db.session() as session:
        updated = await matrix.update_matrix(session, matrix_id, body)
        if updated is None:
            raise HTTPException(status_code=404, detail="matrix not found")
        return updated


@router.patch("/api/projects/{project_id}/matrix/{matrix_id}/cells", response_model=MatrixCell)
async def update_cell(project_id: uuid.UUID, matrix_id: uuid.UUID, body: UpdateCellRequest) -> MatrixCell:
    async with db.session() as session:
        cell = await matrix.update_cell(session, matrix_id, body.row_id, body.column_key, body.value)
        if cell is None:
            raise HTTPException(status_code=404, detail="matrix or row not found")
        return cell
