"""Vault Writer — the sole writer of the vault (MODULES.md, D3/D4).

Phase 1.1 shipped only the startup layout check. Phase 1.6 adds `write_note`:
the file write and the `notes` index row happen as one operation — the file
is written first, the DB row only after, both inside the same transaction the
caller's `db.session()` commits (Rules.md). `write_highlight` / etc. land with
the phases that need them. The vault root itself is owned by Settings Store —
this module only knows the folder shapes inside it.
"""

import uuid
from pathlib import Path

import yaml
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Notes, Project
from settings import get_vault_path
from vault.models import Note, NoteInput

_LAYOUT = ("library/papers", "projects", ".research-os")

_FRONTMATTER_DELIMITER = "---\n"


class VaultWriteFailed(Exception):
    """The file wrote successfully but the index update did not.

    The DB transaction rolls back around this (Rules.md/MODULES.md), so the
    file that is now on disk is an accepted orphan until the same frontmatter
    id is written again — there is no reconciliation pass (D4).
    """


def ensure_layout() -> Path:
    """Create the vault root and its top-level folders if missing.

    Raises OSError if the resolved path exists but is not writable — callers
    treat that as the `vault` readiness capability failing, not a crash
    (Rules.md: an unwritable vault path fails only that capability).
    """
    root = get_vault_path()
    for subpath in _LAYOUT:
        (root / subpath).mkdir(parents=True, exist_ok=True)
    probe = root / ".research-os" / ".write-check"
    probe.write_text("")
    probe.unlink()
    return root


def _render_markdown(frontmatter: dict, body: str) -> str:
    yaml_block = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
    return f"{_FRONTMATTER_DELIMITER}{yaml_block}{_FRONTMATTER_DELIMITER}\n{body}"


async def _unique_note_file_path(session: AsyncSession, project_id: uuid.UUID, project_slug: str, title: str) -> str:
    base = slugify(title) or "note"
    candidate = base
    suffix = 1
    while True:
        file_path = f"projects/{project_slug}/notes/{candidate}.md"
        collision = await session.scalar(
            select(Notes.id).where(Notes.project_id == project_id, Notes.file_path == file_path)
        )
        if collision is None:
            return file_path
        suffix += 1
        candidate = f"{base}-{suffix}"


async def write_note(session: AsyncSession, project_id: uuid.UUID, note: NoteInput) -> Note:
    """Creates or updates one note; assigns the frontmatter id on create (MODULES.md).

    `note.frontmatter_id` absent means create: a new id is generated, written
    into the file's own YAML frontmatter, and used as the DB row's PK — never
    derived from the path (D4). Present means update: the existing file at
    its unchanged `file_path` is overwritten in place: id and location never
    move on an edit, only title/body/frontmatter content does.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")

    existing: Notes | None = None
    if note.frontmatter_id is not None:
        existing = await session.get(Notes, note.frontmatter_id)
        if existing is None or existing.project_id != project_id:
            raise ValueError(f"note {note.frontmatter_id} not found in project {project_id}")

    note_id = existing.id if existing is not None else uuid.uuid4()
    file_path = (
        existing.file_path
        if existing is not None
        else await _unique_note_file_path(session, project_id, project.slug, note.title)
    )
    frontmatter = {"id": str(note_id), "title": note.title}

    absolute_path = get_vault_path() / file_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        absolute_path.write_text(_render_markdown(frontmatter, note.body), encoding="utf-8")
    except OSError as exc:
        raise VaultWriteFailed(f"could not write {file_path}: {exc}") from exc

    try:
        if existing is not None:
            existing.title = note.title
            existing.body = note.body
            existing.frontmatter = frontmatter
            row = existing
        else:
            row = Notes(
                id=note_id, project_id=project_id, title=note.title, file_path=file_path, body=note.body,
                frontmatter=frontmatter,
            )
            session.add(row)
        await session.flush()
    except Exception as exc:
        # The file above is already on disk; per D4 this is an accepted
        # orphan, not something this call retries or repairs (no
        # reconciliation pass exists). The caller's db.session() rolls the
        # DB side back around this exception.
        raise VaultWriteFailed(f"note file written but index update failed: {exc}") from exc

    return Note.from_row(row)
