"""docling-backed parsing (D23) — turns a PDF into `full_text` plus section
boundaries and references, the offset space every quote anchor resolves
against (D24).
"""

from pathlib import Path

import re

import docling
from docling.document_converter import DocumentConverter
from docling_core.types.doc import DocItemLabel
from pydantic import BaseModel

_HEADING_LABELS = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
_SEPARATOR = "\n"

_REFERENCES_HEADING = re.compile(r"^(references|bibliography)\s*$", re.IGNORECASE)
# A bracketed/bare leading index (`[12] `, `12. `) on a numbered-style
# entry — stripped for display once the entry is already split out, not
# used to find the split itself (see `_split_references_section`).
_LEADING_MARKER = re.compile(r"^\s*(?:\[\d+\]|\d{1,3}\.)\s+")


def _split_references_section(full_text: str, sections: list[dict]) -> list[dict]:
    """Fallback reference extraction (Phase 6.3) for papers where docling
    never emitted a `DocItemLabel.REFERENCE` item — the common case. Locates
    the References/Bibliography section via the already-computed section
    boundaries and splits it one entry per line: `parse_pdf` already joins
    docling's own per-item text extraction on newlines (`_SEPARATOR`), and
    docling emits one bibliography entry per item regardless of citation
    style — numbered (`[12] …`), unnumbered author-year (ACL/EMNLP/AAAI
    style, no leading marker at all), or anything else. Splitting on a
    leading `[N]`/`N.` marker instead would silently return the *entire*
    section as one unsplit entry for every unnumbered style, which is the
    common case in NLP papers. Deterministic, no LLM."""
    section = next((s for s in sections if _REFERENCES_HEADING.match(s["heading"].strip())), None)
    if section is None:
        return []

    body = full_text[section["char_start"] : section["char_end"]]
    lines = [line.strip() for line in body.split(_SEPARATOR)]
    entries = [_LEADING_MARKER.sub("", line) for line in lines if line and not _REFERENCES_HEADING.match(line)]
    return [{"ref_id": f"r{i}", "raw": entry} for i, entry in enumerate(entries)]


class ParsedPaper(BaseModel):
    full_text: str
    sections: list[dict]
    references: list[dict]
    parser_version: str


def parse_pdf(pdf_path: Path) -> ParsedPaper:
    document = DocumentConverter().convert(pdf_path).document

    text_parts: list[str] = []
    sections: list[dict] = []
    references: list[dict] = []
    offset = 0
    current_section: dict | None = None

    for item, _level in document.iterate_items():
        text = getattr(item, "text", None)
        if not text:
            continue
        label = getattr(item, "label", None)

        if label in _HEADING_LABELS:
            if current_section is not None:
                current_section["char_end"] = offset
            current_section = {
                "section_id": f"s{len(sections)}",
                "heading": text,
                "level": getattr(item, "level", 0),
                "char_start": offset,
                "char_end": offset,
            }
            sections.append(current_section)
        elif label == DocItemLabel.REFERENCE:
            references.append({"ref_id": f"r{len(references)}", "raw": text})

        text_parts.append(text)
        offset += len(text) + len(_SEPARATOR)

    full_text = _SEPARATOR.join(text_parts)
    if current_section is not None:
        current_section["char_end"] = len(full_text)
    for section in sections:
        section["char_end"] = min(section["char_end"], len(full_text))

    if not references:
        # docling rarely emits DocItemLabel.REFERENCE (Fix Round 1 root
        # cause) — fall back to splitting the References/Bibliography
        # section's own text.
        references = _split_references_section(full_text, sections)

    return ParsedPaper(
        full_text=full_text,
        sections=sections,
        references=references,
        parser_version=docling.__version__,
    )
