"""Open-access-only PDF fetch (D23, invariant #3). arXiv, Unpaywall, and a
source's own OA link, or the user's upload — never a paywalled fetch; there
is no code path here that reaches a non-OA source.
"""

import httpx

from papers.models import SourceIds

_TIMEOUT = 20.0
# Required by Unpaywall's usage terms (a contact address for their outreach,
# not a secret or credential) — https://unpaywall.org/products/api.
_UNPAYWALL_CONTACT = "research-companion-os@localhost"


async def resolve_oa_pdf_url(source_ids: SourceIds, known_pdf_url: str | None) -> tuple[str, str] | None:
    """Returns `(pdf_url, origin)`, or `None` if no open-access copy exists."""
    if known_pdf_url:
        origin = "arxiv" if source_ids.arxiv_id else "s2_oa" if source_ids.s2_id else "unpaywall"
        return known_pdf_url, origin

    if source_ids.arxiv_id:
        return f"https://arxiv.org/pdf/{source_ids.arxiv_id}", "arxiv"

    if source_ids.doi:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                f"https://api.unpaywall.org/v2/{source_ids.doi}", params={"email": _UNPAYWALL_CONTACT}
            )
        if response.status_code == 200:
            best_location = response.json().get("best_oa_location") or {}
            url = best_location.get("url_for_pdf") or best_location.get("url")
            if url:
                return url, "unpaywall"

    return None


async def download_pdf(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
