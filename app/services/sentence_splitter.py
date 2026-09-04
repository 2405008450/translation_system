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

# 常见英文缩写（句号通常不是句界）。这里仅收录稳定缩写，具体是否断句仍结合后文判断。
COMMON_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "etc", "inc", "ltd", "co",
    "corp", "st", "ave", "blvd", "rd", "dept", "gov", "gen", "col", "lt", "sgt",
    "rev", "hon", "pres", "pp", "vol", "no", "fig", "ed", "eds", "trans", "approx",
    "art", "sec", "cl", "para", "subpara", "sch", "app", "ex", "ref", "refs",
    "e.g", "i.e", "cf", "al", "et"
}

# 这些词常跟在点分首字母缩写之后并共同构成专名，例如 U.S. Treasury、B.V. Company。
INITIALISM_CONTINUATION_WORDS = {
    "administration", "agency", "agreement", "association", "bank", "bureau", "company",
    "corporation", "department", "government", "group", "holdings", "law", "limited",
    "ministry", "office", "patent", "regulation", "securities", "treasury",
}

# 大写开头并不必然意味着新句；只有明显的新句起始词才允许结束点分缩写后的句子。
COMMON_SENTENCE_STARTERS = {
    "a", "an", "the", "this", "that", "these", "those", "it", "he", "she", "they",
    "we", "you", "however", "therefore", "accordingly", "meanwhile", "next", "then",
}

# 匹配数字相关的句号（小数、序号等）
NUMBER_DOT_PATTERN = re.compile(r'\d$')
# 匹配单个大写字母（如 A. B. C. 或人名首字母缩写）
SINGLE_LETTER_PATTERN = re.compile(r'^[A-Z]$')
# 匹配罗马数字序号（i. ii. iii. iv. v. vi. vii. viii. ix. x. 等）
ROMAN_NUMERAL_PATTERN = re.compile(r'^[ivxlcdm]+$', re.IGNORECASE)
# 多段首字母缩写，如 C.J.、C.J.B.H.、U.S.。这些句点属于缩写本身，
# 即使后面紧跟方括号引用或大写单词也不能切句。
INITIALISM_PATTERN = re.compile(r'^(?:[A-Za-z]\.)+[A-Za-z]$')

# 独立编号标题既是解析边界也是对齐硬边界。限制长度和结尾，避免把正常编号条款正文误判成标题。
NUMBERED_HEADING_PATTERN = re.compile(
    r"^\s*(?:\d+(?:\.\d+){0,5}|[A-Z])(?:[.)])?\s+(?P<title>\S.*)$"
)

# Word 中常见“短标题 + 手动换行 + 长说明正文”的排版。标题通常没有句号，
# 下一行明显更长并以完整句标点结尾；长度比例用于避免拆开“采\n购”这类词内换行。
HEADING_LINE_MAX_LENGTH = 80
HEADING_LINE_MAX_WORDS = 14
HEADING_BODY_MIN_LENGTH = 24
HEADING_BODY_LENGTH_RATIO = 2
HEADING_FORBIDDEN_PUNCTUATION = frozenset("。？！?!.；;：:，,")
HEADING_BODY_ENDINGS = frozenset("。？！?!.")


def looks_like_numbered_heading(text: str) -> bool:
    value = text.strip()
    if not value or "\n" in value or "\r" in value or len(value) > 180:
        return False
    match = NUMBERED_HEADING_PATTERN.match(value)
    if not match:
        return False
    title = match.group("title").strip()
    if not title or len(title.split()) > 24:
        return False
    # 冒号常用于合同小节标题；完整句号、问号、感叹号和分号则更像条款正文。
    return not title.endswith((".", "?", "!", "。", "？", "！", ";", "；"))


def looks_like_short_heading_before_body(current_line: str, next_line: str) -> bool:
    heading = normalize_text(current_line)
    body = normalize_text(next_line)
    if not heading or not body or "\n" in heading or "\r" in heading:
        return False
    if len(heading) > HEADING_LINE_MAX_LENGTH or len(heading.split()) > HEADING_LINE_MAX_WORDS:
        return False
    if any(char in HEADING_FORBIDDEN_PUNCTUATION for char in heading):
        return False
    if len(body) < max(HEADING_BODY_MIN_LENGTH, len(heading) * HEADING_BODY_LENGTH_RATIO):
        return False
    return body[-1] in HEADING_BODY_ENDINGS


@dataclass(frozen=True)
class SentenceSpan:
    start: int
    end: int


def split_sentence_spans(text: str, *, preserve_dotted_names: bool = False) -> list[SentenceSpan]:
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
            next_line_end = index
            while next_line_end < text_length and scan_text[next_line_end] not in {"\n", "\r"}:
                next_line_end += 1
            current_line = scan_text[start:newline_start].strip()
            next_line = scan_text[index:next_line_end].strip()
            if (
                newline_count >= 2
                or looks_like_numbered_heading(current_line)
                or looks_like_numbered_heading(next_line)
                or looks_like_short_heading_before_body(current_line, next_line)
            ):
                end = _trim_right_boundary(scan_text, newline_start)
                if end > start:
                    spans.append(SentenceSpan(start=start, end=end))
                start = None
            continue

        if current_char in SENTENCE_ENDINGS:
            # 对于英文句号，需要检查是否为缩写或数字
            if current_char == "." and not _is_sentence_ending_dot(
                scan_text, index, preserve_dotted_names=preserve_dotted_names,
            ):
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


def split_sentences(text: str, *, preserve_dotted_names: bool = False) -> list[str]:
    normalized_text = normalize_text_preserve_lines(text)
    if not normalized_text:
        return []

    sentences: list[str] = []
    for span in split_sentence_spans(
        normalized_text, preserve_dotted_names=preserve_dotted_names,
    ):
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


def _is_sentence_ending_dot(
    text: str, dot_index: int, *, preserve_dotted_names: bool = False,
) -> bool:
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
    
    next_non_space = dot_index + 1
    while next_non_space < text_length and text[next_non_space].isspace():
        next_non_space += 1
    next_word_match = re.match(r"[A-Za-z]+", text[next_non_space:])
    next_word = next_word_match.group(0).lower() if next_word_match else ""

    # 检查是否是数字后的句号（小数、版本号、层级编号或行首序号）
    if word_before_dot and NUMBER_DOT_PATTERN.search(word_before_dot):
        # 小数、版本号和层级条款号，例如 3.14、v1.2、Clause 1.2。
        if dot_index + 1 < text_length and text[dot_index + 1].isdigit():
            return False
        line_start = text.rfind("\n", 0, word_start) + 1
        if not text[line_start:word_start].strip() and next_non_space < text_length:
            # 行首的“1. Definitions”是编号，不是上一句的句点。
            return False
        # 普通数字也可能位于真实句尾，例如 “in 2024. The ...”。
        return True
    
    # 检查是否是常见缩写
    word_lower = word_before_dot.lower().rstrip(".")
    if word_lower in COMMON_ABBREVIATIONS:
        if next_non_space >= text_length:
            return True
        # “etc. The ...”可以结束句子；“No. 10”或“Inc. entered”继续当前句。
        return next_word in COMMON_SENTENCE_STARTERS and next_word not in {"a", "an"}
    
    # 检查是否是单个大写字母（如 A. B. 或人名首字母 J. K.）
    if SINGLE_LETTER_PATTERN.match(word_before_dot):
        return False

    # 检查多段首字母缩写（最终句点本身不包含在 word_before_dot 中）。
    if INITIALISM_PATTERN.match(word_before_dot):
        # 法规/文件引用中的 C.J. [2023] 属于名称内部缩写；而 C.V. PROPERTY
        # 或 U.S.A. – ... 在现有文档中承担真实句界，需要结合后续字符判断。
        if (
            dot_index + 2 < text_length
            and text[dot_index + 1].isalpha()
            and text[dot_index + 2] == "."
        ):
            return False
        if next_non_space >= text_length:
            return True
        following = text[next_non_space]
        if following in "([（【[":
            return False
        if following in "-–—":
            return True
        if next_word in INITIALISM_CONTINUATION_WORDS:
            return False
        if following.isupper():
            # 点分专名后跟大写名称时优先保持完整，例如 C.V. PROPERTY、S.p.A. Holdings。
            # 真正的新句通常还能由后续标点切开；破折号边界已在上方单独处理。
            return (
                next_word in COMMON_SENTENCE_STARTERS
                if preserve_dotted_names
                else True
            )
        if "\u3400" <= following <= "\u9fff":
            return True
        return False
    
    # 检查是否是罗马数字序号（如 i. ii. iii. iv. 等）
    if ROMAN_NUMERAL_PATTERN.match(word_before_dot):
        return False
    
    # 检查句号后面的字符
    if dot_index + 1 < text_length:
        next_char = text[dot_index + 1]
        # 如果句号后直接跟着字母或数字，不是句子结束（如文件名 file.txt）
        if next_char.isascii() and next_char.isalnum():
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
