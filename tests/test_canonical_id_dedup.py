"""D25 canonical-id dedup suite (Rules.md) — fixture-based, no network/DB/LLM.

Priority is DOI -> arXiv -> OpenAlex/S2 (D25); every source's id variants
that name the same real-world paper must normalise to the identical
canonical id, which is the dedup key search federation relies on.
"""

import pytest
from papers import resolve_canonical_id
from papers.models import SourceIds


class TestDoiPriority:
    def test_doi_alone(self):
        assert resolve_canonical_id(SourceIds(doi="10.1145/3442188")) == "doi:10.1145/3442188"

    def test_doi_wins_over_every_other_id(self):
        ids = SourceIds(doi="10.1145/3442188", arxiv_id="2310.11511", openalex_id="W2741809807", s2_id="649def34")
        assert resolve_canonical_id(ids) == "doi:10.1145/3442188"

    def test_doi_url_prefix_is_stripped(self):
        assert resolve_canonical_id(SourceIds(doi="https://doi.org/10.1145/3442188")) == "doi:10.1145/3442188"

    def test_doi_scheme_prefix_is_stripped(self):
        assert resolve_canonical_id(SourceIds(doi="doi:10.1145/3442188")) == "doi:10.1145/3442188"

    def test_doi_is_lowercased(self):
        assert resolve_canonical_id(SourceIds(doi="10.1145/ABC123")) == "doi:10.1145/abc123"

    def test_doi_format_and_case_variants_collide(self):
        a = resolve_canonical_id(SourceIds(doi="https://doi.org/10.1145/ABC123"))
        b = resolve_canonical_id(SourceIds(doi="DOI:10.1145/abc123"))
        c = resolve_canonical_id(SourceIds(doi="10.1145/Abc123"))
        assert a == b == c


class TestArxivPriority:
    def test_arxiv_used_when_no_doi(self):
        ids = SourceIds(arxiv_id="2310.11511", openalex_id="W2741809807", s2_id="649def34")
        assert resolve_canonical_id(ids) == "arxiv:2310.11511"

    def test_arxiv_scheme_prefix_is_stripped(self):
        assert resolve_canonical_id(SourceIds(arxiv_id="arXiv:2310.11511")) == "arxiv:2310.11511"

    def test_arxiv_version_suffix_is_stripped(self):
        assert resolve_canonical_id(SourceIds(arxiv_id="2310.11511v2")) == "arxiv:2310.11511"

    def test_arxiv_versions_of_the_same_paper_collide(self):
        a = resolve_canonical_id(SourceIds(arxiv_id="2310.11511v1"))
        b = resolve_canonical_id(SourceIds(arxiv_id="2310.11511v3"))
        c = resolve_canonical_id(SourceIds(arxiv_id="arXiv:2310.11511"))
        assert a == b == c


class TestOpenAlexPriority:
    def test_openalex_used_when_no_doi_or_arxiv(self):
        ids = SourceIds(openalex_id="W2741809807", s2_id="649def34")
        assert resolve_canonical_id(ids) == "openalex:W2741809807"

    def test_openalex_url_form_is_normalised(self):
        assert resolve_canonical_id(SourceIds(openalex_id="https://openalex.org/W2741809807")) == "openalex:W2741809807"

    def test_openalex_url_and_bare_forms_collide(self):
        a = resolve_canonical_id(SourceIds(openalex_id="https://openalex.org/W2741809807"))
        b = resolve_canonical_id(SourceIds(openalex_id="W2741809807"))
        assert a == b


class TestS2Fallback:
    def test_s2_used_when_nothing_else_present(self):
        ids = SourceIds(s2_id="649def34f8be52c8b66281af98ae884c09aef38")
        assert resolve_canonical_id(ids) == "s2:649def34f8be52c8b66281af98ae884c09aef38"

    def test_s2_corpus_id_prefix_is_stripped(self):
        assert resolve_canonical_id(SourceIds(s2_id="CorpusID:236149338")) == "s2:236149338"


class TestNoSourceIds:
    def test_raises_when_nothing_is_provided(self):
        with pytest.raises(ValueError):
            resolve_canonical_id(SourceIds())
