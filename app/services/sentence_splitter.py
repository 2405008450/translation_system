from __future__ import annotations

from dataclasses import dataclass

from app.services.normalizer import (
    INLINE_INVISIBLE_CHAR_PATTERN,
    normalize_text,
    normalize_text_preserve_lines,
)


# 中文 Word 导入场景优先按句号、问号和连续换行断句。
SENTENCE_ENDINGS = "\u3002\uff1f?"
TRAILING_SENTENCE_CLOSERS = "\"'\u201d\u2019\u3011\u300b\u3009\u300f\uff09)]}"


@dataclass(frozen=True)
class SentenceSpan:
    start: int
    end: int


def split_sentence_spans(text: str) -> list[SentenceSpan]:
    scan_text = _prepare_text_for_span_scan(text)
    if not normalize_text(scan_text):
        return []

    spans: list[SentenceSpan] = []
    start: int | None = None
    index = 0
    text_length = len(scan_text)

    while index < text_length:
        current_char = scan_text[index]

        if start is None:
            if current_char.isspace():
                index += 1
                continue
            start = index

        if current_char in {"\n", "\r"}:
            newline_start = index
            newline_count = 0
            while index < text_length and scan_text[index] in {"\n", "\r"}:
                newline_count += 1
                if scan_text[index] == "\r" and index + 1 < text_length and scan_text[index + 1] == "\n":
                    index += 2
                else:
                    index += 1
            if newline_count >= 2:
                end = _trim_right_boundary(scan_text, newline_start)
                if end > start:
                    spans.append(SentenceSpan(start=start, end=end))
                start = None
            continue

        if current_char in SENTENCE_ENDINGS:
            end = index + 1
            while end < text_length and scan_text[end] in SENTENCE_ENDINGS:
                end += 1
            while end < text_length and scan_text[end] in TRAILING_SENTENCE_CLOSERS:
                end += 1
            spans.append(SentenceSpan(start=start, end=end))
            start = None
            index = end
            continue

        index += 1

    if start is not None:
        end = _trim_right_boundary(scan_text, text_length)
        if end > start:
            spans.append(SentenceSpan(start=start, end=end))

    return [
        span
        for span in spans
        if normalize_text(scan_text[span.start:span.end])
    ]


def split_sentences(text: str) -> list[str]:
    normalized_text = normalize_text_preserve_lines(text)
    if not normalized_text:
        return []

    sentences: list[str] = []
    for span in split_sentence_spans(normalized_text):
        sentence = normalize_text(normalized_text[span.start:span.end])
        if sentence:
            sentences.append(sentence)
    return sentences


def _trim_right_boundary(text: str, end: int) -> int:
    right = end
    while right > 0 and text[right - 1].isspace():
        right -= 1
    return right


def _prepare_text_for_span_scan(text: str) -> str:
    if not text:
        return ""
    return INLINE_INVISIBLE_CHAR_PATTERN.sub(" ", text)
