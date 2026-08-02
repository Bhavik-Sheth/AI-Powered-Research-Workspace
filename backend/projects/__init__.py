"""Project Record — CRUD for the unit of isolation (memory, graph, feed, session).

MODULES.md does not name an owning module for basic project CRUD; this
module fills that gap so REST API routes never touch SQL directly (Rules.md:
"call a service package" — never the database — from a route handler).
Flagged here for reconciliation against MODULES.md, not added silently.
"""

import uuid
from datetime import datetime, timezone

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Paper, Project, ProjectPapers


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


async def add_paper_to_project(session: AsyncSession, project_id: uuid.UUID, paper_id: uuid.UUID) -> ProjectPapers:
    """Membership only — a paper's content is global and computed once (D25)."""
    stmt = (
        insert(ProjectPapers)
        .values(project_id=project_id, paper_id=paper_id)
        .on_conflict_do_nothing(index_elements=["project_id", "paper_id"])
    )
    await session.execute(stmt)
    row = await session.get(ProjectPapers, {"project_id": project_id, "paper_id": paper_id})
    assert row is not None
    return row


async def list_project_papers(session: AsyncSession, project_id: uuid.UUID) -> list[tuple[Paper, ProjectPapers]]:
    """Every paper in a project's library, with this project's own membership row."""
    rows = (
        await session.execute(
            select(Paper, ProjectPapers)
            .join(ProjectPapers, ProjectPapers.paper_id == Paper.id)
            .where(ProjectPapers.project_id == project_id)
            .order_by(ProjectPapers.added_at.desc())
        )
    ).all()
    return [(paper, membership) for paper, membership in rows]


async def save_tab_stack(
    session: AsyncSession, project_id: uuid.UUID, tab_stack: list[dict], active_tab: str | None
) -> Project | None:
    """Persists the center-pane tab stack (Schema.md `projects.tab_stack` /
    `active_tab`), and bumps `last_opened_at` — the same signal Dashboard's
    "continue where you left off" tiles read, since writing the stack is
    what every navigation change in the project already does."""
    project = await session.get(Project, project_id)
    if project is None:
        return None
    project.tab_stack = tab_stack
    project.active_tab = active_tab
    project.last_opened_at = datetime.now(timezone.utc)
    await session.flush()
    return project


async def set_paper_relevance(
    session: AsyncSession, project_id: uuid.UUID, paper_id: uuid.UUID, relevance: str | None, why_relevant: str | None
) -> ProjectPapers | None:
    """A partial update: only fields actually given (non-`None`) change."""
    row = await session.get(ProjectPapers, {"project_id": project_id, "paper_id": paper_id})
    if row is None:
        return None
    if relevance is not None:
        row.relevance = relevance
    if why_relevant is not None:
        row.why_relevant = why_relevant
    await session.flush()
    return row
