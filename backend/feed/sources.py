"""Per-source category-driven fetch (D28) — broad recall by category,
windowed to "since last poll", recency-sorted; never keyword-driven. A
separate client from `search/sources.py`'s keyword fan-out: Research Feed
does not depend on Search Federation (MODULES.md), and the query shape here
(one category, a date window, recency order) is a different concern from a
one-off keyword search.

arXiv: `cat:` field plus a `submittedDate:[from TO to]` range query,
`sortBy=submittedDate&sortOrder=descending` (arXiv API user manual, "Details
of Query Construction" and "sort order for return results").

OpenAlex: `search` (broad recall text match on the category name) plus
`filter=from_publication_date:...`, `sort=publication_date:desc`
(developers.openalex.org, Works "Filter results" / "Sort results").

Semantic Scholar: the same `/graph/v1/paper/search` endpoint as Search
Federation, `query`-matched on the category name for broad recall. Not
`fieldsOfStudy`: that parameter's accepted values are broad names like
"Computer Science", not arXiv-style subcodes like "cs.CL", and asserting an
unverified mapping between the two would be worse than the plain text match.
The Graph API's documented sort/date-range controls are on
`/paper/search/bulk`, not this endpoint, so results are windowed client-side
against `since` instead of trusting an unverified server-side date filter.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from feed.models import RawFeedHit
from papers.models import SourceIds

_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}
_TIMEOUT = 15.0
_ARXIV_DATE_FMT = "%Y%m%d%H%M"


def _arxiv_date(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_ARXIV_DATE_FMT)


async def fetch_arxiv_by_category(category: str, since: datetime, max_results: int = 50) -> list[RawFeedHit]:
    query = f"cat:{category} AND submittedDate:[{_arxiv_date(since)} TO {_arxiv_date(datetime.now(timezone.utc))}]"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": query, "sortBy": "submittedDate", "sortOrder": "descending", "start": 0, "max_results": max_results},
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
            (link.get("href") for link in entry.findall("atom:link", _ARXIV_NS) if link.get("title") == "pdf"), None
        )
        published = entry.findtext("atom:published", default="", namespaces=_ARXIV_NS)
        published_at = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if published else None
        title = " ".join(entry.findtext("atom:title", default="", namespaces=_ARXIV_NS).split())
        summary = " ".join(entry.findtext("atom:summary", default="", namespaces=_ARXIV_NS).split())
        hits.append(
            RawFeedHit(
                source_ids=SourceIds(arxiv_id=arxiv_id),
                category=category,
                title=title,
                abstract=summary or None,
                source_url=arxiv_url,
                pdf_url=pdf_url,
                published_at=published_at,
            )
        )
    return hits


async def fetch_openalex_by_category(category: str, since: datetime, max_results: int = 50) -> list[RawFeedHit]:
    params = {
        "search": category,
        "filter": f"from_publication_date:{since.date().isoformat()}",
        "sort": "publication_date:desc",
        "per_page": max_results,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get("https://api.openalex.org/works", params=params)
        response.raise_for_status()

    hits = []
    for work in response.json().get("results", []):
        ids = work.get("ids", {})
        primary_location = work.get("primary_location") or {}
        source = primary_location.get("source") or {}
        open_access = work.get("open_access") or {}
        publication_date = work.get("publication_date")
        published_at = datetime.strptime(publication_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) if publication_date else None
        hits.append(
            RawFeedHit(
                source_ids=SourceIds(doi=ids.get("doi"), openalex_id=ids.get("openalex")),
                category=category,
                title=work.get("title") or work.get("display_name") or "",
                abstract=None,
                venue=source.get("display_name"),
                source_url=primary_location.get("landing_page_url"),
                pdf_url=primary_location.get("pdf_url") or open_access.get("oa_url"),
                published_at=published_at,
            )
        )
    return hits


async def fetch_s2_by_category(category: str, since: datetime, max_results: int = 50) -> list[RawFeedHit]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": category,
                "limit": max_results,
                "fields": "title,abstract,venue,publicationDate,externalIds,openAccessPdf",
            },
        )
        response.raise_for_status()

    hits = []
    for paper in response.json().get("data", []):
        publication_date = paper.get("publicationDate")
        published_at = datetime.strptime(publication_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) if publication_date else None
        if published_at is not None and published_at < since:
            continue
        external = paper.get("externalIds") or {}
        open_access_pdf = paper.get("openAccessPdf") or {}
        paper_id = paper.get("paperId")
        hits.append(
            RawFeedHit(
                source_ids=SourceIds(doi=external.get("DOI"), arxiv_id=external.get("ArXiv"), s2_id=paper_id),
                category=category,
                title=paper.get("title") or "",
                abstract=paper.get("abstract"),
                venue=paper.get("venue"),
                source_url=f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else None,
                pdf_url=open_access_pdf.get("url"),
                published_at=published_at,
            )
        )
    return hits
