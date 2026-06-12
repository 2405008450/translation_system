from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.normalizer import (
    INLINE_INVISIBLE_CHAR_PATTERN,
    normalize_text,
    normalize_text_preserve_lines,
)


# 中文和英文句子结束符
SENTENCE_ENDINGS = "\u3002\uff1f?!.！"
TRAILING_SENTENCE_CLOSERS = "\"'\u201d\u2019\u3011\u300b\u3009\u300f\uff09)]}"

# 常见英文缩写（句号不应断句）
COMMON_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "etc", "inc", "ltd", "co",
    "corp", "st", "ave", "blvd", "rd", "dept", "gov", "gen", "col", "lt", "sgt",
    "rev", "hon", "pres", "pp", "vol", "no", "fig", "ed", "eds", "trans", "approx",
    "e.g", "i.e", "cf", "al", "et"
}

# 匹配数字相关的句号（小数、序号等）
NUMBER_DOT_PATTERN = re.compile(r'\d$')
# 匹配单个大写字母（如 A. B. C. 或人名首字母缩写）
SINGLE_LETTER_PATTERN = re.compile(r'^[A-Z]$')
# 匹配罗马数字序号（i. ii. iii. iv. v. vi. vii. viii. ix. x. 等）
ROMAN_NUMERAL_PATTERN = re.compile(r'^[ivxlcdm]+$', re.IGNORECASE)


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
            # 对于英文句号，需要检查是否为缩写或数字
            if current_char == "." and not _is_sentence_ending_dot(scan_text, index):
                index += 1
                continue
            
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


def _is_sentence_ending_dot(text: str, dot_index: int) -> bool:
    """
    判断句号是否为真正的句子结束符。
    排除以下情况：
    1. 数字后的句号（如 3.14, Article 12.）
    2. 常见缩写后的句号（如 Mr. Dr. Inc.）
    3. 单个大写字母后的句号（如 A. B. 或人名首字母）
    """
    text_length = len(text)
    
    # 获取句号前的单词
    word_start = dot_index - 1
    while word_start >= 0 and (text[word_start].isalnum() or text[word_start] == "."):
        word_start -= 1
    word_start += 1
    
    word_before_dot = text[word_start:dot_index]
    
    # 检查是否是数字后的句号（小数、序号等）
    if word_before_dot and NUMBER_DOT_PATTERN.search(word_before_dot):
        # 检查句号后面是否有数字（小数）
        if dot_index + 1 < text_length and text[dot_index + 1].isdigit():
            return False
        # 数字序号（如 1. 2. 3.）不断句
        return False
    
    # 检查是否是常见缩写
    word_lower = word_before_dot.lower().rstrip(".")
    if word_lower in COMMON_ABBREVIATIONS:
        return False
    
    # 检查是否是单个大写字母（如 A. B. 或人名首字母 J. K.）
    if SINGLE_LETTER_PATTERN.match(word_before_dot):
        return False
    
    # 检查是否是罗马数字序号（如 i. ii. iii. iv. 等）
    if ROMAN_NUMERAL_PATTERN.match(word_before_dot):
        return False
    
    # 检查句号后面的字符
    if dot_index + 1 < text_length:
        next_char = text[dot_index + 1]
        # 如果句号后直接跟着字母或数字，不是句子结束（如文件名 file.txt）
        if next_char.isalnum():
            return False
        # 如果句号后面是空格，再检查空格后的字符
        if next_char.isspace():
            next_non_space = dot_index + 2
            while next_non_space < text_length and text[next_non_space].isspace():
                next_non_space += 1
            # 如果后面是大写字母或到达文本末尾，认为是句子结束
            if next_non_space >= text_length or text[next_non_space].isupper():
                return True
            # 如果后面是小写字母，可能是缩写
            if text[next_non_space].islower():
                return False
    
    # 默认认为是句子结束
    return True
