"""Per-source literature API clients (D21) — deterministic parameter mapping
from the one query-understanding pass; no LLM call happens here.
"""

import xml.etree.ElementTree as ET

import httpx

from papers.models import SourceIds
from search.models import FirecrawlHit, RawHit, SearchFilters

_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}
_TIMEOUT = 15.0

# Verified against Firecrawl's current docs (https://docs.firecrawl.dev/api-reference/endpoint/search):
# POST https://api.firecrawl.dev/v1/search, Bearer auth, body {"query": ..., "limit": ...},
# response {"success": true, "data": [{"url": ..., "title": ..., "description": ..., ...}]} in
# relevance order. The response is still walked defensively below (`.get()`, `isinstance` checks,
# malformed entries skipped) since a field addition/rename on Firecrawl's side must degrade this
# client to the fallback path, never raise past its caller.
_FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v1/search"


async def search_firecrawl(query: str, api_key: str, limit: int) -> list[FirecrawlHit]:
    """Firecrawl `/search` is the relevance authority (D21 amendment): its
    result order drives ranking, arXiv/OpenAlex/S2 only enrich each hit
    afterwards. Raises `httpx.HTTPError` on a network/HTTP failure or
    `ValueError` on unparsable JSON — the caller degrades to the
    arXiv/OpenAlex/S2 fallback on either, the same shape as one literature
    source failing in `_fan_out`."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            _FIRECRAWL_SEARCH_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "limit": limit},
        )
        response.raise_for_status()
        payload = response.json()

    raw_hits = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw_hits, list):
        return []

    hits = []
    for item in raw_hits:
        if not isinstance(item, dict):
            continue
        url, title = item.get("url"), item.get("title")
        if not url or not title:
            continue
        description = item.get("description")
        hits.append(FirecrawlHit(url=url, title=title, description=description if isinstance(description, str) else None))
    return hits


async def search_arxiv(keywords: list[str], max_results: int = 30) -> list[RawHit]:
    query = " AND ".join(f"all:{kw}" for kw in keywords) if keywords else "all:*"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": query, "start": 0, "max_results": max_results},
        )
        response.raise_for_status()

    root = ET.fromstring(response.text)
    hits = []
    for entry in root.findall("atom:entry", _ARXIV_NS):
        arxiv_url = entry.findtext("atom:id", default="", namespaces=_ARXIV_NS)
        arxiv_id = arxiv_url.rsplit("/", 1)[-1]
        if not arxiv_id:
            continue
        pdf_url = next(
            (link.get("href") for link in entry.findall("atom:link", _ARXIV_NS) if link.get("title") == "pdf"),
            None,
        )
        published = entry.findtext("atom:published", default="", namespaces=_ARXIV_NS)
        year = int(published[:4]) if published[:4].isdigit() else None
        authors = [
            name
            for author in entry.findall("atom:author", _ARXIV_NS)
            if (name := author.findtext("atom:name", default="", namespaces=_ARXIV_NS))
        ]
        title = " ".join(entry.findtext("atom:title", default="", namespaces=_ARXIV_NS).split())
        summary = " ".join(entry.findtext("atom:summary", default="", namespaces=_ARXIV_NS).split())
        hits.append(
            RawHit(
                source_ids=SourceIds(arxiv_id=arxiv_id),
                title=title,
                abstract=summary or None,
                authors=authors,
                year=year,
                source_url=arxiv_url,
                pdf_url=pdf_url,
            )
        )
    return hits


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex returns abstracts as a word->positions index, not plain text."""
    if not inverted_index:
        return None
    positions: dict[int, str] = {}
    for word, indices in inverted_index.items():
        for index in indices:
            positions[index] = word
    return " ".join(positions[i] for i in sorted(positions))


async def search_openalex(keywords: list[str], filters: SearchFilters, max_results: int = 30) -> list[RawHit]:
    params: dict[str, str | int] = {"search": " ".join(keywords), "per_page": max_results}
    filter_parts = []
    if filters.year_min is not None:
        filter_parts.append(f"publication_year:>{filters.year_min - 1}")
    if filters.year_max is not None:
        filter_parts.append(f"publication_year:<{filters.year_max + 1}")
    if filter_parts:
        params["filter"] = ",".join(filter_parts)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get("https://api.openalex.org/works", params=params)
        response.raise_for_status()

    hits = []
    for work in response.json().get("results", []):
        ids = work.get("ids", {})
        primary_location = work.get("primary_location") or {}
        source = primary_location.get("source") or {}
        open_access = work.get("open_access") or {}
        authors = [
            name
            for authorship in work.get("authorships", [])
            if (name := (authorship.get("author") or {}).get("display_name"))
        ]
        hits.append(
            RawHit(
                source_ids=SourceIds(doi=ids.get("doi"), openalex_id=ids.get("openalex")),
                title=work.get("title") or work.get("display_name") or "",
                abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
                authors=authors,
                year=work.get("publication_year"),
                venue=source.get("display_name"),
                citation_count=work.get("cited_by_count"),
                source_url=primary_location.get("landing_page_url"),
                pdf_url=primary_location.get("pdf_url") or open_access.get("oa_url"),
            )
        )
    return hits


async def fetch_openalex_references(openalex_id: str, max_results: int = 5) -> list[RawHit]:
    """A paper's own `referenced_works` (D26/Phase 6.3), ranked by citation
    count. OpenAlex's `Work.referenced_works` is a list of OpenAlex work ids
    in the paper's own citation order, not citation-count order, so this
    batch-fetches their metadata (id filter, up to 50 per call, well under
    OpenAlex's own filter-list limit) and sorts the results itself."""
    bare_id = openalex_id.rsplit("/", 1)[-1]
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        work = await client.get(f"https://api.openalex.org/works/{bare_id}")
        work.raise_for_status()
        referenced_ids: list[str] = [w.rsplit("/", 1)[-1] for w in work.json().get("referenced_works", []) if w]
        if not referenced_ids:
            return []

        works: list[dict] = []
        for i in range(0, len(referenced_ids), 50):
            chunk = referenced_ids[i : i + 50]
            response = await client.get(
                "https://api.openalex.org/works",
                params={"filter": "openalex_id:" + "|".join(chunk), "per_page": len(chunk)},
            )
            response.raise_for_status()
            works.extend(response.json().get("results", []))

    hits = []
    for work_data in works:
        ids = work_data.get("ids", {})
        primary_location = work_data.get("primary_location") or {}
        open_access = work_data.get("open_access") or {}
        authors = [
            name
            for authorship in work_data.get("authorships", [])
            if (name := (authorship.get("author") or {}).get("display_name"))
        ]
        hits.append(
            RawHit(
                source_ids=SourceIds(doi=ids.get("doi"), openalex_id=ids.get("openalex")),
                title=work_data.get("title") or work_data.get("display_name") or "",
                authors=authors,
                year=work_data.get("publication_year"),
                citation_count=work_data.get("cited_by_count"),
                source_url=primary_location.get("landing_page_url"),
                pdf_url=primary_location.get("pdf_url") or open_access.get("oa_url"),
            )
        )
    hits.sort(key=lambda h: h.citation_count or 0, reverse=True)
    return hits[:max_results]


async def fetch_s2_references(s2_id: str, max_results: int = 5) -> list[RawHit]:
    """A paper's own `references` (D26/Phase 6.3) via S2's Graph API, which
    already returns each reference's own `citationCount` inline — no second
    batch call needed, unlike OpenAlex."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"https://api.semanticscholar.org/graph/v1/paper/{s2_id}",
            params={"fields": "references.title,references.year,references.externalIds,references.citationCount"},
        )
        response.raise_for_status()

    hits = []
    for ref in response.json().get("references") or []:
        if not ref or not ref.get("title"):
            continue
        external = ref.get("externalIds") or {}
        hits.append(
            RawHit(
                source_ids=SourceIds(doi=external.get("DOI"), arxiv_id=external.get("ArXiv"), s2_id=ref.get("paperId")),
                title=ref["title"],
                year=ref.get("year"),
                citation_count=ref.get("citationCount"),
            )
        )
    hits.sort(key=lambda h: h.citation_count or 0, reverse=True)
    return hits[:max_results]


async def search_s2(keywords: list[str], max_results: int = 30) -> list[RawHit]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": " ".join(keywords),
                "limit": max_results,
                "fields": "title,abstract,year,venue,authors,externalIds,openAccessPdf,citationCount",
            },
        )
        response.raise_for_status()

    hits = []
    for paper in response.json().get("data", []):
        external = paper.get("externalIds") or {}
        open_access_pdf = paper.get("openAccessPdf") or {}
        paper_id = paper.get("paperId")
        authors = [name for author in paper.get("authors", []) if (name := author.get("name"))]
        hits.append(
            RawHit(
                source_ids=SourceIds(doi=external.get("DOI"), arxiv_id=external.get("ArXiv"), s2_id=paper_id),
                title=paper.get("title") or "",
                abstract=paper.get("abstract"),
                authors=authors,
                year=paper.get("year"),
                venue=paper.get("venue"),
                citation_count=paper.get("citationCount"),
                source_url=f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else None,
                pdf_url=open_access_pdf.get("url"),
            )
        )
    return hits
