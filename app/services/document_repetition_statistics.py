from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from app.services.analytics_service import SOURCE_WORD_PATTERN
from app.services.normalizer import normalize_text


REPETITION_STATISTIC_KEYS = (
    "internal_repeated_words",
    "internal_repeated_characters",
    "cross_file_repeated_words",
    "cross_file_repeated_characters",
)


@dataclass(frozen=True)
class _Occurrence:
    file_id: UUID
    start: int
    end: int
    order: int


def empty_repetition_statistics() -> dict[str, int]:
    return {key: 0 for key in REPETITION_STATISTIC_KEYS}


def compute_document_repetition_statistics(
    file_segments: Mapping[UUID, Iterable[str]],
) -> dict[UUID, dict[str, int]]:
    """按文件统计内部重复和跨文件重复。

    统计基于已解析源文句段。词级最小窗口为 1，字符级最小窗口为 2；
    重复覆盖区间会合并，避免重叠片段重复累加。
    """
    stats = {file_id: empty_repetition_statistics() for file_id in file_segments}
    if not file_segments:
        return stats

    word_occurrences: dict[str, list[_Occurrence]] = defaultdict(list)
    character_occurrences: dict[str, list[_Occurrence]] = defaultdict(list)
    order = 0

    for file_id, segments in file_segments.items():
        word_offset = 0
        character_offset = 0
        for text in segments:
            word_tokens = _extract_word_tokens(text)
            for index, token in enumerate(word_tokens):
                word_occurrences[token].append(
                    _Occurrence(
                        file_id=file_id,
                        start=word_offset + index,
                        end=word_offset + index + 1,
                        order=order,
                    )
                )
                order += 1
            word_offset += len(word_tokens)

            characters = _extract_visible_characters(text)
            for index in range(0, max(len(characters) - 1, 0)):
                character_occurrences["".join(characters[index : index + 2])].append(
                    _Occurrence(
                        file_id=file_id,
                        start=character_offset + index,
                        end=character_offset + index + 2,
                        order=order,
                    )
                )
                order += 1
            character_offset += len(characters)

    internal_word_ranges, cross_word_ranges = _build_repeated_ranges(word_occurrences)
    internal_character_ranges, cross_character_ranges = _build_repeated_ranges(character_occurrences)

    for file_id in stats:
        stats[file_id]["internal_repeated_words"] = _count_covered_units(
            internal_word_ranges.get(file_id, [])
        )
        stats[file_id]["cross_file_repeated_words"] = _count_covered_units(
            cross_word_ranges.get(file_id, [])
        )
        stats[file_id]["internal_repeated_characters"] = _count_covered_units(
            internal_character_ranges.get(file_id, [])
        )
        stats[file_id]["cross_file_repeated_characters"] = _count_covered_units(
            cross_character_ranges.get(file_id, [])
        )

    return stats


def _extract_word_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    return [match.group(0).lower() for match in SOURCE_WORD_PATTERN.finditer(text)]


def _extract_visible_characters(text: str | None) -> list[str]:
    if not text:
        return []
    normalized = normalize_text(text)
    return [char for char in normalized if not char.isspace()]


def _build_repeated_ranges(
    occurrences_by_key: Mapping[str, list[_Occurrence]],
) -> tuple[dict[UUID, list[tuple[int, int]]], dict[UUID, list[tuple[int, int]]]]:
    internal_ranges: dict[UUID, list[tuple[int, int]]] = defaultdict(list)
    cross_file_ranges: dict[UUID, list[tuple[int, int]]] = defaultdict(list)

    for occurrences in occurrences_by_key.values():
        if len(occurrences) < 2:
            continue
        ordered = sorted(occurrences, key=lambda item: item.order)

        seen_in_file: set[UUID] = set()
        for occurrence in ordered:
            if occurrence.file_id in seen_in_file:
                internal_ranges[occurrence.file_id].append((occurrence.start, occurrence.end))
            seen_in_file.add(occurrence.file_id)

        seen_files: set[UUID] = set()
        for occurrence in ordered:
            if any(file_id != occurrence.file_id for file_id in seen_files):
                cross_file_ranges[occurrence.file_id].append((occurrence.start, occurrence.end))
            seen_files.add(occurrence.file_id)

    return internal_ranges, cross_file_ranges


def _count_covered_units(ranges: Iterable[tuple[int, int]]) -> int:
    ordered_ranges = sorted((start, end) for start, end in ranges if end > start)
    if not ordered_ranges:
        return 0

    total = 0
    current_start, current_end = ordered_ranges[0]
    for start, end in ordered_ranges[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        total += current_end - current_start
        current_start, current_end = start, end
    total += current_end - current_start
    return total
