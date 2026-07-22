from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_

from app.services.normalizer import (
    is_short_structural_fragment,
    normalize_match_text,
    normalize_text,
)


def apply_segment_status(segment: Any, next_status: str) -> None:
    """统一维护句段状态与最近确认时间。"""
    if next_status == "confirmed":
        if getattr(segment, "status", None) != "confirmed" or getattr(segment, "confirmed_at", None) is None:
            segment.confirmed_at = datetime.now()
    else:
        segment.confirmed_at = None
    segment.status = next_status


def _normalized_source_values(segment: Any) -> tuple[str, str, str]:
    source_text = normalize_match_text(getattr(segment, "source_text", "") or "")
    display_text = normalize_match_text(getattr(segment, "display_text", "") or "")
    matched_source_text = normalize_match_text(getattr(segment, "matched_source_text", "") or "")
    return source_text, display_text, matched_source_text


def has_exact_match_signal(segment: Any) -> bool:
    source_text, display_text, matched_source_text = _normalized_source_values(segment)
    if source_text and matched_source_text and matched_source_text == source_text:
        return True
    if (
        display_text
        and matched_source_text
        and matched_source_text == display_text
        and not is_short_structural_fragment(getattr(segment, "source_text", "") or "")
    ):
        return True
    return False


def is_llm_protected_reuse_segment(segment: Any) -> bool:
    if not normalize_text(getattr(segment, "target_text", "") or ""):
        return False
    status = str(getattr(segment, "status", "") or "")
    if status == "confirmed":
        return True
    return has_exact_match_signal(segment)


def resolve_unconfirmed_segment_status(segment: Any) -> str:
    if has_exact_match_signal(segment):
        return "exact"
    try:
        score = float(getattr(segment, "score", 0) or 0)
    except (TypeError, ValueError):
        score = 0.0
    matched_source_text = normalize_match_text(getattr(segment, "matched_source_text", "") or "")
    if score > 0 or matched_source_text:
        return "fuzzy"
    return "none"


def resolve_segment_match_status(segment: Any) -> str:
    """按译文来源和 TM 匹配信号分类，不受人工确认状态影响。"""
    if str(getattr(segment, "source", "") or "") == "project_sync":
        return "project_sync"
    return resolve_unconfirmed_segment_status(segment)


def sql_normalize_match_text(column: Any) -> Any:
    value = func.coalesce(column, "")
    value = func.regexp_replace(value, r"[[:space:]]+", " ", "g")
    value = func.regexp_replace(
        value,
        "[[:space:]]+([\u3002\uff01\uff1f!?.\uff0c,\u3001\uff1b;\uff1a:\uff09\\)\\]\\}])",
        r"\1",
        "g",
    )
    value = func.btrim(value)
    value = func.regexp_replace(value, "[\u3002\uff01\uff1f!?.]+$", "", "g")
    return func.btrim(value)


def sql_compact_match_core(column: Any) -> Any:
    return func.regexp_replace(sql_normalize_match_text(column), r"[^[:alnum:]]+", "", "g")


def sql_is_short_structural_fragment(column: Any) -> Any:
    core = sql_compact_match_core(column)
    return and_(
        core != "",
        func.length(core) <= 4,
        or_(
            core.op("~")(r"^[0-9]+[A-Za-z]?$"),
            core.op("~")(r"^[A-Za-z]$"),
            core.op("~*")(r"^[ivxlcdm]{1,4}$"),
        ),
    )


def segment_has_exact_match_signal_expr(segment_model: Any) -> Any:
    source_text = sql_normalize_match_text(segment_model.source_text)
    display_text = sql_normalize_match_text(segment_model.display_text)
    matched_source_text = sql_normalize_match_text(segment_model.matched_source_text)
    source_exact = and_(
        source_text != "",
        matched_source_text != "",
        matched_source_text == source_text,
    )
    display_exact = and_(
        display_text != "",
        matched_source_text != "",
        matched_source_text == display_text,
        ~sql_is_short_structural_fragment(segment_model.source_text),
    )
    return or_(source_exact, display_exact)


def segment_effective_status_conditions(segment_model: Any) -> dict[str, Any]:
    exact_signal = segment_has_exact_match_signal_expr(segment_model)
    project_sync = func.coalesce(segment_model.source, "") == "project_sync"
    fuzzy_signal = or_(
        segment_model.status == "fuzzy",
        func.coalesce(segment_model.score, 0) > 0,
        sql_normalize_match_text(segment_model.matched_source_text) != "",
    )
    exact = and_(~project_sync, exact_signal)
    fuzzy = and_(~project_sync, ~exact_signal, fuzzy_signal)
    none = and_(~project_sync, ~exact_signal, ~fuzzy_signal)
    confirmed = segment_model.status == "confirmed"
    return {
        "project_sync": project_sync,
        "exact": exact,
        "fuzzy": fuzzy,
        "none": none,
        "confirmed": confirmed,
    }
