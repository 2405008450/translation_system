from pydantic import BaseModel, Field


class FuzzyCandidate(BaseModel):
    """单个模糊匹配候选项"""
    source_text: str
    target_text: str
    score: float


class MatchResult(BaseModel):
    source_sentence: str
    status: str = Field(pattern="^(exact|fuzzy|none)$")
    score: float
    matched_source_text: str | None = None
    target_text: str | None = None
    sentence_id: str = ""
    # 所有超过阈值的模糊匹配候选（最多5条），存储为字典列表便于JSON序列化
    fuzzy_candidates: list[dict] = []
