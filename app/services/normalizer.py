import hashlib
import re


INLINE_INVISIBLE_CHAR_PATTERN = re.compile(
    r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u200B-\u200D\uFEFF]"
)
WHITESPACE_PATTERN = re.compile(r"\s+")
LINE_WHITESPACE_PATTERN = re.compile(r"[^\S\n]+")
TRAILING_SENTENCE_MARK_PATTERN = re.compile(r"[\u3002\uff01\uff1f!?.]+$")
SPACE_BEFORE_PUNCTUATION_PATTERN = re.compile(
    r"\s+([\u3002\uff01\uff1f!?.\uff0c,\u3001\uff1b;\uff1a:\uff09\)\]\}])"
)


def normalize_text(text: str) -> str:
    if not text:
        return ""

    cleaned = INLINE_INVISIBLE_CHAR_PATTERN.sub(" ", text)
    cleaned = WHITESPACE_PATTERN.sub(" ", cleaned)
    cleaned = SPACE_BEFORE_PUNCTUATION_PATTERN.sub(r"\1", cleaned)
    return cleaned.strip()


def normalize_text_preserve_lines(text: str) -> str:
    if not text:
        return ""

    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = INLINE_INVISIBLE_CHAR_PATTERN.sub(" ", cleaned)
    cleaned = LINE_WHITESPACE_PATTERN.sub(" ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_match_text(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""

    return TRAILING_SENTENCE_MARK_PATTERN.sub("", normalized).strip()


def build_source_hash(text: str) -> str:
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
