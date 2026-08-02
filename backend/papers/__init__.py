"""Paper Pipeline — turns a paper reference into a fetched, parsed, extracted,
validated global paper record (MODULES.md).

Phase 1.3 ships `resolve_canonical_id` only, the one function Search
Federation needs for dedup. `add_paper` / `parse_paper_job` / `extract_card_job`
/ `enrich_paper_job` land in Phase 1.4 with the Reader.
"""

import re

from papers.models import SourceIds

_DOI_PREFIX = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_ARXIV_PREFIX = re.compile(r"^\s*arxiv:\s*", re.IGNORECASE)
_ARXIV_VERSION_SUFFIX = re.compile(r"v\d+$", re.IGNORECASE)
_OPENALEX_PREFIX = re.compile(r"^\s*https?://openalex\.org/", re.IGNORECASE)
_S2_CORPUS_PREFIX = re.compile(r"^\s*corpusid:\s*", re.IGNORECASE)


def _normalise_doi(doi: str) -> str:
    return _DOI_PREFIX.sub("", doi).strip().lower()


def _normalise_arxiv_id(arxiv_id: str) -> str:
    stripped = _ARXIV_PREFIX.sub("", arxiv_id).strip()
    return _ARXIV_VERSION_SUFFIX.sub("", stripped)


def _normalise_openalex_id(openalex_id: str) -> str:
    return _OPENALEX_PREFIX.sub("", openalex_id).strip()


def _normalise_s2_id(s2_id: str) -> str:
    return _S2_CORPUS_PREFIX.sub("", s2_id).strip()


def resolve_canonical_id(source_ids: SourceIds) -> str:
    """The one function that derives `canonical_id` — never re-derived inline
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
