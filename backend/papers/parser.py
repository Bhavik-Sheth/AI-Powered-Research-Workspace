"""docling-backed parsing (D23) — turns a PDF into `full_text` plus section
boundaries and references, the offset space every quote anchor resolves
against (D24).
"""

from pathlib import Path

import docling
from docling.document_converter import DocumentConverter
from docling_core.types.doc import DocItemLabel
from pydantic import BaseModel

_HEADING_LABELS = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
_SEPARATOR = "\n"


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

    return ParsedPaper(
        full_text=full_text,
        sections=sections,
        references=references,
        parser_version=docling.__version__,
    )
