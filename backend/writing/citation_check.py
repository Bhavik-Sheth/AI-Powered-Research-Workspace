"""Missing/unsupported citation detection (MODULES.md Manuscript, D34) — the
rule Vault Writer must not know. Two independent checks over one document:

- **Missing**: a `\\cite{key}` whose key doesn't name a paper in the
  project's library — a deterministic regex + set-membership check, no LLM.
- **Unsupported**: a claim-shaped sentence with no citation nearby. Finding
  candidate sentences needs a reader, so this one LLM pass identifies them —
  but it never authors anything (D34's "no prose" line): every returned
  span is checked back against the document's own text with a plain
  substring match, and a span that doesn't literally occur is dropped
  rather than rendered, the same discipline D24's provenance validator
  applies to extractive-card fields.
"""

import re

from pydantic import BaseModel

from llm import LLMError, Message, complete_structured
from writing.models import CitationFinding

_CITE_KEY = re.compile(r"\\cite\{([^}]*)\}")
_LATEX_COMMAND = re.compile(r"\\[a-zA-Z]+(\{[^}]*\})?")
_MAX_UNSUPPORTED_CHARS = 20_000

_UNSUPPORTED_CLAIM_PROMPT = (
    "This is prose from a researcher's LaTeX manuscript, LaTeX commands stripped. "
    "List up to 5 sentences that state a specific factual claim (a number, a named result, a "
    "comparison to prior work) with no citation attached. Quote each sentence VERBATIM, copied "
    "exactly character-for-character from the text below — never paraphrase, never invent a "
    "sentence that isn't there. If every claim is already cited, return an empty list."
)


class _UnsupportedClaims(BaseModel):
    sentences: list[str] = []


def _strip_latex(tex: str) -> str:
    return _LATEX_COMMAND.sub(" ", tex)


def _find_missing_citations(tex: str, known_cite_keys: set[str]) -> list[CitationFinding]:
    findings = []
    for match in _CITE_KEY.finditer(tex):
        for key in (k.strip() for k in match.group(1).split(",")):
            if key and key not in known_cite_keys:
                findings.append(CitationFinding(kind="missing", span=match.group(0), note=f"no reference named '{key}' in this project's library"))
    return findings


async def _find_unsupported_claims(tex: str) -> list[CitationFinding]:
    plain = _strip_latex(tex)[:_MAX_UNSUPPORTED_CHARS]
    if not plain.strip():
        return []
    try:
        result = await complete_structured(
            messages=[Message(role="system", content=_UNSUPPORTED_CLAIM_PROMPT), Message(role="user", content=plain)],
            schema=_UnsupportedClaims,
            tier="auxiliary",
            timeout=60,
        )
    except (*LLMError, RuntimeError):
        return []
    return [CitationFinding(kind="unsupported", span=sentence) for sentence in result.sentences if sentence and sentence in tex]


async def check_citations(tex: str, known_cite_keys: set[str]) -> list[CitationFinding]:
    missing = _find_missing_citations(tex, known_cite_keys)
    unsupported = await _find_unsupported_claims(tex)
    return missing + unsupported
