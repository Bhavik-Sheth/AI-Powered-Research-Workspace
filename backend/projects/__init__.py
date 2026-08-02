"""Project Record — CRUD for the unit of isolation (memory, graph, feed, session).

MODULES.md does not name an owning module for basic project CRUD; this
module fills that gap so REST API routes never touch SQL directly (Rules.md:
"call a service package" — never the database — from a route handler).
Flagged here for reconciliation against MODULES.md, not added silently.
"""

import uuid

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Project


async def _unique_slug(session: AsyncSession, name: str) -> str:
    base = slugify(name) or "project"
    slug = base
    suffix = 1
    while await session.scalar(select(Project.id).where(Project.slug == slug)) is not None:
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


async def create_project(session: AsyncSession, name: str, focus_seed: str | None = None) -> Project:
    """Creates a project; the slug names its vault folder (D3)."""
    project = Project(id=uuid.uuid4(), name=name, slug=await _unique_slug(session, name), focus_seed=focus_seed)
    session.add(project)
    await session.flush()
    return project


async def list_projects(session: AsyncSession) -> list[Project]:
    return list((await session.scalars(select(Project).order_by(Project.created_at))).all())


async def get_project(session: AsyncSession, project_id: uuid.UUID) -> Project | None:
    return await session.get(Project, project_id)
