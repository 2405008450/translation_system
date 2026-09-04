from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .parser import AlignUnit

_CN_NUMBERS = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
_NUMBER_PATTERNS = (
    (re.compile(r"^第([一二三四五六七八九十]+)[章条节]", re.I), lambda m: f"c{_CN_NUMBERS.get(m.group(1), m.group(1))}"),
    (re.compile(r"^第(\d+)[章条节]", re.I), lambda m: f"c{m.group(1)}"),
    (re.compile(r"^chapter\s*([ivx\d]+)", re.I), lambda m: f"c{m.group(1).lower()}"),
    (re.compile(r"^article\s*(\d+)", re.I), lambda m: f"a{m.group(1)}"),
    # 保留完整层级编号；1、1.1、1.2 必须是不同锚点，不能都退化成“1”。
    (re.compile(r"^(\d+(?:\.\d+){0,5})(?:[.)）]|\s)", re.I), lambda m: m.group(1)),
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


def crosses_heading_boundary(units: list[AlignUnit]) -> bool:
    """标题是原子单元，不能与相邻正文或另一个标题合并成同一配对。"""
    return len(units) > 1 and any(unit.is_heading for unit in units)


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


def comparable_text(value: str) -> str:
    """用于精确锚点的保守归一化，不翻译、不改写原文。"""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def has_structure_conflict(source: list[AlignUnit], target: list[AlignUnit]) -> bool:
    """判断结构类型是否真的冲突，不比较两份独立 DOCX 的物理行列坐标。"""
    source_table = [unit for unit in source if unit.block_type == "table_cell"]
    target_table = [unit for unit in target if unit.block_type == "table_cell"]
    if bool(source_table) != bool(target_table):
        return True
    if not source_table:
        return False
    # 一个候选内部混入正文和表格，或跨越多张表，才属于可靠的结构冲突。
    if len(source_table) != len(source) or len(target_table) != len(target):
        return True
    source_tables = {unit.block_index for unit in source_table}
    target_tables = {unit.block_index for unit in target_table}
    return len(source_tables) > 1 or len(target_tables) > 1


_FIELD_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "stock_code": (
        re.compile(r"证券代码|股票代码"), re.compile(r"\bstock\s*(?:code|symbol)\b", re.I),
        re.compile(r"\bsecurities\s*code\b", re.I),
    ),
    "announcement_no": (
        re.compile(r"公告编号|公告号"), re.compile(r"\bannouncement\s*(?:no\.?|number)\b", re.I),
    ),
    "announcement_date": (
        re.compile(r"公告时间|公告日期"), re.compile(r"\bannouncement\s*date\b", re.I),
    ),
    "report_title": (
        re.compile(r"年度报告(?:全文)?"), re.compile(r"\b(?:full\s+text\s+of\s+)?\d{4}\s+annual\s+report\b", re.I),
    ),
}


def classify_field(text: str) -> str:
    for field_type, patterns in _FIELD_PATTERNS.items():
        if any(pattern.search(text) for pattern in patterns):
            return field_type
    return ""
