from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal

from bs4 import BeautifulSoup, Comment, NavigableString


Dialect = Literal["british", "american"]
WordPair = tuple[str, str]
DEFAULT_LEXICON_PATH = Path(__file__).resolve().parent.parent / "resources" / "english_variant_lexicon.csv"
LEXICON_COLUMNS = {
    "british",
    "american",
    "category",
    "form",
    "to_american_enabled",
    "to_british_enabled",
    "source_refs",
    "notes",
}
_WORD_BOUNDARY_LEFT = r"(?<![A-Za-z0-9])"
_WORD_BOUNDARY_RIGHT = r"(?![A-Za-z0-9])"


@dataclass(frozen=True)
class TextReplacement:
    start: int
    end: int
    before: str
    after: str


@dataclass(frozen=True)
class ConversionResult:
    text: str
    replacements: tuple[TextReplacement, ...]

    @property
    def replacement_count(self) -> int:
        return len(self.replacements)


def _parse_enabled(value: str, *, row_number: int, column: str) -> bool:
    normalized = (value or "").strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"词库第 {row_number} 行的 {column} 必须是 true 或 false")


def load_lexicon(path: str | Path = DEFAULT_LEXICON_PATH) -> tuple[tuple[WordPair, ...], tuple[WordPair, ...]]:
    lexicon_path = Path(path).expanduser().resolve()
    if not lexicon_path.is_file():
        raise FileNotFoundError(f"英美英语词库不存在：{lexicon_path}")

    to_american: dict[str, WordPair] = {}
    to_british: dict[str, WordPair] = {}
    american_targets: set[str] = set()
    british_targets: set[str] = set()
    with lexicon_path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if set(reader.fieldnames or ()) != LEXICON_COLUMNS:
            raise ValueError("英美英语词库列结构不符合要求")
        for row_number, row in enumerate(reader, start=2):
            british = (row.get("british") or "").strip()
            american = (row.get("american") or "").strip()
            if not british or not american or british.casefold() == american.casefold():
                raise ValueError(f"词库第 {row_number} 行包含无效词对")

            if _parse_enabled(row.get("to_american_enabled") or "", row_number=row_number, column="to_american_enabled"):
                key = british.casefold()
                previous = to_american.setdefault(key, (british, american))
                if previous[1].casefold() != american.casefold():
                    raise ValueError(f"词库第 {row_number} 行与已有英转美映射冲突：{british}")
                american_targets.add(american.casefold())

            if _parse_enabled(row.get("to_british_enabled") or "", row_number=row_number, column="to_british_enabled"):
                key = american.casefold()
                previous = to_british.setdefault(key, (american, british))
                if previous[1].casefold() != british.casefold():
                    raise ValueError(f"词库第 {row_number} 行与已有美转英映射冲突：{american}")
                british_targets.add(british.casefold())

    if set(to_american).intersection(american_targets):
        raise ValueError("启用的英转美词库存在源词/目标词重叠")
    if set(to_british).intersection(british_targets):
        raise ValueError("启用的美转英词库存在源词/目标词重叠")
    if not to_american and not to_british:
        raise ValueError("英美英语词库中没有启用的映射")
    return tuple(to_american.values()), tuple(to_british.values())


def _compile_rules(pairs: Iterable[WordPair]) -> tuple[dict[str, str], re.Pattern[str] | None]:
    normalized_pairs = tuple(pairs)
    lookup = {source.casefold(): target for source, target in normalized_pairs}
    if not lookup:
        return lookup, None
    protected_targets = {target.casefold() for _, target in normalized_pairs}
    candidates = sorted(set(lookup).union(protected_targets), key=len, reverse=True)
    alternatives = "|".join(re.escape(candidate) for candidate in candidates)
    return lookup, re.compile(
        f"{_WORD_BOUNDARY_LEFT}(?:{alternatives}){_WORD_BOUNDARY_RIGHT}",
        flags=re.IGNORECASE,
    )


def _capitalize_first_letter(text: str) -> str:
    for index, char in enumerate(text):
        if "a" <= char <= "z":
            return text[:index] + char.upper() + text[index + 1 :]
        if "A" <= char <= "Z":
            return text
    return text


def _match_case(source_text: str, target_text: str) -> str:
    letters = [char for char in source_text if char.isalpha()]
    if letters and all(char.isupper() for char in letters):
        return target_text.upper()
    if source_text.istitle():
        return target_text.title()

    first_letter_seen = False
    first_is_upper = False
    remaining_are_lower = True
    for char in source_text:
        if not char.isalpha():
            continue
        if not first_letter_seen:
            first_letter_seen = True
            first_is_upper = char.isupper()
        elif not char.islower():
            remaining_are_lower = False
    if first_letter_seen and first_is_upper and remaining_are_lower:
        return _capitalize_first_letter(target_text)
    return target_text


class EnglishVariantConverter:
    def __init__(self, british_to_american: Iterable[WordPair], american_to_british: Iterable[WordPair]) -> None:
        self._to_american_lookup, self._to_american_pattern = _compile_rules(british_to_american)
        self._to_british_lookup, self._to_british_pattern = _compile_rules(american_to_british)

    def convert(self, text: str, target_style: Dialect) -> ConversionResult:
        if not isinstance(text, str):
            raise TypeError("text 必须是字符串")
        if target_style == "british":
            lookup, pattern = self._to_british_lookup, self._to_british_pattern
        elif target_style == "american":
            lookup, pattern = self._to_american_lookup, self._to_american_pattern
        else:
            raise ValueError("target_style 只能是 'british' 或 'american'")
        if not text or pattern is None:
            return ConversionResult(text=text, replacements=())

        replacements: list[TextReplacement] = []

        def replace_match(match: re.Match[str]) -> str:
            before = match.group(0)
            target = lookup.get(before.casefold())
            if target is None:
                return before
            after = _match_case(before, target)
            replacements.append(
                TextReplacement(
                    start=match.start(),
                    end=match.end(),
                    before=before,
                    after=after,
                )
            )
            return after

        converted = pattern.sub(replace_match, text)
        return ConversionResult(text=converted, replacements=tuple(replacements))


@lru_cache(maxsize=1)
def get_default_converter() -> EnglishVariantConverter:
    british_to_american, american_to_british = load_lexicon()
    return EnglishVariantConverter(british_to_american, american_to_british)


def _visible_text_nodes(soup: BeautifulSoup) -> list[NavigableString]:
    nodes: list[NavigableString] = []
    for node in soup.find_all(string=True):
        if isinstance(node, Comment):
            continue
        if node.parent and node.parent.name in {"script", "style"}:
            continue
        nodes.append(node)
    return nodes


def convert_html_fragment(
    html_text: str | None,
    plain_text: str,
    *,
    target_style: Dialect,
    converter: EnglishVariantConverter | None = None,
) -> tuple[str | None, ConversionResult]:
    active_converter = converter or get_default_converter()
    plain_result = active_converter.convert(plain_text, target_style)
    if not html_text:
        return None, plain_result
    if not plain_result.replacements:
        return html_text, plain_result

    try:
        soup = BeautifulSoup(html_text, "html.parser")
        nodes = _visible_text_nodes(soup)
        original_parts = [str(node) for node in nodes]
        if "".join(original_parts) != plain_text:
            return None, plain_result

        contents = list(original_parts)
        offsets: list[tuple[int, int]] = []
        cursor = 0
        for part in original_parts:
            offsets.append((cursor, cursor + len(part)))
            cursor += len(part)

        for replacement in reversed(plain_result.replacements):
            affected = [
                index
                for index, (start, end) in enumerate(offsets)
                if start < replacement.end and end > replacement.start
            ]
            if not affected:
                return None, plain_result
            first_index, last_index = affected[0], affected[-1]
            first_start, _ = offsets[first_index]
            last_start, _ = offsets[last_index]
            local_start = replacement.start - first_start
            local_end = replacement.end - last_start
            if first_index == last_index:
                contents[first_index] = (
                    contents[first_index][:local_start]
                    + replacement.after
                    + contents[first_index][local_end:]
                )
            else:
                contents[first_index] = contents[first_index][:local_start] + replacement.after
                for index in affected[1:-1]:
                    contents[index] = ""
                contents[last_index] = contents[last_index][local_end:]

        if "".join(contents) != plain_result.text:
            return None, plain_result
        for node, content in zip(nodes, contents):
            node.replace_with(NavigableString(content))
        return str(soup), plain_result
    except Exception:
        return None, plain_result


__all__ = [
    "ConversionResult",
    "DEFAULT_LEXICON_PATH",
    "Dialect",
    "EnglishVariantConverter",
    "TextReplacement",
    "convert_html_fragment",
    "get_default_converter",
    "load_lexicon",
]
