from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

COUNT_POLICY_VERSION = "text-content-v5-parsed"

_WORD_ADDRESS = re.compile(r"(?:https?://|www\.)[^\s<>]+|(?:mailto:)?[\w.+@-]+@[\w.-]+", re.IGNORECASE)

SCRIPT_COUNT_FIELDS = (
    "han_count",
    "kana_count",
    "hangul_count",
    "latin_word_count",
    "number_token_count",
    "mixed_latin_number_count",
    "cyrillic_word_count",
    "arabic_word_count",
    "greek_word_count",
    "hebrew_word_count",
    "thai_word_count",
    "cjk_punct_count",
    "other_count",
)

QUOTE_COUNT_FIELDS = (
    "billable_chinese_count",
    "billable_japanese_count",
    "billable_korean_count",
    "billable_latin_count",
    "billable_cyrillic_count",
    "billable_arabic_count",
    "billable_greek_count",
    "billable_hebrew_count",
    "billable_thai_count",
)

@dataclass(frozen=True)
class TextMetrics:
    word_count: int
    non_space_chars: int
    raw_chars: int
    cjk_char_count_no_spaces: int = 0
    cjk_char_count_with_spaces: int = 0
    latin_char_count_no_spaces: int = 0
    latin_char_count_with_spaces: int = 0
    han_count: int = 0
    kana_count: int = 0
    hangul_count: int = 0
    latin_word_count: int = 0
    number_token_count: int = 0
    mixed_latin_number_count: int = 0
    cyrillic_word_count: int = 0
    arabic_word_count: int = 0
    greek_word_count: int = 0
    hebrew_word_count: int = 0
    thai_word_count: int = 0
    cjk_punct_count: int = 0
    other_count: int = 0

    @property
    def script_count_total(self) -> int:
        return sum(int(getattr(self, field, 0)) for field in SCRIPT_COUNT_FIELDS)

    @property
    def billable_chinese_count(self) -> int:
        return self.han_count + self.cjk_punct_count

    @property
    def billable_japanese_count(self) -> int:
        return self.han_count + self.kana_count + self.cjk_punct_count

    @property
    def billable_korean_count(self) -> int:
        return self.hangul_count + self.cjk_punct_count

    @property
    def billable_latin_count(self) -> int:
        return self.latin_word_count + self.mixed_latin_number_count

    @property
    def billable_cyrillic_count(self) -> int:
        return self.cyrillic_word_count

    @property
    def billable_arabic_count(self) -> int:
        return self.arabic_word_count

    @property
    def billable_greek_count(self) -> int:
        return self.greek_word_count

    @property
    def billable_hebrew_count(self) -> int:
        return self.hebrew_word_count

    @property
    def billable_thai_count(self) -> int:
        return self.thai_word_count

    def script_counts(self) -> dict[str, int]:
        return {field: int(getattr(self, field, 0)) for field in SCRIPT_COUNT_FIELDS}

    def quote_counts(self) -> dict[str, int]:
        return {field: int(getattr(self, field, 0)) for field in QUOTE_COUNT_FIELDS}

def _count_text(text: str) -> TextMetrics:
    content = text or ""
    counts = {field: 0 for field in SCRIPT_COUNT_FIELDS}
    index = 0
    while index < len(content):
        char = content[index]
        cjk_field = _cjk_script_field(char)
        if cjk_field:
            counts[cjk_field] += 1
            index += 1
            continue
        address = _WORD_ADDRESS.match(content, index)
        if address:
            counts["mixed_latin_number_count" if any(c.isdigit() for c in address[0]) else "latin_word_count"] += 1
            index = address.end()
            continue
        if _is_token_char(char):
            token, index = _consume_word_like_token(content, index)
            counts[_token_count_field(token)] += 1
            continue
        previous_char = content[index - 1] if index > 0 else ""
        next_char = content[index + 1] if index + 1 < len(content) else ""
        if _is_word_count_cjk_punctuation(char, previous_char, next_char):
            counts["cjk_punct_count"] += 1
            index += 1
            continue
        index += 1
    # Word 会将独立符号词及法式引号计入字数，破折号与项目符号不计。
    counts["other_count"] += content.count("«") + content.count("»")
    for token in content.split():
        if token and all(unicodedata.category(c) == "Cf" for c in token):
            counts["other_count"] += 1
        if (all(unicodedata.category(c)[0] in {"P", "S"} for c in token)
                and not any(_is_word_count_cjk_punctuation(c) for c in token)
                and not any(c in "«»—–•·()[]{}\"'“”‘’" for c in token)):
            counts["other_count"] += 1
    word_count = sum(counts.values())
    cjk_char_count_no_spaces, cjk_char_count_with_spaces = _count_cjk_candidate_characters(content)
    latin_char_count_no_spaces, latin_char_count_with_spaces = _count_latin_candidate_characters(content)
    return TextMetrics(
        word_count=word_count,
        non_space_chars=sum(1 for char in text or "" if not char.isspace()),
        raw_chars=len(text or ""),
        cjk_char_count_no_spaces=cjk_char_count_no_spaces,
        cjk_char_count_with_spaces=cjk_char_count_with_spaces,
        latin_char_count_no_spaces=latin_char_count_no_spaces,
        latin_char_count_with_spaces=latin_char_count_with_spaces,
        **counts,
    )

def _count_cjk_candidate_characters(text: str) -> tuple[int, int]:
    """只统计中日韩主导文本片段的字符数，避免把整篇拉丁文本并入候选值。"""
    return _count_candidate_characters(text, _is_cjk_dominant_segment)

def _count_latin_candidate_characters(text: str) -> tuple[int, int]:
    """只统计拉丁文字主导文本片段的字符数。"""
    return _count_candidate_characters(text, _is_latin_dominant_segment)

def _count_candidate_characters(
    text: str,
    is_selected_segment: Callable[[str], bool],
) -> tuple[int, int]:
    parts = re.split(r"([\t\r\n]+)", text or "")
    segments = parts[::2]
    separators = parts[1::2]
    selected = [is_selected_segment(segment) for segment in segments]

    no_spaces = sum(
        sum(1 for char in segment if not char.isspace())
        for segment, is_selected in zip(segments, selected)
        if is_selected
    )
    with_spaces = sum(
        len(segment)
        for segment, is_selected in zip(segments, selected)
        if is_selected
    )
    # 只有分隔符两侧都属于同一候选语系时，才把制表符或换行计入“计空格”。
    with_spaces += sum(
        len(separator)
        for index, separator in enumerate(separators)
        if selected[index] and selected[index + 1]
    )
    return no_spaces, with_spaces

def _is_cjk_dominant_segment(text: str) -> bool:
    cjk_count = sum(1 for char in text if _is_cjk(char))
    if cjk_count == 0:
        return False

    non_cjk_word_count = 0
    index = 0
    while index < len(text):
        char = text[index]
        if _is_cjk(char):
            index += 1
            continue
        if _is_token_char(char):
            token, index = _consume_word_like_token(text, index)
            if _token_count_field(token) != "number_token_count":
                non_cjk_word_count += 1
            continue
        index += 1
    return cjk_count >= non_cjk_word_count

def _is_latin_dominant_segment(text: str) -> bool:
    latin_word_count = 0
    other_language_count = sum(1 for char in text if _is_cjk(char))
    index = 0
    while index < len(text):
        char = text[index]
        if _is_cjk(char):
            index += 1
            continue
        if _is_token_char(char):
            token, index = _consume_word_like_token(text, index)
            field = _token_count_field(token)
            if field in {"latin_word_count", "mixed_latin_number_count"}:
                latin_word_count += 1
            elif field != "number_token_count":
                other_language_count += 1
            continue
        index += 1
    # 平票的混合片段归入中日韩候选，避免同一片段在两个字符候选中重复出现。
    return latin_word_count > other_language_count

def _consume_word_like_token(text: str, start: int) -> tuple[str, int]:
    index = start
    while index < len(text):
        char = text[index]
        if _is_token_char(char):
            index += 1
            continue
        if unicodedata.category(char) in {"Mn", "Mc", "Me", "Cf"}:
            index += 1
            continue
        if char in {"'", "’", "-", "‐", "‑", "−", "_", ".", "/"}:
            index += 1
            continue
        if char in {"“", "”", "‘", "…", "·"} and not (index + 1 < len(text) and _is_cjk(text[index + 1])):
            index += 1
            continue
        # Word 将字母间的 ASCII 标点及排版用空格视为同一西文词。
        if (ord(char) < 128 and unicodedata.category(char)[0] in {"P", "S"}
                and not (index + 1 < len(text) and _is_cjk(text[index + 1]))):
            index += 1
            continue
        if char in "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a":
            index += 1
            continue
        if char == "," and index > start and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit():
            index += 1
            continue
        break
    return text[start:index], index

def _cjk_script_field(char: str) -> str:
    if _is_han(char):
        return "han_count"
    if _is_kana(char):
        return "kana_count"
    if _is_hangul(char):
        return "hangul_count"
    return ""

def _is_cjk(char: str) -> bool:
    return bool(_cjk_script_field(char))

def _is_han(char: str) -> bool:
    if not char or len(char) != 1:
        return False
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0x2CEB0 <= code <= 0x2EBEF
        or 0x2F800 <= code <= 0x2FA1F
        or 0x30000 <= code <= 0x3134F
    )

def _is_kana(char: str) -> bool:
    if not char or len(char) != 1:
        return False
    code = ord(char)
    return (
        0x3040 <= code <= 0x30FF
        or 0x31F0 <= code <= 0x31FF
        or 0xFF66 <= code <= 0xFF9D
    )

def _is_hangul(char: str) -> bool:
    if not char or len(char) != 1:
        return False
    code = ord(char)
    return (
        0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xA960 <= code <= 0xA97F
        or 0xAC00 <= code <= 0xD7AF
        or 0xD7B0 <= code <= 0xD7FF
    )

def _is_token_char(char: str) -> bool:
    if not char or _is_cjk(char):
        return False
    category = unicodedata.category(char)
    return category[0] in {"L", "N"}

def _token_count_field(token: str) -> str:
    letter_scripts: set[str] = set()
    has_number = False
    for char in token:
        if not _is_token_char(char):
            continue
        category = unicodedata.category(char)
        if category[0] == "N":
            has_number = True
            continue
        letter_scripts.add(_letter_script(char))

    if not letter_scripts and has_number:
        return "number_token_count"
    if len(letter_scripts) == 1:
        script = next(iter(letter_scripts))
        if script == "latin":
            return "mixed_latin_number_count" if has_number else "latin_word_count"
        script_field = {
            "cyrillic": "cyrillic_word_count",
            "arabic": "arabic_word_count",
            "greek": "greek_word_count",
            "hebrew": "hebrew_word_count",
            "thai": "thai_word_count",
        }.get(script)
        if script_field:
            return script_field
    return "other_count"

def _letter_script(char: str) -> str:
    code = ord(char)
    name = unicodedata.name(char, "")
    if "LATIN" in name:
        return "latin"
    if "CYRILLIC" in name:
        return "cyrillic"
    if "ARABIC" in name or 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F or 0x08A0 <= code <= 0x08FF:
        return "arabic"
    if "GREEK" in name:
        return "greek"
    if "HEBREW" in name:
        return "hebrew"
    if 0x0E00 <= code <= 0x0E7F:
        return "thai"
    return "other"

def _is_word_count_cjk_punctuation(char: str, previous_char: str = "", next_char: str = "") -> bool:
    if not char or char.isspace() or _is_token_char(char):
        return False
    code = ord(char)
    category = unicodedata.category(char)
    if category[0] not in {"P", "S"}:
        return False

    # Word 的中文统计会把中文/全角标点算入“中文字符”，但不会把 © 这类普通符号算入字数。
    if (
        0x3000 <= code <= 0x303F
        or 0xFE10 <= code <= 0xFE1F
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFFEF
    ):
        return True

    chinese_context_punctuation = {"“", "”", "‘", "’", "—", "–", "…", "·"}
    if char in {"√", "□", "☑", "✓"}:
        return True
    if char in ",:;!?" and (_is_cjk(previous_char) or _is_cjk(next_char)):
        return True
    if char in chinese_context_punctuation and (_is_cjk(previous_char) or _is_cjk(next_char)):
        return True

    return False

count_text = _count_text


def word_tokens(text: str) -> list[str]:
    """为重复覆盖统计提供与主计数相同的词边界。"""
    tokens = []
    index = 0
    while index < len(text):
        char = text[index]
        if _cjk_script_field(char):
            tokens.append(char)
            index += 1
            continue
        address = _WORD_ADDRESS.match(text, index)
        if address:
            tokens.append(address[0])
            index = address.end()
            continue
        if _is_token_char(char):
            token, index = _consume_word_like_token(text, index)
            tokens.append(token)
            continue
        previous = text[index-1] if index else ""
        following = text[index+1] if index+1 < len(text) else ""
        if _is_word_count_cjk_punctuation(char, previous, following) or char in "«»":
            tokens.append(char)
        index += 1
    for token in text.split():
        if token and all(unicodedata.category(c) == "Cf" for c in token):
            tokens.append(token)
        if (all(unicodedata.category(c)[0] in {"P", "S"} for c in token)
            and not any(_is_word_count_cjk_punctuation(c) for c in token)
            and not any(c in "«»—–•·()[]{}\"'“”‘’" for c in token)):
            tokens.append(token)
    return tokens
