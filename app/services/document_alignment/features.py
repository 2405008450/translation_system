from __future__ import annotations

import re
from collections import Counter

from .parser import AlignUnit

_CN_NUMBERS = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
_NUMBER_PATTERNS = (
    (re.compile(r"^第([一二三四五六七八九十]+)[章条节]", re.I), lambda m: f"c{_CN_NUMBERS.get(m.group(1), m.group(1))}"),
    (re.compile(r"^第(\d+)[章条节]", re.I), lambda m: f"c{m.group(1)}"),
    (re.compile(r"^chapter\s*([ivx\d]+)", re.I), lambda m: f"c{m.group(1).lower()}"),
    (re.compile(r"^article\s*(\d+)", re.I), lambda m: f"a{m.group(1)}"),
    (re.compile(r"^(\d+)\.", re.I), lambda m: m.group(1)),
    (re.compile(r"^[（(]([一二三四五六七八九十\d]+)[）)]", re.I), lambda m: _CN_NUMBERS.get(m.group(1), m.group(1))),
    (re.compile(r"^([一二三四五六七八九十]+)、", re.I), lambda m: _CN_NUMBERS.get(m.group(1), m.group(1))),
)


def normalize_numbering(value: str) -> str:
    text = value.strip()
    for pattern, extractor in _NUMBER_PATTERNS:
        match = pattern.match(text)
        if match:
            return str(extractor(match)).lower()
    return ""


def unit_numbering(unit: AlignUnit) -> str:
    return normalize_numbering(unit.numbering) or normalize_numbering(unit.text)


def rare_numbers(units: list[AlignUnit]) -> set[str]:
    counts = Counter(number for unit in units for number in set(unit.numbers))
    return {number for number, count in counts.items() if count == 1}


def punctuation_features(text: str) -> dict[str, int | str]:
    stripped = text.rstrip()
    ending = "?" if stripped.endswith(("?", "？")) else "!" if stripped.endswith(("!", "！")) else ":" if stripped.endswith((":", "：")) else "."
    return {
        "ending": ending,
        "brackets": sum(text.count(ch) for ch in "()（）[]【】"),
        "quotes": sum(text.count(ch) for ch in "\"'“”‘’"),
    }
