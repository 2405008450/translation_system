from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.schemas import MatchResult
from app.services.analytics_service import count_source_words
from app.services.matcher import match_sentences_with_stats
from app.services.normalizer import build_source_hash, normalize_text


MATCH_ANALYSIS_THRESHOLD = 0.5
MATCH_ANALYSIS_ROW_ORDER = (
    "new",
    "tm_50_74",
    "tm_75_84",
    "tm_85_94",
    "tm_95_99",
    "tm_100",
    "tm_101",
    "tm_102",
    "internal_repeat",
    "cross_file_repeat",
)
MATCH_ANALYSIS_ROW_LABELS = {
    "new": "新字",
    "tm_50_74": "50%-74%",
    "tm_75_84": "75%-84%",
    "tm_85_94": "85%-94%",
    "tm_95_99": "95%-99%",
    "tm_100": "100%",
    "tm_101": "101%",
    "tm_102": "102%",
    "internal_repeat": "内部重复",
    "cross_file_repeat": "跨文件重复",
}


@dataclass(frozen=True)
class DocumentMatchSegment:
    file_id: UUID
    source_text: str
    display_text: str = ""
    source_word_count: int = 0
    collection_ids: tuple[UUID, ...] = ()


Matcher = Callable[
    [Session | None, list[str], list[str], list[UUID], float],
    Sequence[MatchResult],
]


def empty_document_match_analysis() -> dict[str, Any]:
    return _build_match_analysis(_empty_counts(), collection_ids=[])


def compute_document_match_analysis(
    db: Session | None,
    file_segments: Mapping[UUID, Iterable[DocumentMatchSegment]],
    *,
    matcher: Matcher | None = None,
    match_threshold: float = MATCH_ANALYSIS_THRESHOLD,
    applied_threshold: float | None = None,
    include_workload: bool = False,
) -> dict[UUID, dict[str, Any]]:
    """按互斥报价口径统计句段级重复和 TM 匹配区间。"""
    matcher = matcher or _match_segments_with_tm
    analysis_threshold = _normalize_threshold(match_threshold)
    workload_threshold = _normalize_threshold(
        applied_threshold if applied_threshold is not None else analysis_threshold
    )
    counts_by_file = {file_id: _empty_counts() for file_id in file_segments}
    workload_by_file = {file_id: _empty_workload_counts() for file_id in file_segments}
    collection_ids_by_file: dict[UUID, list[UUID]] = {
        file_id: [] for file_id in file_segments
    }
    pending_tm_segments: list[DocumentMatchSegment] = []

    seen_hashes_by_file: dict[UUID, set[str]] = defaultdict(set)
    seen_files_by_hash: dict[str, set[UUID]] = defaultdict(set)

    for file_id, raw_segments in file_segments.items():
        segments = list(raw_segments)
        for segment in segments:
            word_count = _segment_word_count(segment)
            source_hash = _segment_source_hash(segment)

            for collection_id in segment.collection_ids:
                if collection_id not in collection_ids_by_file[file_id]:
                    collection_ids_by_file[file_id].append(collection_id)

            if not source_hash:
                _add_to_counts(counts_by_file[file_id], "new", word_count)
                _add_to_workload(workload_by_file[file_id], "remaining_new", word_count)
                continue

            if source_hash in seen_hashes_by_file[file_id]:
                _add_to_counts(counts_by_file[file_id], "internal_repeat", word_count)
                _add_to_workload(workload_by_file[file_id], "repeat", word_count)
            elif any(seen_file_id != file_id for seen_file_id in seen_files_by_hash[source_hash]):
                _add_to_counts(counts_by_file[file_id], "cross_file_repeat", word_count)
                _add_to_workload(workload_by_file[file_id], "repeat", word_count)
            else:
                pending_tm_segments.append(segment)

            seen_hashes_by_file[file_id].add(source_hash)
            seen_files_by_hash[source_hash].add(file_id)

    for collection_ids, grouped_segments in _group_segments_for_tm(pending_tm_segments).items():
        if not collection_ids:
            for segment in grouped_segments:
                word_count = _segment_word_count(segment)
                _add_to_counts(counts_by_file[segment.file_id], "new", word_count)
                _add_to_workload(workload_by_file[segment.file_id], "remaining_new", word_count)
            continue

        source_sentences = [segment.source_text for segment in grouped_segments]
        auxiliary_sentences = [segment.display_text or segment.source_text for segment in grouped_segments]
        matches = matcher(db, source_sentences, auxiliary_sentences, list(collection_ids), analysis_threshold)
        for segment, match in zip(grouped_segments, matches, strict=False):
            bucket = _bucket_for_tm_match(match, match_threshold=analysis_threshold)
            word_count = _segment_word_count(segment)
            _add_to_counts(counts_by_file[segment.file_id], bucket, word_count)
            if _tm_match_applies_at_threshold(match, workload_threshold):
                _add_to_workload(workload_by_file[segment.file_id], "tm_applied", word_count)
            else:
                _add_to_workload(workload_by_file[segment.file_id], "remaining_new", word_count)

        if len(matches) < len(grouped_segments):
            for segment in grouped_segments[len(matches) :]:
                word_count = _segment_word_count(segment)
                _add_to_counts(counts_by_file[segment.file_id], "new", word_count)
                _add_to_workload(workload_by_file[segment.file_id], "remaining_new", word_count)

    result: dict[UUID, dict[str, Any]] = {}
    for file_id in file_segments:
        analysis = _build_match_analysis(
            counts_by_file[file_id],
            collection_ids=collection_ids_by_file.get(file_id, []),
            threshold=analysis_threshold,
        )
        if include_workload:
            analysis.update(
                _build_workload_summary(
                    workload_by_file[file_id],
                    applied_threshold=workload_threshold,
                )
            )
        result[file_id] = analysis
    return result


def merge_document_match_analyses(analyses: Iterable[Any]) -> dict[str, Any]:
    counts = _empty_counts()
    collection_ids: list[str] = []
    threshold = MATCH_ANALYSIS_THRESHOLD

    for raw_analysis in analyses:
        analysis = normalize_document_match_analysis(raw_analysis)
        if analysis is None:
            continue
        threshold = float(analysis.get("threshold") or threshold)
        for collection_id in analysis.get("collection_ids") or []:
            if collection_id not in collection_ids:
                collection_ids.append(collection_id)
        for row in analysis.get("rows") or []:
            key = row.get("key")
            if key not in counts:
                continue
            counts[key]["segment_count"] += _to_int(row.get("segment_count"))
            counts[key]["word_count"] += _to_int(row.get("word_count"))

    return _build_match_analysis(
        counts,
        collection_ids=collection_ids,
        threshold=threshold,
    )


def reconcile_document_match_analysis_words(
    analysis: Any,
    authoritative_word_count: int | None,
) -> dict[str, Any] | None:
    normalized = normalize_document_match_analysis(analysis)
    if normalized is None or authoritative_word_count is None:
        return normalized

    target_total = max(int(authoritative_word_count or 0), 0)
    current_total = int(normalized.get("total_words") or 0)
    if target_total == current_total:
        return normalized

    counts = _counts_from_analysis(normalized)
    if target_total <= 0:
        for row in counts.values():
            row["word_count"] = 0
    elif current_total <= 0:
        counts["new"]["word_count"] = target_total
    elif target_total > current_total:
        counts["new"]["word_count"] += target_total - current_total
    else:
        counts = _scale_counts_to_word_total(counts, target_total)

    return _build_match_analysis(
        counts,
        collection_ids=normalized.get("collection_ids") or [],
        threshold=float(normalized.get("threshold") or MATCH_ANALYSIS_THRESHOLD),
    )


def normalize_document_match_analysis(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    counts = _empty_counts()
    for row in value.get("rows") or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "")
        if key not in counts:
            continue
        counts[key]["segment_count"] = max(_to_int(row.get("segment_count")), 0)
        counts[key]["word_count"] = max(_to_int(row.get("word_count")), 0)

    collection_ids = [
        str(collection_id)
        for collection_id in value.get("collection_ids") or []
        if str(collection_id)
    ]
    threshold = value.get("threshold")
    try:
        normalized_threshold = float(threshold)
    except (TypeError, ValueError):
        normalized_threshold = MATCH_ANALYSIS_THRESHOLD

    return _build_match_analysis(
        counts,
        collection_ids=list(dict.fromkeys(collection_ids)),
        threshold=normalized_threshold,
    )


def _match_segments_with_tm(
    db: Session | None,
    source_sentences: list[str],
    auxiliary_sentences: list[str],
    collection_ids: list[UUID],
    match_threshold: float,
) -> Sequence[MatchResult]:
    if db is None:
        return []
    matches, _ = match_sentences_with_stats(
        db=db,
        sentences=source_sentences,
        auxiliary_sentences=auxiliary_sentences,
        similarity_threshold=match_threshold,
        collection_ids=collection_ids,
    )
    return matches


def _bucket_for_tm_match(
    match: MatchResult | Any,
    *,
    match_threshold: float = MATCH_ANALYSIS_THRESHOLD,
) -> str:
    status = str(getattr(match, "status", "") or "")
    try:
        score = float(getattr(match, "score", 0) or 0)
    except (TypeError, ValueError):
        score = 0.0

    if status == "exact" or score >= 0.999:
        return "tm_100"
    if status != "fuzzy" and score < match_threshold:
        return "new"
    if score >= 0.95:
        return "tm_95_99"
    if score >= 0.85:
        return "tm_85_94"
    if score >= 0.75:
        return "tm_75_84"
    if score >= match_threshold:
        return "tm_50_74"
    return "new"


def _tm_match_applies_at_threshold(match: MatchResult | Any, threshold: float) -> bool:
    status = str(getattr(match, "status", "") or "")
    try:
        score = float(getattr(match, "score", 0) or 0)
    except (TypeError, ValueError):
        score = 0.0

    if status == "exact" or score >= 0.999:
        return True
    return status == "fuzzy" and score >= threshold


def _group_segments_for_tm(
    segments: Iterable[DocumentMatchSegment],
) -> dict[tuple[UUID, ...], list[DocumentMatchSegment]]:
    grouped: dict[tuple[UUID, ...], list[DocumentMatchSegment]] = defaultdict(list)
    for segment in segments:
        grouped[tuple(dict.fromkeys(segment.collection_ids))].append(segment)
    return dict(grouped)


def _empty_counts() -> dict[str, dict[str, int]]:
    return {
        key: {"segment_count": 0, "word_count": 0}
        for key in MATCH_ANALYSIS_ROW_ORDER
    }


def _empty_workload_counts() -> dict[str, dict[str, int]]:
    return {
        "remaining_new": {"segment_count": 0, "word_count": 0},
        "tm_applied": {"segment_count": 0, "word_count": 0},
        "repeat": {"segment_count": 0, "word_count": 0},
    }


def _counts_from_analysis(analysis: dict[str, Any]) -> dict[str, dict[str, int]]:
    counts = _empty_counts()
    for row in analysis.get("rows") or []:
        key = row.get("key")
        if key not in counts:
            continue
        counts[key]["segment_count"] = max(_to_int(row.get("segment_count")), 0)
        counts[key]["word_count"] = max(_to_int(row.get("word_count")), 0)
    return counts


def _scale_counts_to_word_total(
    counts: dict[str, dict[str, int]],
    target_total: int,
) -> dict[str, dict[str, int]]:
    current_total = sum(max(counts[key]["word_count"], 0) for key in MATCH_ANALYSIS_ROW_ORDER)
    if current_total <= 0:
        return counts

    scaled: list[tuple[str, int, float]] = []
    floor_total = 0
    for key in MATCH_ANALYSIS_ROW_ORDER:
        raw_value = max(counts[key]["word_count"], 0) * target_total / current_total
        floor_value = int(raw_value)
        floor_total += floor_value
        scaled.append((key, floor_value, raw_value - floor_value))

    remaining = max(target_total - floor_total, 0)
    ranked_keys = [
        key
        for key, _, _ in sorted(
            scaled,
            key=lambda item: (-item[2], MATCH_ANALYSIS_ROW_ORDER.index(item[0])),
        )
    ]
    extra_by_key = {key: 0 for key in MATCH_ANALYSIS_ROW_ORDER}
    for key in ranked_keys[:remaining]:
        extra_by_key[key] += 1

    next_counts = _empty_counts()
    for key, floor_value, _ in scaled:
        next_counts[key]["segment_count"] = counts[key]["segment_count"]
        next_counts[key]["word_count"] = floor_value + extra_by_key[key]
    return next_counts


def _add_to_counts(counts: dict[str, dict[str, int]], key: str, word_count: int) -> None:
    bucket = counts.setdefault(key, {"segment_count": 0, "word_count": 0})
    bucket["segment_count"] += 1
    bucket["word_count"] += max(int(word_count or 0), 0)


def _add_to_workload(counts: dict[str, dict[str, int]], key: str, word_count: int) -> None:
    bucket = counts.setdefault(key, {"segment_count": 0, "word_count": 0})
    bucket["segment_count"] += 1
    bucket["word_count"] += max(int(word_count or 0), 0)


def _build_workload_summary(
    counts: dict[str, dict[str, int]],
    *,
    applied_threshold: float,
) -> dict[str, Any]:
    remaining_new = counts.get("remaining_new", {})
    tm_applied = counts.get("tm_applied", {})
    repeats = counts.get("repeat", {})
    return {
        "applied_threshold": float(applied_threshold),
        "remaining_new_segments": _to_int(remaining_new.get("segment_count")),
        "remaining_new_words": _to_int(remaining_new.get("word_count")),
        "tm_applied_segments": _to_int(tm_applied.get("segment_count")),
        "tm_applied_words": _to_int(tm_applied.get("word_count")),
        "repeat_segments": _to_int(repeats.get("segment_count")),
        "repeat_words": _to_int(repeats.get("word_count")),
    }


def _build_match_analysis(
    counts: dict[str, dict[str, int]],
    *,
    collection_ids: Iterable[UUID | str],
    threshold: float = MATCH_ANALYSIS_THRESHOLD,
) -> dict[str, Any]:
    total_segments = sum(counts[key]["segment_count"] for key in MATCH_ANALYSIS_ROW_ORDER)
    total_words = sum(counts[key]["word_count"] for key in MATCH_ANALYSIS_ROW_ORDER)
    rows = []
    for key in MATCH_ANALYSIS_ROW_ORDER:
        word_count = counts[key]["word_count"]
        rows.append(
            {
                "key": key,
                "label": MATCH_ANALYSIS_ROW_LABELS[key],
                "segment_count": counts[key]["segment_count"],
                "word_count": word_count,
                "percent": _calculate_percent(word_count, total_words),
            }
        )

    return {
        "threshold": float(threshold),
        "collection_ids": [str(collection_id) for collection_id in collection_ids],
        "total_segments": total_segments,
        "total_words": total_words,
        "rows": rows,
    }


def _calculate_percent(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((value / total) * 100, 2)


def _segment_word_count(segment: DocumentMatchSegment) -> int:
    if segment.source_word_count > 0:
        return int(segment.source_word_count)
    return count_source_words(segment.source_text)


def _segment_source_hash(segment: DocumentMatchSegment) -> str:
    if not normalize_text(segment.source_text):
        return ""
    return build_source_hash(segment.source_text)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_threshold(value: float | int | str | None) -> float:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = MATCH_ANALYSIS_THRESHOLD
    if numeric_value < MATCH_ANALYSIS_THRESHOLD:
        return MATCH_ANALYSIS_THRESHOLD
    if numeric_value > 1:
        return 1.0
    return round(numeric_value, 2)
