"""Paper Pipeline — turns a paper reference into a fetched, parsed, extracted,
validated global paper record (MODULES.md).

`resolve_canonical_id` shipped in Phase 1.3 for Search Federation's dedup;
Phase 1.4 adds the rest: fetch (OA only, invariant #3), docling parse,
extractive-card extraction (D22/D24), and open-only enrichment.
"""

import asyncio
import logging
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
from config import get_config
from db.models import Paper as PaperRow
from db.models import PaperCards, PaperContent, QuoteAnchors
from graph import write_llm_edges, write_metadata_edges
from graph.models import LLMEdge, MetadataEdge
from llm import LLMError, Message, complete_structured
from papers.fetch import download_pdf, resolve_oa_pdf_url
from papers.models import Paper, PaperCardField, PaperContentView, PaperInput, ReferenceInfo, SourceIds
from papers.parser import parse_pdf
from provenance import validate_and_anchor
from settings import get_vault_path
from vault import write_paper_asset

logger = logging.getLogger(__name__)

_DOI_PREFIX = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_ARXIV_PREFIX = re.compile(r"^\s*arxiv:\s*", re.IGNORECASE)
_ARXIV_VERSION_SUFFIX = re.compile(r"v\d+$", re.IGNORECASE)
_OPENALEX_PREFIX = re.compile(r"^\s*https?://openalex\.org/", re.IGNORECASE)
_S2_CORPUS_PREFIX = re.compile(r"^\s*corpusid:\s*", re.IGNORECASE)

# SAQ's own job default is 10s (fine for most jobs, far too short for these).
# docling's first parse also cold-downloads its OCR/layout models.
# `_FETCH_JOB_TIMEOUT_S`: Unpaywall's own 20s timeout plus `download_pdf`'s
# 60s, plus headroom — this used to run inline on `POST .../papers` (Bug
# Fix Plan Phase 6.12), leaving the "Add to library" click hanging for up
# to that long; it is now `fetch_pdf_job`, off the request path.
_FETCH_JOB_TIMEOUT_S = 90
_PARSE_JOB_TIMEOUT_S = 600
_EXTRACT_JOB_TIMEOUT_S = 180
_ENRICH_JOB_TIMEOUT_S = 30
_EMBED_JOB_TIMEOUT_S = 120

_FIELD_KEYS = ("problem", "method", "datasets", "results", "limitations")

# Top N references shown in the Reader's References box (Phase 6.3), ranked
# by citation count where a source provides one.
_TOP_REFERENCES = 5
_TRACE_REFERENCES_JOB_TIMEOUT_S = 30

# A raw PDF-parsed reference string sometimes carries an inline arXiv id or
# DOI even with no API record for the *citing* paper — e.g. a preprint's own
# bibliography still names arXiv ids for what it cites. Extracting an
# explicit, already-present id from text is parsing, not invention; no
# fabricated id is ever produced for a reference that names none.
_ARXIV_ID_IN_TEXT = re.compile(r"arxiv:\s*(\d{4}\.\d{4,5})", re.IGNORECASE)
_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s,;()]+")

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
        references_state=row.references_state,
    )


async def _fetch_pdf(session: AsyncSession, row: PaperRow, source_ids: SourceIds, pdf_url: str | None) -> None:
    """OA-only fetch (invariant #3), shared by `add_paper` and `reprocess_paper`."""
    oa = await resolve_oa_pdf_url(source_ids, pdf_url)
    if oa is not None:
        resolved_url, origin = oa
        try:
            pdf_bytes = await download_pdf(resolved_url)
        except httpx.HTTPError:
            row.fetch_state = "degraded"
        else:
            path = await write_paper_asset(session, row.id, "pdf", pdf_bytes)
            row.pdf_path = str(path.relative_to(get_vault_path()))
            row.pdf_origin = origin
            row.fetch_state = "done"
    else:
        row.fetch_state = "degraded"


async def add_paper(session: AsyncSession, paper_input: PaperInput) -> Paper:
    """Dedupes on canonical id, or creates the row and enqueues `fetch_pdf_job`
    (Bug Fix Plan Phase 6.12): OA resolution + download used to run inline
    here, leaving `POST .../papers` ("Add to library") hanging for up to
    ~80s on a slow/rate-limited source; now the row comes back immediately
    with `fetch_state='queued'` and the fetch (then parse, then
    embed/extract/trace once parse completes) happens off the request
    path, same shape every other pipeline stage already used."""
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
        fetch_state="queued",
    )
    session.add(row)
    await session.flush()

    await jobs.enqueue("fetch_pdf_job", paper_id=str(row.id), pdf_url=paper_input.pdf_url, timeout=_FETCH_JOB_TIMEOUT_S)

    return paper_from_row(row)


async def fetch_pdf_job(_ctx: dict, *, paper_id: str, pdf_url: str | None) -> None:
    """The background counterpart of the old inline `_fetch_pdf` call in
    `add_paper` (Bug Fix Plan Phase 6.12) — same OA-only fetch (invariant
    #3), driven to `fetch_state`'s usual `done`/`degraded`, then enqueues
    `parse_paper_job` on success exactly as `add_paper` used to do inline."""
    pid = uuid.UUID(paper_id)
    async with db.session() as session:
        row = await session.get(PaperRow, pid)
        if row is None:
            return
        row.fetch_state = "running"
        source_ids = SourceIds(doi=row.doi, arxiv_id=row.arxiv_id, openalex_id=row.openalex_id, s2_id=row.s2_id)
        await _fetch_pdf(session, row, source_ids, pdf_url)
        fetch_state = row.fetch_state

    if fetch_state == "done":
        await jobs.enqueue("parse_paper_job", paper_id=paper_id, timeout=_PARSE_JOB_TIMEOUT_S)


_TERMINAL_STAGE_STATES = ("done", "degraded")


async def reprocess_paper(session: AsyncSession, paper_id: uuid.UUID) -> Paper | None:
    """Re-drives whichever stage stalled or failed — the Library's `Retry`
    action (Bug Fix Plan Phase 1.3). Only stages not already in a terminal
    state (`done`/`degraded`) are reset to `queued` and re-enqueued; a stage
    downstream of one still incomplete is left alone, since its job assumes
    the stage before it already produced output.

    A fetch retry (Phase 6.12) enqueues `fetch_pdf_job` and returns
    immediately, same as a fresh `add_paper` — it does not know yet whether
    the retry will land on `done`, so it can't decide here whether parse
    should also be queued; `fetch_pdf_job` itself does that on success,
    same as it does for a brand new paper."""
    row = await session.get(PaperRow, paper_id)
    if row is None:
        return None

    if row.fetch_state not in _TERMINAL_STAGE_STATES:
        row.fetch_state = "queued"
        await session.flush()
        await jobs.enqueue("fetch_pdf_job", paper_id=str(row.id), pdf_url=None, timeout=_FETCH_JOB_TIMEOUT_S)
        return paper_from_row(row)

    if row.fetch_state == "done" and row.parse_state not in _TERMINAL_STAGE_STATES:
        row.parse_state = "queued"
        await session.flush()
        await jobs.enqueue("parse_paper_job", paper_id=str(row.id), timeout=_PARSE_JOB_TIMEOUT_S)
        return paper_from_row(row)

    if row.parse_state == "done":
        if row.extract_state not in _TERMINAL_STAGE_STATES:
            row.extract_state = "queued"
            await session.flush()
            await jobs.enqueue("extract_card_job", paper_id=str(row.id), timeout=_EXTRACT_JOB_TIMEOUT_S)
        if row.embed_state not in _TERMINAL_STAGE_STATES:
            row.embed_state = "queued"
            await session.flush()
            await jobs.enqueue("embed_paper_job", paper_id=str(row.id), timeout=_EMBED_JOB_TIMEOUT_S)
        if row.references_state not in _TERMINAL_STAGE_STATES:
            row.references_state = "queued"
            await session.flush()
            await jobs.enqueue("trace_references_job", paper_id=str(row.id), timeout=_TRACE_REFERENCES_JOB_TIMEOUT_S)

    return paper_from_row(row)


async def get_paper(session: AsyncSession, paper_id: uuid.UUID, *, heal: bool = False) -> Paper | None:
    """`heal=True` is the open-paper read path only (`GET /api/papers/:id`):
    a paper whose reference trace has never run (`references_state` still at
    its `queued` default — Phase 6.3 papers predate the trace entirely, and
    a handful of Phase 6.4-era papers may have raced the enqueue in
    `parse_paper_job`) gets it enqueued exactly once here. Every other
    caller of this function (harness tools, matrix, internal reads) passes
    `heal=False` (the default) so a plain lookup never has a side effect."""
    row = await session.get(PaperRow, paper_id)
    if row is None:
        return None
    if heal and row.references_state == "queued":
        await jobs.enqueue("trace_references_job", paper_id=str(row.id), timeout=_TRACE_REFERENCES_JOB_TIMEOUT_S)
    return paper_from_row(row)


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
    await jobs.enqueue("trace_references_job", paper_id=paper_id, timeout=_TRACE_REFERENCES_JOB_TIMEOUT_S)


_EXTRACTION_PROMPT = (
    "You extract five fields from an academic paper's text, strictly from the "
    "paper's own content and section headings — no outside knowledge, no "
    "inference. For each field you can support, return the VERBATIM quote "
    "(copied exactly, character for character, from the text) that states it, "
    "plus a few words of the text immediately before (prefix) and after "
    "(suffix) the quote, for disambiguation. Omit a field entirely if the "
    "paper does not state it — never paraphrase, never guess."
)

# Per-window extraction budget: a paper longer than this is extracted over
# several section-aware windows rather than one truncated whole-paper call
# (Bug Fix Plan Phase 1.2) — this bounds each window, not the paper.
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


def _section_windows(full_text: str, sections: list[dict]) -> list[str]:
    """Groups the paper into bounded, section-aligned text windows, in
    document order, each no larger than `_MAX_EXTRACTION_CHARS` — so a
    long paper is extracted from in full rather than truncated to its
    opening chars. Falls back to fixed-size chunks of `full_text` when
    docling found no section boundaries."""
    bounds = [(s["char_start"], s["char_end"]) for s in sections if s["char_end"] > s["char_start"]]
    if not bounds:
        bounds = [(0, len(full_text))]

    merged: list[tuple[int, int]] = []
    window_start, window_end = bounds[0]
    for start, end in bounds[1:]:
        if end - window_start <= _MAX_EXTRACTION_CHARS:
            window_end = end
        else:
            merged.append((window_start, window_end))
            window_start, window_end = start, end
    merged.append((window_start, window_end))

    windows: list[str] = []
    for start, end in merged:
        text = full_text[start:end]
        windows.extend(text[i : i + _MAX_EXTRACTION_CHARS] for i in range(0, len(text), _MAX_EXTRACTION_CHARS))
    return windows


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
    """Auxiliary-tier extraction of the five standard fields (D22) over
    bounded, section-aware windows, each validated through Provenance
    before a `paper_cards` row is written. One window's LLM call failing
    skips just that window rather than the whole paper (Bug Fix Plan
    Phase 1.2); the card/state commit is a separate unit of work from the
    graph-edge write, so a failure writing edges can never roll back
    cards that already validated."""
    pid = uuid.UUID(paper_id)
    await _set_extract_state(pid, "running")

    llm_edges: list[LLMEdge] = []
    try:
        async with db.session() as session:
            content = await session.get(PaperContent, pid)
            if content is None:
                return

            model_settings = await settings.get_settings(session)
            model_name = model_settings.auxiliary_model or model_settings.primary_model or "unknown"

            merged: dict[str, _ExtractedSpan] = {}
            any_window_succeeded = False
            for window_text in _section_windows(content.full_text, content.sections):
                try:
                    extracted = await complete_structured(
                        messages=[
                            Message(role="system", content=_EXTRACTION_PROMPT),
                            Message(role="user", content=window_text),
                        ],
                        schema=_ExtractedCard,
                        tier="auxiliary",
                        timeout=60,
                    )
                except (*LLMError, RuntimeError) as exc:
                    # Skipping just this window (Phase 1.2) must not mean
                    # losing the reason why — without this, every window
                    # failing surfaces only as the generic "every
                    # extraction window failed" below, with no way to tell
                    # a rate limit from a schema/timeout failure short of
                    # reproducing it by hand.
                    logger.warning(
                        "event=extraction_window_failed paper_id=%s model=%s error=%r",
                        pid,
                        model_name,
                        exc,
                    )
                    continue
                any_window_succeeded = True
                for field_key in _FIELD_KEYS:
                    if field_key in merged:
                        continue  # first window to state a field wins
                    span = getattr(extracted, field_key)
                    if span is not None and span.quote:
                        merged[field_key] = span

            if not any_window_succeeded:
                raise RuntimeError(f"every extraction window failed for paper {pid}")

            paper = await session.get(PaperRow, pid)
            for field_key, span in merged.items():
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

            paper.extract_state = "done"
    except (*LLMError, RuntimeError):
        # Recorded so the Library View shows "failed" rather than a stuck
        # "queued" badge indistinguishable from "never started" — then
        # re-raised so SAQ's own job-failure tracking still sees it.
        await _set_extract_state(pid, "failed")
        raise

    if llm_edges:
        async with db.session() as session:
            await write_llm_edges(session, pid, llm_edges)

    await jobs.enqueue("enrich_paper_job", paper_id=paper_id, timeout=_ENRICH_JOB_TIMEOUT_S)


# Tier 1 (text scan): domains carrying implementation code vs. datasets, in
# one alternation so the paper's full text is scanned once. github/gitlab
# and huggingface.co model/Space links are code; huggingface.co/datasets,
# kaggle.com/datasets and zenodo.org (DOI-style archival records, almost
# always a dataset/artifact release) are datasets — see the classifier
# below for the exact split.
_SOURCE_URL = re.compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com|huggingface\.co|kaggle\.com|zenodo\.org)/[^\s)\"'<>\]]+",
    re.IGNORECASE,
)


def _harvest_text_links(full_text: str) -> tuple[list[dict], list[dict]]:
    """Tier 1 (D26 amendment): URLs verbatim in the paper's own parsed text
    — about as metadata-exact as it gets, hence `source_api='text'` and
    `provenance='metadata'` (no inference, just extraction of an explicit
    string already present in the PDF)."""
    code: list[dict] = []
    datasets: list[dict] = []
    seen_code: set[str] = set()
    seen_datasets: set[str] = set()
    for raw_url in _SOURCE_URL.findall(full_text):
        url = raw_url.rstrip(".,;:)")
        is_dataset = "zenodo.org" in url or "/datasets/" in url  # HF or Kaggle dataset path, or a Zenodo record
        bucket, seen = (datasets, seen_datasets) if is_dataset else (code, seen_code)
        if url in seen:
            continue
        seen.add(url)
        bucket.append({"name": url.rsplit("/", 1)[-1], "url": url, "source": "text"} if is_dataset else {"url": url, "source": "text"})
    return code, datasets


# HuggingFace's papers API (verified live against a known paper's arXiv id
# during Phase 6.5 tracer fire): `GET /api/papers/{arxiv_id}` returns 200
# with `linkedModels`/`linkedDatasets`/`linkedSpaces` — HF repos whose own
# README cites this arXiv id, not necessarily the paper authors' own
# canonical repo, but the real, still-live successor to Papers with Code
# (D26 amendment) and the best structured signal available post-PwC. A
# paper with no HF repo citing it 404s with an `{"error": ...}` body,
# handled as "found nothing", not a failure.
_HF_PAPERS_API_URL = "https://huggingface.co/api/papers/{arxiv_id}"
_HF_LINKED_MODELS_LIMIT = 3
_HF_LINKED_DATASETS_LIMIT = 3


async def _harvest_huggingface_links(arxiv_id: str) -> tuple[list[dict], list[dict]]:
    """Tier 2, only called when tier 1 found nothing. Defensive `.get()`
    throughout: an undocumented shape drift on HuggingFace's side must
    degrade to tier 3, never crash the job (Rules.md)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(_HF_PAPERS_API_URL.format(arxiv_id=arxiv_id))
        if response.status_code != 200:
            return [], []
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return [], []

    if not isinstance(payload, dict):
        return [], []

    code = [
        {"url": f"https://huggingface.co/{model_id}", "source": "huggingface"}
        for model in payload.get("linkedModels", [])[:_HF_LINKED_MODELS_LIMIT]
        if isinstance(model, dict) and (model_id := model.get("id"))
    ]
    datasets = [
        {"name": dataset_id, "url": f"https://huggingface.co/datasets/{dataset_id}", "source": "huggingface"}
        for dataset in payload.get("linkedDatasets", [])[:_HF_LINKED_DATASETS_LIMIT]
        if isinstance(dataset, dict) and (dataset_id := dataset.get("id"))
    ]
    return code, datasets


# Tier 3's search query (D26 amendment) — last resort, only when tiers 1
# and 2 both found nothing.
_FIRECRAWL_CODE_QUERY = '"{title}" official implementation'
_FIRECRAWL_RESULT_LIMIT = 5
_FIRECRAWL_CODE_DOMAINS = ("github.com", "gitlab.com")


async def _harvest_firecrawl_link(title: str) -> list[dict]:
    """Tier 3, reusing Phase 6.1's Firecrawl client — same optional-key
    degrade Search Federation already established: no key configured means
    no fallback, not a failure."""
    # Imported locally, not at module top: search/__init__.py imports
    # `resolve_canonical_id` from this module (papers), so a top-level
    # import of anything under `search` here would cycle — same pattern
    # `trace_references_job` already uses for `search.sources` imports.
    from search.sources import search_firecrawl

    config = get_config()
    if not config.firecrawl_api_key:
        return []
    try:
        hits = await search_firecrawl(
            _FIRECRAWL_CODE_QUERY.format(title=title), config.firecrawl_api_key, _FIRECRAWL_RESULT_LIMIT
        )
    except httpx.HTTPError:
        return []
    for hit in hits:
        if any(domain in hit.url for domain in _FIRECRAWL_CODE_DOMAINS):
            return [{"url": hit.url, "source": "firecrawl"}]
    return []


def _dataset_edge_dst_id(dataset: dict) -> str:
    """Node identity, split by trust (D26): a HuggingFace-sourced dataset
    already has a real dataset id/slug (used directly); a dataset name
    harvested from text gets the same light normalisation a concept node
    gets — dup-tolerant, under-merging beats false-merging."""
    return dataset["name"] if dataset.get("source") == "huggingface" else slugify(dataset["name"])


async def enrich_paper_job(_ctx: dict, *, paper_id: str) -> None:
    """Code/dataset provenance, on open only (D21/D26 amendment): the
    paper's own text, then HuggingFace's papers API (the real successor to
    the discontinued Papers with Code), then a Firecrawl search, each tier
    only tried if the previous one found nothing. Writes
    `paper_content.code_links`/`datasets` plus the corresponding
    `has_code`/`uses_dataset` metadata edges. Partial source failure
    degrades, it never fails the job — worst case, all three tiers find
    nothing and both fields legitimately stay `[]`."""
    pid = uuid.UUID(paper_id)
    async with db.session() as session:
        paper = await session.get(PaperRow, pid)
        content = await session.get(PaperContent, pid)
        if paper is None or content is None:
            return

        code, datasets = _harvest_text_links(content.full_text)

        if not code and not datasets and paper.arxiv_id:
            code, datasets = await _harvest_huggingface_links(paper.arxiv_id)

        if not code and not datasets:
            code = await _harvest_firecrawl_link(paper.title)

        if not code and not datasets:
            return

        content.code_links = code
        content.datasets = datasets

        edges = [
            MetadataEdge(
                src_type="paper",
                src_id=paper.canonical_id,
                dst_type="repo",
                dst_id=link["url"],
                relation="has_code",
                source_api=link["source"],
            )
            for link in code
        ] + [
            MetadataEdge(
                src_type="paper",
                src_id=paper.canonical_id,
                dst_type="dataset",
                dst_id=_dataset_edge_dst_id(dataset),
                relation="uses_dataset",
                source_api=dataset["source"],
            )
            for dataset in datasets
        ]
        if edges:
            await write_metadata_edges(session, pid, edges)


def _extract_ids_from_raw_reference(raw: str) -> SourceIds | None:
    """An explicit arXiv id or DOI already present in a PDF-parsed reference
    string — parsing, not invention (Rules.md "never invent"). `None` when
    the raw text names neither, which is common and expected."""
    arxiv_match = _ARXIV_ID_IN_TEXT.search(raw)
    doi_match = _DOI_IN_TEXT.search(raw)
    if arxiv_match is None and doi_match is None:
        return None
    return SourceIds(
        doi=doi_match.group(0) if doi_match else None,
        arxiv_id=arxiv_match.group(1) if arxiv_match else None,
    )


async def _set_references_state(pid: uuid.UUID, state: str) -> None:
    async with db.session() as session:
        paper = await session.get(PaperRow, pid)
        if paper is not None:
            paper.references_state = state


async def add_reference_stub(session: AsyncSession, source_ids: SourceIds, title: str) -> PaperRow:
    """A metadata-only reference stub (Phase 6.3) — title/authors/year/
    canonical id only, `fetch_state='skipped'` so no fetch/parse/extract job
    is ever enqueued for it. Dedupes on canonical id exactly like
    `add_paper`: if the referenced paper already has a row (a full paper, or
    an earlier trace's stub), that row is returned untouched rather than
    downgraded or duplicated."""
    canonical_id = resolve_canonical_id(source_ids)
    existing = await session.scalar(select(PaperRow).where(PaperRow.canonical_id == canonical_id))
    if existing is not None:
        return existing

    row = PaperRow(
        id=uuid.uuid4(),
        canonical_id=canonical_id,
        canonical_id_source=canonical_id.split(":", 1)[0],
        doi=source_ids.doi,
        arxiv_id=source_ids.arxiv_id,
        openalex_id=source_ids.openalex_id,
        s2_id=source_ids.s2_id,
        title=title or canonical_id,
        fetch_state="skipped",
    )
    session.add(row)
    await session.flush()
    return row


async def trace_references_job(_ctx: dict, *, paper_id: str) -> None:
    """API-first, PDF-section-fallback reference tracing (Phase 6.3, edges
    added Phase 6.4): resolves the top `_TOP_REFERENCES` references, by
    citation count where a source provides one, into metadata-only stub rows
    via `add_reference_stub`, then overwrites `paper_content.references` with
    the resolved list so the Reader's References box has a real title and a
    `paper_id` to navigate to for each entry. A reference this cannot
    resolve to any source id still renders (its raw text is kept) but has no
    `paper_id` — a "not stated" style degrade, never a fabricated link.

    Every reference that *did* resolve to a stub/full `papers` row also gets
    a `cites` `paper_edges` row via `write_metadata_edges` — the Graph View's
    real paper->paper tracebacks. `source_api` names whichever source
    actually resolved it (`openalex`/`s2`); the PDF-section fallback's
    inline-id case resolved no API at all, so its edges carry no
    `source_api` rather than fabricating one that wasn't queried.
    `references_state` tracks this stage exactly like `parse_state`/
    `extract_state`: `running` at the start, `done` on success, `failed` on
    an unhandled exception (this job is the job-worker boundary — the one
    place Rules.md permits a bare `except Exception`, same as
    `embed_paper_job`)."""
    # Imported locally, not at module top: search/__init__.py imports
    # `resolve_canonical_id` from this module, so a top-level import here
    # would cycle (the same pattern jobs/__init__.py already uses for its
    # own handler imports).
    from search.models import RawHit
    from search.sources import fetch_openalex_references, fetch_s2_references

    pid = uuid.UUID(paper_id)
    await _set_references_state(pid, "running")
    try:
        async with db.session() as session:
            paper = await session.get(PaperRow, pid)
            content = await session.get(PaperContent, pid)
            if paper is None or content is None:
                return

            hits: list[RawHit] = []
            source_api: str | None = None
            if paper.openalex_id:
                try:
                    hits = await fetch_openalex_references(paper.openalex_id, _TOP_REFERENCES)
                    if hits:
                        source_api = "openalex"
                except httpx.HTTPError:
                    hits = []  # degrade to the S2/PDF fallback, never fail the job (Rules.md)
            if not hits and paper.s2_id:
                try:
                    hits = await fetch_s2_references(paper.s2_id, _TOP_REFERENCES, api_key=get_config().s2_api_key)
                    if hits:
                        source_api = "s2"
                except httpx.HTTPError:
                    hits = []  # degrade to the PDF-section fallback below

            resolved: list[dict] = []
            edges: list[MetadataEdge] = []
            if hits:
                for hit in hits[:_TOP_REFERENCES]:
                    try:
                        stub = await add_reference_stub(session, hit.source_ids, hit.title)
                    except ValueError:
                        continue  # a hit with no usable source id at all — skip rather than fabricate one
                    resolved.append(
                        {"ref_id": f"r{len(resolved)}", "raw": hit.title, "title": hit.title, "paper_id": str(stub.id)}
                    )
                    edges.append(
                        MetadataEdge(
                            src_type="paper",
                            src_id=paper.canonical_id,
                            dst_type="paper",
                            dst_id=stub.canonical_id,
                            relation="cites",
                            source_api=source_api,
                        )
                    )
            else:
                for entry in content.references_[:_TOP_REFERENCES]:
                    raw = entry.get("raw", "")
                    stub_id: str | None = None
                    source_ids = _extract_ids_from_raw_reference(raw)
                    if source_ids is not None:
                        stub = None
                        try:
                            stub = await add_reference_stub(session, source_ids, raw[:200])
                        except ValueError:
                            pass
                        if stub is not None:
                            stub_id = str(stub.id)
                            edges.append(
                                MetadataEdge(
                                    src_type="paper",
                                    src_id=paper.canonical_id,
                                    dst_type="paper",
                                    dst_id=stub.canonical_id,
                                    relation="cites",
                                    source_api=None,
                                )
                            )
                    resolved.append(
                        {"ref_id": entry.get("ref_id") or f"r{len(resolved)}", "raw": raw, "title": None, "paper_id": stub_id}
                    )

            content.references_ = resolved
            if edges:
                await write_metadata_edges(session, pid, edges)
            paper.references_state = "done"
    except Exception:
        await _set_references_state(pid, "failed")
        raise
