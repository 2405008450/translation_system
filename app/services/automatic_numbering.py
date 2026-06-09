from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import re
from typing import Any

from app.services.normalizer import normalize_text


WORD_DOCUMENT_EXTENSIONS = {".doc", ".docx"}
CHINESE_NUMBER_CHARS = "零〇一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰仟"
CHINESE_DIGIT_VALUES = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "壹": 1,
    "贰": 2,
    "叁": 3,
    "肆": 4,
    "伍": 5,
    "陆": 6,
    "柒": 7,
    "捌": 8,
    "玖": 9,
}
CHINESE_UNIT_VALUES = {
    "十": 10,
    "拾": 10,
    "百": 100,
    "佰": 100,
    "千": 1000,
    "仟": 1000,
    "万": 10000,
}
CHINESE_NUMBER_PATTERN = f"0-9{CHINESE_NUMBER_CHARS}"
LOCALIZED_NUMBERING_LABELS = {
    "章": "Chapter",
    "节": "Section",
    "条": "Article",
    "款": "Clause",
}
OPTIONAL_PREFIX_SEPARATORS = {":", "：", "-", "–", "—"}
TIGHT_BOUNDARY_SUFFIXES = ("、", "。", "．", ".", ")", "）", "]", "】")


def is_word_document_filename(filename: str | None) -> bool:
    if not filename:
        return False
    return Path(filename).suffix.lower() in WORD_DOCUMENT_EXTENSIONS


def get_automatic_numbering_text(
    *,
    source_text: str,
    display_text: str | None = None,
    numbering_text: str | None = None,
) -> str:
    prefixes = _source_prefixes(
        source_text=source_text,
        display_text=display_text,
        numbering_text=numbering_text,
        reference_texts=None,
    )
    return prefixes[0] if prefixes else ""


def strip_segment_automatic_numbering_prefix(
    segment: Any,
    text: str | None,
    *,
    reference_texts: Iterable[str | None] | None = None,
) -> str:
    return strip_automatic_numbering_prefix(
        text,
        source_text=str(_get_value(segment, "source_text", "") or ""),
        display_text=str(_get_value(segment, "display_text", "") or ""),
        numbering_text=str(_get_value(segment, "numbering_text", "") or ""),
        reference_texts=reference_texts,
    )


def strip_automatic_numbering_prefix(
    text: str | None,
    *,
    source_text: str,
    display_text: str | None = None,
    numbering_text: str | None = None,
    reference_texts: Iterable[str | None] | None = None,
) -> str:
    if text is None:
        return ""
    if not text:
        return text

    candidates = _build_prefix_candidates(
        source_text=source_text,
        display_text=display_text,
        numbering_text=numbering_text,
        reference_texts=reference_texts,
    )
    if not candidates:
        return text

    for prefix in candidates:
        stripped = _strip_prefix_once(text, prefix)
        if stripped != text and normalize_text(stripped):
            return stripped
    return text


def has_automatic_numbering_context(
    *,
    source_text: str,
    display_text: str | None = None,
    numbering_text: str | None = None,
) -> bool:
    return bool(
        _source_prefixes(
            source_text=source_text,
            display_text=display_text,
            numbering_text=numbering_text,
            reference_texts=None,
        )
    )


def _build_prefix_candidates(
    *,
    source_text: str,
    display_text: str | None,
    numbering_text: str | None,
    reference_texts: Iterable[str | None] | None,
) -> list[str]:
    source_prefixes = _source_prefixes(
        source_text=source_text,
        display_text=display_text,
        numbering_text=numbering_text,
        reference_texts=reference_texts,
    )
    candidates: list[str] = []
    for prefix in source_prefixes:
        candidates.append(prefix)
        candidates.extend(_localize_prefix_candidates(prefix))
    return _dedupe_prefixes(candidates)


def _source_prefixes(
    *,
    source_text: str,
    display_text: str | None,
    numbering_text: str | None,
    reference_texts: Iterable[str | None] | None,
) -> list[str]:
    prefixes: list[str] = []
    if numbering_text:
        prefixes.append(numbering_text)
    display_prefix = _derive_prefix_from_text(display_text or "", source_text)
    if display_prefix:
        prefixes.append(display_prefix)
    for reference_text in reference_texts or []:
        reference_prefix = _derive_prefix_from_text(reference_text or "", source_text)
        if reference_prefix:
            prefixes.append(reference_prefix)
    return _dedupe_prefixes(prefixes)


def _derive_prefix_from_text(text: str, source_text: str) -> str:
    normalized_text = normalize_text(text)
    normalized_source = normalize_text(source_text)
    if not normalized_text or not normalized_source:
        return ""
    if normalized_text == normalized_source:
        return ""
    if not normalized_text.endswith(normalized_source):
        return ""

    prefix = normalized_text[: -len(normalized_source)].rstrip()
    if not _looks_like_numbering_prefix(prefix):
        return ""
    return prefix


def _looks_like_numbering_prefix(prefix: str) -> bool:
    normalized = normalize_text(prefix)
    if not normalized or len(normalized) > 40:
        return False
    if re.fullmatch(r"[\(（]\s*\d+(?:\.\d+)*\s*[\)）]", normalized):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)*[.)、]?", normalized):
        return True
    if re.fullmatch(r"[A-Za-z][.)、]?", normalized):
        return True
    if re.fullmatch(r"(?i)[ivxlcdm]+[.)、]", normalized):
        return True
    if re.match(r"(?i)^(chapter|section|article|clause)\s+\d+[.)]?$", normalized):
        return True
    if re.search(rf"[{CHINESE_NUMBER_PATTERN}]", normalized):
        return bool(
            re.search(r"[.．。)、）\]】、-]", normalized)
            or any(marker in normalized for marker in ("第", "章", "节", "条", "款"))
        )
    return False


def _localize_prefix_candidates(prefix: str) -> list[str]:
    normalized = normalize_text(prefix)
    if not normalized:
        return []

    candidates: list[str] = []
    numbered_label_match = re.fullmatch(
        rf"第\s*([{CHINESE_NUMBER_PATTERN}]+)\s*([章节条款])",
        normalized,
    )
    if numbered_label_match:
        value = _parse_number_token(numbered_label_match.group(1))
        label = LOCALIZED_NUMBERING_LABELS.get(numbered_label_match.group(2))
        if value is not None and label:
            candidates.append(f"{label} {value}")

    list_match = re.fullmatch(rf"([{CHINESE_NUMBER_PATTERN}]+)\s*[、．。.]", normalized)
    if list_match:
        value = _parse_number_token(list_match.group(1))
        if value is not None:
            candidates.append(f"{value}.")

    parenthesized_match = re.fullmatch(rf"[（(]\s*([{CHINESE_NUMBER_PATTERN}]+)\s*[)）]", normalized)
    if parenthesized_match:
        value = _parse_number_token(parenthesized_match.group(1))
        if value is not None:
            candidates.append(f"({value})")

    if "、" in normalized:
        candidates.append(normalized.replace("、", "."))
    if "．" in normalized or "。" in normalized:
        candidates.append(normalized.replace("．", ".").replace("。", "."))
    if "（" in normalized or "）" in normalized:
        candidates.append(normalized.replace("（", "(").replace("）", ")"))
    return candidates


def _parse_number_token(token: str) -> int | None:
    normalized = normalize_text(token)
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)
    return _parse_chinese_number(normalized)


def _parse_chinese_number(text: str) -> int | None:
    if not text or any(char not in CHINESE_DIGIT_VALUES and char not in CHINESE_UNIT_VALUES for char in text):
        return None

    total = 0
    section = 0
    number = 0
    for char in text:
        if char in CHINESE_DIGIT_VALUES:
            number = CHINESE_DIGIT_VALUES[char]
            continue

        unit = CHINESE_UNIT_VALUES[char]
        if unit == 10000:
            section = (section + number) or 1
            total += section * unit
            section = 0
            number = 0
            continue

        section += (number or 1) * unit
        number = 0

    return total + section + number


def _strip_prefix_once(text: str, prefix: str) -> str:
    normalized_prefix = normalize_text(prefix)
    if not normalized_prefix:
        return text

    pattern = re.compile(r"^\s*" + _prefix_to_regex(normalized_prefix), re.IGNORECASE)
    match = pattern.match(text)
    if match is None:
        return text

    rest = text[match.end() :]
    cleaned_rest = _trim_prefix_remainder(normalized_prefix, rest)
    if cleaned_rest is None:
        return text
    return cleaned_rest


def _prefix_to_regex(prefix: str) -> str:
    parts = re.split(r"\s+", prefix)
    return r"\s+".join(re.escape(part) for part in parts if part)


def _trim_prefix_remainder(prefix: str, rest: str) -> str | None:
    if not rest:
        return None
    if rest[0].isspace():
        return rest.lstrip()
    if rest[0] in OPTIONAL_PREFIX_SEPARATORS:
        return rest[1:].lstrip()
    if _allows_tight_boundary(prefix, rest[0]):
        return rest.lstrip()
    return None


def _allows_tight_boundary(prefix: str, next_char: str) -> bool:
    if prefix.endswith(".") and next_char.isdigit():
        return False
    if prefix.endswith(TIGHT_BOUNDARY_SUFFIXES):
        return True
    return bool(re.search(r"第\s*.+[章节条款]$", prefix))


def _dedupe_prefixes(prefixes: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for prefix in prefixes:
        normalized = normalize_text(prefix or "")
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    result.sort(key=len, reverse=True)
    return result


def _get_value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)
