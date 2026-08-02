"""Wire/value shapes for Voice Engine (Rules.md: names match the wire shape)."""

from pydantic import BaseModel


class Transcript(BaseModel):
    text: str
