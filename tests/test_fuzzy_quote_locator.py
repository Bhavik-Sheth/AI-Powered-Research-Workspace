"""D33 fuzzy quote locator suite (Rules.md) — fixture-based, no network/DB/LLM.
Whitespace, hyphenation and ligature variants must locate correctly across
both the docling text stream and the PDF.js text layer — the same locator
provenance's D24 validator falls back to (built once, per D33).
"""

from provenance.locator import locate


class TestExactMatch:
    def test_locates_an_exact_substring(self):
        text = "Attention sinks are tokens that receive disproportionate attention."
        span = locate("Attention sinks", text)
        assert span is not None
        assert text[span.start : span.end] == "Attention sinks"


class TestWhitespaceVariants:
    def test_quote_with_single_spaces_locates_in_text_with_newlines(self):
        # e.g. copied from PDF.js, which often breaks lines differently than docling.
        text = "the model exhibits\nan attention   sink\nat the first token"
        span = locate("an attention sink", text)
        assert span is not None
        assert " ".join(text[span.start : span.end].split()) == "an attention sink"

    def test_quote_with_extra_internal_whitespace_still_locates(self):
        text = "streaming language models remain stable over long contexts"
        span = locate("streaming   language\tmodels", text)
        assert span is not None
        assert " ".join(text[span.start : span.end].split()) == "streaming language models"


class TestHyphenationVariants:
    def test_line_wrapped_hyphenation_in_text_matches_unhyphenated_quote(self):
        # docling/PDF extraction commonly preserves a line-wrap hyphen.
        text = "the model exhibits a strong atten-\ntion sink at the BOS token"
        span = locate("attention sink", text)
        assert span is not None
        assert text[span.start : span.end] == "atten-\ntion sink"

    def test_hyphen_with_surrounding_spaces_also_normalises(self):
        text = "we observe a secondary at- tention sink in middle layers"
        span = locate("attention sink", text)
        assert span is not None
        assert text[span.start : span.end] == "at- tention sink"


class TestLigatureVariants:
    def test_ligature_in_text_matches_expanded_quote(self):
        text = "this beﬁts the streaming setting well"  # "befits" with fi-ligature
        span = locate("befits the streaming setting", text)
        assert span is not None
        assert text[span.start : span.end] == "beﬁts the streaming setting"

    def test_ligature_in_quote_matches_expanded_text(self):
        text = "an efficient implementation avoids reflow entirely"
        span = locate("eﬃcient implementation", text)  # "efficient" typed with ffi-ligature
        assert span is not None
        assert text[span.start : span.end] == "efficient implementation"


class TestNoMatch:
    def test_returns_none_when_the_quote_is_not_present(self):
        text = "attention sinks persist across layers"
        assert locate("this text does not exist in the source", text) is None

    def test_returns_none_for_an_empty_quote(self):
        assert locate("", "some text") is None


class TestBothTextStreams:
    def test_locates_the_same_quote_in_a_docling_style_stream(self):
        docling_text = "4.2 Ablations\n\nRemoving the attention sink degrades perplexity substantially."
        span = locate("Removing the attention sink degrades perplexity", docling_text)
        assert span is not None
        assert docling_text[span.start : span.end] == "Removing the attention sink degrades perplexity"

    def test_locates_the_same_quote_in_a_pdfjs_style_stream(self):
        # PDF.js text layers frequently lack the paragraph breaks docling keeps.
        pdfjs_text = "4.2 Ablations Removing the attention sink degrades perplexity substantially."
        span = locate("Removing the attention sink degrades perplexity", pdfjs_text)
        assert span is not None
        assert pdfjs_text[span.start : span.end] == "Removing the attention sink degrades perplexity"
