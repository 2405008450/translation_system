import re

from app.services.normalizer import normalize_text, normalize_text_preserve_lines


SENTENCE_ENDINGS = "。？！!?."
SENTENCE_PATTERN = re.compile(rf"[^{re.escape(SENTENCE_ENDINGS)}]+[{re.escape(SENTENCE_ENDINGS)}]?")


def split_sentences(text: str) -> list[str]:
    normalized_text = normalize_text_preserve_lines(text)
    if not normalized_text:
        return []

    sentences: list[str] = []
    for raw_line in normalized_text.split("\n"):
        line = normalize_text(raw_line)
        if not line:
            continue

        if not any(mark in line for mark in SENTENCE_ENDINGS):
            sentences.append(line)
            continue

        for match in SENTENCE_PATTERN.finditer(line):
            sentence = normalize_text(match.group())
            if sentence:
                sentences.append(sentence)

    return sentences
