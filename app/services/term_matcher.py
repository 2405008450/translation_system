import re
from dataclasses import dataclass


ASCII_WORD_CHAR_PATTERN = re.compile(r"[A-Za-z0-9_]")
ASCII_LETTER_PATTERN = re.compile(r"[A-Za-z]")
ASCII_LOWER_PATTERN = re.compile(r"[a-z]")
ASCII_UPPER_PATTERN = re.compile(r"[A-Z]")


@dataclass(frozen=True)
class TermTextMatch:
    start: int
    end: int


def _is_ascii_word_char(value: str) -> bool:
    return bool(value and ASCII_WORD_CHAR_PATTERN.fullmatch(value))


def _is_acronym_like_term(term: str) -> bool:
    """全大写英文缩写默认按大小写精确匹配，避免 IT 命中 it/with/monitoring。"""
    compact = re.sub(r"[^A-Za-z0-9]", "", term.strip())
    return (
        bool(compact)
        and bool(ASCII_LETTER_PATTERN.search(compact))
        and bool(ASCII_UPPER_PATTERN.search(compact))
        and not ASCII_LOWER_PATTERN.search(compact)
    )


def _use_case_sensitive_match(term: str, case_sensitive: bool) -> bool:
    return case_sensitive or _is_acronym_like_term(term)


def _has_ascii_word_boundary(text: str, start: int, end: int, term: str) -> bool:
    """英文/数字术语只允许在词边界命中；中文等非 ASCII 术语仍允许子串匹配。"""
    stripped_term = term.strip()
    if not stripped_term:
        return False

    if _is_ascii_word_char(stripped_term[0]) and start > 0 and _is_ascii_word_char(text[start - 1]):
        return False
    if _is_ascii_word_char(stripped_term[-1]) and end < len(text) and _is_ascii_word_char(text[end]):
        return False
    return True


def find_term_text_matches(text: str, term: str, *, case_sensitive: bool = False) -> list[TermTextMatch]:
    clean_term = (term or "").strip()
    if not text or not clean_term:
        return []

    use_case_sensitive = _use_case_sensitive_match(clean_term, case_sensitive)
    haystack = text if use_case_sensitive else text.lower()
    needle = clean_term if use_case_sensitive else clean_term.lower()

    matches: list[TermTextMatch] = []
    start = 0
    while True:
        pos = haystack.find(needle, start)
        if pos == -1:
            break

        end = pos + len(clean_term)
        if _has_ascii_word_boundary(text, pos, end, clean_term):
            matches.append(TermTextMatch(start=pos, end=end))
        start = pos + 1

    return matches


def text_contains_term(text: str | None, term: str | None, *, case_sensitive: bool = False) -> bool:
    return bool(find_term_text_matches(text or "", term or "", case_sensitive=case_sensitive))
