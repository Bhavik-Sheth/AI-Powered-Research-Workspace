"""The project's own references — the paper set `\\cite` autocomplete and
BibTeX export both draw from (D34). One cite-key rule lives here so a key
inserted while autocompleting always resolves in the exported `.bib`
(Rules.md: one decision, one module).
"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Paper, ProjectPapers
from writing.models import Reference

_TITLE_WORD = re.compile(r"[A-Za-z]{4,}")
_NON_ALPHA = re.compile(r"[^a-z]")


def _cite_key_base(title: str, authors: list[str], year: int | None) -> str:
    surname = _NON_ALPHA.sub("", authors[0].split()[-1].lower()) if authors else "anon"
    word_match = _TITLE_WORD.search(title)
    title_word = word_match.group(0).lower() if word_match else "paper"
    return f"{surname}{year or ''}{title_word}"


async def list_references(session: AsyncSession, project_id: uuid.UUID) -> list[Reference]:
    """Every paper in the project's library, each carrying the same
    deterministic `cite_key` autocomplete inserts and BibTeX exports under —
    collisions get a/b/c suffixes in title order, so the result is stable
    across calls for an unchanged library."""
    rows = await session.execute(
        select(Paper).join(ProjectPapers, ProjectPapers.paper_id == Paper.id).where(ProjectPapers.project_id == project_id).order_by(Paper.title)
    )
    papers = rows.scalars().all()

    used_keys: set[str] = set()
    references: list[Reference] = []
    for paper in papers:
        authors = paper.metadata_.get("authors") or []
        year = paper.metadata_.get("year")
        base = _cite_key_base(paper.title, authors, year)
        key = base
        suffix = ord("a")
        while key in used_keys:
            key = f"{base}{chr(suffix)}"
            suffix += 1
        used_keys.add(key)
        references.append(Reference(paper_id=paper.id, cite_key=key, title=paper.title, authors=authors, year=year))
    return references


async def autocomplete_citations(session: AsyncSession, project_id: uuid.UUID, prefix: str) -> list[Reference]:
    """Backs `\\cite{` autocomplete (MODULES.md) — a plain prefix/substring
    match over the project's own reference list. Not routed through Memory
    Index's `query_memory`: that call embeds the query text before it can
    return anything, which is the wrong tool for a live per-keystroke match
    against a 1-2 character prefix (and would rerank nonsense for one)."""
    references = await list_references(session, project_id)
    if not prefix:
        return references
    needle = prefix.lower()
    return [
        ref
        for ref in references
        if needle in ref.cite_key.lower() or needle in ref.title.lower() or any(needle in author.lower() for author in ref.authors)
    ]


def format_bibtex(references: list[Reference], papers_by_id: dict[uuid.UUID, Paper]) -> str:
    """BibTeX for the project's library (MODULES.md `export_bibtex`).
    `@misc` uniformly: Phase 1's paper metadata doesn't reliably know a
    publication venue, so asserting `@article`/`@inproceedings` would claim
    more than is known — `@misc` plus `howpublished`/`url` is the honest
    generic entry type."""
    entries = []
    for ref in references:
        paper = papers_by_id[ref.paper_id]
        fields = [f'  title = {{{ref.title}}}']
        if ref.authors:
            fields.append(f'  author = {{{" and ".join(ref.authors)}}}')
        if ref.year:
            fields.append(f"  year = {{{ref.year}}}")
        if paper.source_url:
            fields.append(f'  howpublished = {{\\url{{{paper.source_url}}}}}')
        entries.append(f"@misc{{{ref.cite_key},\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries)
