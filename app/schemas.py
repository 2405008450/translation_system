from pydantic import BaseModel, Field


class MatchResult(BaseModel):
    source_sentence: str
    status: str = Field(pattern="^(exact|fuzzy|none)$")
    score: float
    matched_source_text: str | None = None
    target_text: str | None = None
    sentence_id: str = ""
