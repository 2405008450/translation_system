from __future__ import annotations

from typing import Any

from app.services.normalizer import normalize_match_text, normalize_text


EXACT_MATCH_SCORE_THRESHOLD = 0.999


def has_exact_match_signal(segment: Any) -> bool:
    score = float(getattr(segment, "score", 0) or 0)
    source_text = normalize_match_text(getattr(segment, "source_text", "") or "")
    matched_source_text = normalize_match_text(getattr(segment, "matched_source_text", "") or "")
    return score >= EXACT_MATCH_SCORE_THRESHOLD or (
        bool(matched_source_text) and matched_source_text == source_text
    )


def is_llm_protected_reuse_segment(segment: Any) -> bool:
    if not normalize_text(getattr(segment, "target_text", "") or ""):
        return False
    status = str(getattr(segment, "status", "") or "")
    if status in {"exact", "confirmed"}:
        return True
    return has_exact_match_signal(segment)


def resolve_unconfirmed_segment_status(segment: Any) -> str:
    if not normalize_text(getattr(segment, "target_text", "") or ""):
        return "none"

    if has_exact_match_signal(segment):
        return "exact"
    score = float(getattr(segment, "score", 0) or 0)
    matched_source_text = normalize_match_text(getattr(segment, "matched_source_text", "") or "")
    if score > 0 or matched_source_text:
        return "fuzzy"
    return "none"
