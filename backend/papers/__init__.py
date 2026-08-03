"""Paper Pipeline — turns a paper reference into a fetched, parsed, extracted,
validated global paper record (MODULES.md).

`resolve_canonical_id` shipped in Phase 1.3 for Search Federation's dedup;
Phase 1.4 adds the rest: fetch (OA only, invariant #3), docling parse,
extractive-card extraction (D22/D24), and open-only enrichment.
"""

import asyncio
import re
import uuid
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import db
import jobs
import memory
import settings
from db.models import Paper as PaperRow
from db.models import PaperCards, PaperContent, QuoteAnchors
from graph import write_llm_edges, write_metadata_edges
from graph.models import LLMEdge, MetadataEdge
from llm import LLMError, Message, complete_structured
from papers.fetch import download_pdf, resolve_oa_pdf_url
from papers.models import Paper, PaperCardField, PaperContentView, PaperInput, SourceIds
from papers.parser import parse_pdf
from provenance import validate_and_anchor
from settings import get_vault_path
from vault import write_paper_asset

_DOI_PREFIX = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_ARXIV_PREFIX = re.compile(r"^\s*arxiv:\s*", re.IGNORECASE)
_ARXIV_VERSION_SUFFIX = re.compile(r"v\d+$", re.IGNORECASE)
_OPENALEX_PREFIX = re.compile(r"^\s*https?://openalex\.org/", re.IGNORECASE)
_S2_CORPUS_PREFIX = re.compile(r"^\s*corpusid:\s*", re.IGNORECASE)

# SAQ's own job default is 10s (fine for most jobs, far too short for these).
# docling's first parse also cold-downloads its OCR/layout models.
_PARSE_JOB_TIMEOUT_S = 600
_EXTRACT_JOB_TIMEOUT_S = 180
_ENRICH_JOB_TIMEOUT_S = 30
_EMBED_JOB_TIMEOUT_S = 120

_FIELD_KEYS = ("problem", "method", "datasets", "results", "limitations")

# Which extractive-card fields double as LLM-derived graph edges (D26), and
# which node type/relation each becomes — a paper-intrinsic edge is only
# ever asserted from a field that already passed Provenance for this paper
# (the same anchor a `paper_cards` row carries), never a separate claim.
_GRAPH_EDGE_FIELDS: dict[str, tuple[str, str]] = {
    "datasets": ("dataset", "uses_dataset"),
    "method": ("method", "related_method"),
}


def _normalise_doi(doi: str) -> str:
    return _DOI_PREFIX.sub("", doi).strip().lower()


def _normalise_arxiv_id(arxiv_id: str) -> str:
    return _ARXIV_VERSION_SUFFIX.sub("", _ARXIV_PREFIX.sub("", arxiv_id).strip())


def _normalise_openalex_id(openalex_id: str) -> str:
    return _OPENALEX_PREFIX.sub("", openalex_id).strip()


def _normalise_s2_id(s2_id: str) -> str:
    return _S2_CORPUS_PREFIX.sub("", s2_id).strip()


def resolve_canonical_id(source_ids: SourceIds) -> str:
    """The one function that derives `canonical_id` — never re-derived
    elsewhere (Rules.md). Priority is DOI -> arXiv -> OpenAlex/S2 (D25); the
    `<source>:` prefix on the result doubles as `papers.canonical_id_source`.
    """
    if source_ids.doi:
        return f"doi:{_normalise_doi(source_ids.doi)}"
    if source_ids.arxiv_id:
        return f"arxiv:{_normalise_arxiv_id(source_ids.arxiv_id)}"
    if source_ids.openalex_id:
        return f"openalex:{_normalise_openalex_id(source_ids.openalex_id)}"
    if source_ids.s2_id:
        return f"s2:{_normalise_s2_id(source_ids.s2_id)}"
    raise ValueError("no source id provided — cannot derive a canonical id")


def paper_from_row(row: PaperRow) -> Paper:
    return Paper(
        id=row.id,
        canonical_id=row.canonical_id,
        title=row.title,
        abstract=row.abstract,
        source_url=row.source_url,
        pdf_origin=row.pdf_origin,
        fetch_state=row.fetch_state,
        parse_state=row.parse_state,
        embed_state=row.embed_state,
        extract_state=row.extract_state,
    )


async def add_paper(session: AsyncSession, paper_input: PaperInput) -> Paper:
    """Fetches (OA only) or dedupes on canonical id, then enqueues parse
    (embed lands in Phase 1.7, extract is dispatched once parse completes)."""
    canonical_id = resolve_canonical_id(paper_input.source_ids)

    existing = await session.scalar(select(PaperRow).where(PaperRow.canonical_id == canonical_id))
    if existing is not None:
        return paper_from_row(existing)

    row = PaperRow(
        id=uuid.uuid4(),
        canonical_id=canonical_id,
        canonical_id_source=canonical_id.split(":", 1)[0],
        doi=paper_input.source_ids.doi,
        arxiv_id=paper_input.source_ids.arxiv_id,
        openalex_id=paper_input.source_ids.openalex_id,
        s2_id=paper_input.source_ids.s2_id,
        title=paper_input.title or canonical_id,
        abstract=paper_input.abstract,
        source_url=paper_input.source_url,
    )
    session.add(row)
    await session.flush()

    oa = await resolve_oa_pdf_url(paper_input.source_ids, paper_input.pdf_url)
    if oa is not None:
        pdf_url, origin = oa
        try:
            pdf_bytes = await download_pdf(pdf_url)
        except httpx.HTTPError:
            row.fetch_state = "degraded"
        else:
            path = await write_paper_asset(session, row.id, "pdf", pdf_bytes)
            row.pdf_path = str(path.relative_to(get_vault_path()))
            row.pdf_origin = origin
            row.fetch_state = "done"
    else:
        row.fetch_state = "degraded"

    await session.flush()

    if row.fetch_state == "done":
        await jobs.enqueue("parse_paper_job", paper_id=str(row.id), timeout=_PARSE_JOB_TIMEOUT_S)

    return paper_from_row(row)


async def get_paper(session: AsyncSession, paper_id: uuid.UUID) -> Paper | None:
    row = await session.get(PaperRow, paper_id)
    return paper_from_row(row) if row else None


async def get_pdf_path(session: AsyncSession, paper_id: uuid.UUID) -> str | None:
    """The vault-relative PDF path, or `None` when there is no OA copy (D23)."""
    row = await session.get(PaperRow, paper_id)
    return row.pdf_path if row else None


async def get_paper_content(session: AsyncSession, paper_id: uuid.UUID) -> PaperContentView | None:
    content = await session.get(PaperContent, paper_id)
    if content is None:
        return None
    return PaperContentView(
        full_text=content.full_text,
        sections=content.sections,
        references=content.references_,
        datasets=content.datasets,
        code_links=content.code_links,
        parsed_at=content.parsed_at,
    )


async def get_paper_card(session: AsyncSession, paper_id: uuid.UUID) -> list[PaperCardField]:
    rows = (
        await session.execute(
            select(PaperCards, QuoteAnchors)
            .join(QuoteAnchors, PaperCards.anchor_id == QuoteAnchors.id)
            .where(PaperCards.paper_id == paper_id)
        )
    ).all()
    return [
        PaperCardField(
            field_key=card.field_key,
            value=card.value,
            anchor_id=card.anchor_id,
            section_heading=anchor.section_heading,
            char_start=anchor.char_start,
            char_end=anchor.char_end,
        )
        for card, anchor in rows
    ]


async def parse_paper_job(_ctx: dict, *, paper_id: str) -> None:
    """docling parse (D23); writes `paper_content` and the vault's `parsed.json`."""
    pid = uuid.UUID(paper_id)
    vault_root = get_vault_path()

    async with db.session() as session:
        paper = await session.get(PaperRow, pid)
        if paper is None or not paper.pdf_path:
            return
        paper.parse_state = "running"
        pdf_path = vault_root / paper.pdf_path

    parsed = await asyncio.to_thread(parse_pdf, pdf_path)

    async with db.session() as session:
        session.add(
            PaperContent(
                paper_id=pid,
                full_text=parsed.full_text,
                sections=parsed.sections,
                references_=parsed.references,
                datasets=[],
                code_links=[],
                parser_version=parsed.parser_version,
                parsed_at=datetime.now(timezone.utc),
            )
        )
        await write_paper_asset(session, pid, "parsed", parsed.model_dump())
        paper = await session.get(PaperRow, pid)
        paper.parse_state = "done"

    await jobs.enqueue("extract_card_job", paper_id=paper_id, timeout=_EXTRACT_JOB_TIMEOUT_S)
    await jobs.enqueue("embed_paper_job", paper_id=paper_id, timeout=_EMBED_JOB_TIMEOUT_S)


_EXTRACTION_PROMPT = (
    "You extract five fields from an academic paper's text, strictly from the "
    "paper's own content and section headings — no outside knowledge, no "
    "inference. For each field you can support, return the VERBATIM quote "
    "(copied exactly, character for character, from the text) that states it, "
    "plus a few words of the text immediately before (prefix) and after "
    "(suffix) the quote, for disambiguation. Omit a field entirely if the "
    "paper does not state it — never paraphrase, never guess."
)

# Keeps the extraction context bounded; very long papers are truncated for
# v1 (Memory Index's section-aware chunking is Phase 1.7).
_MAX_EXTRACTION_CHARS = 60_000


class _ExtractedSpan(BaseModel):
    quote: str
    prefix: str = ""
    suffix: str = ""


class _ExtractedCard(BaseModel):
    problem: _ExtractedSpan | None = None
    method: _ExtractedSpan | None = None
    datasets: _ExtractedSpan | None = None
    results: _ExtractedSpan | None = None
    limitations: _ExtractedSpan | None = None


async def _set_extract_state(pid: uuid.UUID, state: str) -> None:
    async with db.session() as session:
        paper = await session.get(PaperRow, pid)
        if paper is not None:
            paper.extract_state = state


async def _set_embed_state(pid: uuid.UUID, state: str) -> None:
    async with db.session() as session:
        paper = await session.get(PaperRow, pid)
        if paper is not None:
            paper.embed_state = state


async def embed_paper_job(_ctx: dict, *, paper_id: str) -> None:
    """Chunks and embeds the paper's abstract and sections into `paper_chunks`
    (Memory Index's `chunk_and_embed_job`, called directly rather than
    enqueued twice — one job here avoids a two-job completion race over
    `embed_state`)."""
    pid = uuid.UUID(paper_id)
    await _set_embed_state(pid, "running")
    try:
        await memory.chunk_and_embed_job({}, source_type="abstract", source_id=paper_id)
        await memory.chunk_and_embed_job({}, source_type="paper_section", source_id=paper_id)
    except Exception:
        await _set_embed_state(pid, "failed")
        raise
    await _set_embed_state(pid, "done")


async def extract_card_job(_ctx: dict, *, paper_id: str) -> None:
    """Auxiliary-tier extraction of the five standard fields (D22), each
    validated through Provenance before a `paper_cards` row is written."""
    pid = uuid.UUID(paper_id)
    await _set_extract_state(pid, "running")

    try:
        async with db.session() as session:
            content = await session.get(PaperContent, pid)
            if content is None:
                return

            model_settings = await settings.get_settings(session)
            model_name = model_settings.auxiliary_model or model_settings.primary_model or "unknown"

            extracted = await complete_structured(
                messages=[
                    Message(role="system", content=_EXTRACTION_PROMPT),
                    Message(role="user", content=content.full_text[:_MAX_EXTRACTION_CHARS]),
                ],
                schema=_ExtractedCard,
                tier="auxiliary",
                timeout=60,
            )

            paper = await session.get(PaperRow, pid)
            llm_edges: list[LLMEdge] = []
            for field_key in _FIELD_KEYS:
                span = getattr(extracted, field_key)
                if span is None or not span.quote:
                    continue
                anchor = await validate_and_anchor(session, pid, span.quote, span.prefix, span.suffix)
                if anchor is None:
                    continue  # dropped: absence of a row *is* "not stated" (Schema.md)
                session.add(
                    PaperCards(
                        id=uuid.uuid4(),
                        paper_id=pid,
                        field_key=field_key,
                        value=span.quote,
                        anchor_id=anchor.id,
                        extracted_by_model=model_name,
                    )
                )
                edge_kind = _GRAPH_EDGE_FIELDS.get(field_key)
                if edge_kind is not None:
                    dst_type, relation = edge_kind
                    llm_edges.append(
                        LLMEdge(
                            src_type="paper",
                            src_id=paper.canonical_id,
                            dst_type=dst_type,
                            dst_id=slugify(span.quote[:80]),
                            relation=relation,
                        )
                    )
            if llm_edges:
                await write_llm_edges(session, pid, llm_edges)

            paper.extract_state = "done"
    except (*LLMError, RuntimeError):
        # Recorded so the Library View shows "failed" rather than a stuck
        # "queued" badge indistinguishable from "never started" — then
        # re-raised so SAQ's own job-failure tracking still sees it.
        await _set_extract_state(pid, "failed")
        raise

    await jobs.enqueue("enrich_paper_job", paper_id=paper_id, timeout=_ENRICH_JOB_TIMEOUT_S)


async def enrich_paper_job(_ctx: dict, *, paper_id: str) -> None:
    """Papers with Code / GitHub enrichment, on open only (D21/D26).

    As of this build, paperswithcode.com's API redirects to huggingface.co
    — the service appears discontinued. This degrades gracefully (no code
    links, no metadata edges) rather than failing the job; nothing
    downstream depends on enrichment succeeding.
    """
    pid = uuid.UUID(paper_id)
    async with db.session() as session:
        paper = await session.get(PaperRow, pid)
        if paper is None or not paper.arxiv_id:
            return

        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(
                    "https://paperswithcode.com/api/v1/papers/", params={"arxiv_id": paper.arxiv_id}
                )
            if response.status_code != 200:
                return
            results = response.json().get("results", [])
        except httpx.HTTPError:
            return

        edges = [
            MetadataEdge(
                src_type="paper",
                src_id=paper.canonical_id,
                dst_type="repo",
                dst_id=repo_url,
                relation="has_code",
                source_api="pwc",
            )
            for entry in results
            if (repo_url := entry.get("url_source_url") or entry.get("url_abs"))
        ]
        if edges:
            await write_metadata_edges(session, pid, edges)
