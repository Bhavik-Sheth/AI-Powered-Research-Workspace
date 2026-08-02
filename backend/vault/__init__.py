"""Vault Writer — the sole writer of the vault (MODULES.md, D3/D4).

Phase 1.1 shipped only the startup layout check. Phase 1.6 adds `write_note`:
the file write and the `notes` index row happen as one operation — the file
is written first, the DB row only after, both inside the same transaction the
caller's `db.session()` commits (Rules.md). `write_highlight` / etc. land with
the phases that need them. The vault root itself is owned by Settings Store —
this module only knows the folder shapes inside it.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Experiments, Highlights, Notes, Paper, Project
from settings import get_vault_path
from vault.models import ExperimentFilesWritten, Highlight, Note, NoteInput, RunArtifacts

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


async def write_paper_asset(session: AsyncSession, paper_id: uuid.UUID, kind: Literal["pdf", "parsed"], content: bytes | dict) -> Path:
    """Writes into `library/papers/<canonical-id>/` — global, shared across
    every project the paper is a member of (D25)."""
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise ValueError(f"paper {paper_id} not found")

    paper_dir = get_vault_path() / "library" / "papers" / paper.canonical_id
    paper_dir.mkdir(parents=True, exist_ok=True)

    if kind == "pdf":
        path = paper_dir / "paper.pdf"
        path.write_bytes(content if isinstance(content, bytes) else bytes(content))
    else:
        path = paper_dir / "parsed.json"
        path.write_text(json.dumps(content, indent=2), encoding="utf-8")

    return path


async def write_highlight(
    session: AsyncSession,
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    anchor_id: uuid.UUID,
    quote: str,
    prefix: str,
    suffix: str,
    section_heading: str | None,
    char_start: int,
    char_end: int,
    comment: str | None = None,
    color: str | None = None,
    note_id: uuid.UUID | None = None,
) -> Highlight:
    """Writes the vault mirror file and the `highlights` + `quote_anchors`
    rows together (D4). The anchor itself is validated by the caller
    (Provenance's `validate_and_anchor`) before this is called — Vault
    Writer sits *before* Provenance in MODULES.md's dependency order, so it
    cannot call back into it; this takes an already-anchored quote.
    """
    project = await session.get(Project, project_id)
    paper = await session.get(Paper, paper_id)
    if project is None or paper is None:
        raise ValueError("project or paper not found")

    mirror_path = get_vault_path() / "projects" / project.slug / "papers" / "highlights" / f"{paper.canonical_id}.json"
    mirror_path.parent.mkdir(parents=True, exist_ok=True)
    entries = json.loads(mirror_path.read_text(encoding="utf-8")) if mirror_path.exists() else []

    highlight_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    entries.append(
        {
            "id": str(highlight_id),
            "quote": quote,
            "prefix": prefix,
            "suffix": suffix,
            "char_offsets": [char_start, char_end],
            "section_heading": section_heading,
            "comment": comment,
            "color": color,
            "note_id": str(note_id) if note_id else None,
            "created_at": created_at.isoformat(),
        }
    )
    try:
        mirror_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except OSError as exc:
        raise VaultWriteFailed(f"could not write {mirror_path}: {exc}") from exc

    try:
        row = Highlights(
            id=highlight_id,
            project_id=project_id,
            paper_id=paper_id,
            anchor_id=anchor_id,
            note_id=note_id,
            comment=comment,
            color=color,
            created_at=created_at,
        )
        session.add(row)
        await session.flush()
    except Exception as exc:
        raise VaultWriteFailed(f"highlight file written but index update failed: {exc}") from exc

    return Highlight(
        id=row.id,
        project_id=project_id,
        paper_id=paper_id,
        anchor_id=anchor_id,
        quote=quote,
        char_start=char_start,
        char_end=char_end,
        section_heading=section_heading,
        comment=comment,
        color=color,
        note_id=note_id,
        created_at=row.created_at,
    )


async def write_experiment_files(
    session: AsyncSession, experiment_id: uuid.UUID, notebook: bytes, run: RunArtifacts | None = None
) -> ExperimentFilesWritten:
    """Writes `notebook.ipynb` (+ a default `requirements.txt`) under
    `experiments/<exp-slug>/` and updates `experiments.notebook_path` in the
    same operation (MODULES.md, D3/D4). When `run` is supplied (Execution
    Sandbox's `run_all`, Phase 2.2), also writes the captured container log
    to `experiments/<exp>/runs/<run_id>/stdout.log` and returns its
    vault-relative path as `stdout_ref` — the value `experiment_runs.stdout_ref`
    stores. `run.artifacts` is metadata only (Vault Writer does not move
    those files; the run container already wrote them into this same
    directory via its rw mount).
    """
    experiment = await session.get(Experiments, experiment_id)
    if experiment is None:
        raise ValueError(f"experiment {experiment_id} not found")
    project = await session.get(Project, experiment.project_id)
    if project is None:
        raise ValueError(f"project {experiment.project_id} not found")

    exp_dir = get_vault_path() / "projects" / project.slug / "experiments" / experiment.slug
    notebook_path = f"projects/{project.slug}/experiments/{experiment.slug}/notebook.ipynb"
    requirements_path = exp_dir / "requirements.txt"
    stdout_ref: str | None = None
    try:
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "notebook.ipynb").write_bytes(notebook)
        if not requirements_path.exists():
            requirements_path.write_text("", encoding="utf-8")
        if run is not None:
            run_dir = exp_dir / "runs" / str(run.run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "stdout.log").write_bytes(run.stdout)
            stdout_ref = f"projects/{project.slug}/experiments/{experiment.slug}/runs/{run.run_id}/stdout.log"
    except OSError as exc:
        raise VaultWriteFailed(f"could not write {notebook_path}: {exc}") from exc

    try:
        experiment.notebook_path = notebook_path
        await session.flush()
    except Exception as exc:
        raise VaultWriteFailed(f"notebook file written but index update failed: {exc}") from exc

    return ExperimentFilesWritten(notebook_path=notebook_path, stdout_ref=stdout_ref)
