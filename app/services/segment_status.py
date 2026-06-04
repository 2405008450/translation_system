from __future__ import annotations

from typing import Any

from app.services.normalizer import normalize_match_text, normalize_text


def resolve_unconfirmed_segment_status(segment: Any) -> str:
    if not normalize_text(getattr(segment, "target_text", "") or ""):
        return "none"

    score = float(getattr(segment, "score", 0) or 0)
    source_text = normalize_match_text(getattr(segment, "source_text", "") or "")
    matched_source_text = normalize_match_text(getattr(segment, "matched_source_text", "") or "")
    if score >= 0.999 or (matched_source_text and matched_source_text == source_text):
        return "exact"
    if score > 0 or matched_source_text:
        return "fuzzy"
    return "none"
