"""The one LLM query-understanding pass (D21) — everything after this is
deterministic per-source parameter mapping, never a per-source LLM rewrite.
"""

from pydantic import BaseModel

from llm import Message, complete_structured
from search.models import SearchFilters

_SYSTEM_PROMPT = (
    "Extract search keywords and filters from a natural-language academic "
    "literature search query. Only set a filter when the user explicitly "
    "states or clearly implies it — never guess a year or venue."
)


class QueryUnderstanding(BaseModel):
    keywords: list[str]
    filters: SearchFilters


async def understand_query(query: str) -> QueryUnderstanding:
    return await complete_structured(
        messages=[Message(role="system", content=_SYSTEM_PROMPT), Message(role="user", content=query)],
        schema=QueryUnderstanding,
        tier="auxiliary",
        timeout=20,
    )
