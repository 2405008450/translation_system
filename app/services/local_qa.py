"""本地质量保证规则集合（无外部依赖，与 LanguageTool 拼写语法 QA 并列使用）。

覆盖 12 条项目 QA 规则中的以下 10 条（编号与项目设置面板对应，第 6/7 条
分别为 LanguageTool 拼写/语法与术语不一致，由其他模块处理）：

标记与占位符（对比 source_html / target_html / source_text / target_text）
- 1. ``target_without_tag`` 源文包含格式标记但译文完全没有格式标记。
- 2. ``target_tag_missing`` 源文的某类格式标记数量多于译文（如 <b> 被漏掉）。
- 3. ``unmatched_closing_tag`` 译文里出现未匹配的结束标记（``</b>``）。
- 4. ``unmatched_opening_tag`` 译文里出现未闭合的开始标记（``<b>``）。
- 5. ``target_placeholder_missing`` ``⟦MATH_n⟧`` / ``⟦LB_n⟧`` 等占位符在译文中丢失。

标点（仅对比源文与译文文本）
- 8. ``paired_punctuation_missing`` 成对标点符号丢失。
- 9. ``ending_punctuation_mismatch`` 原文和译文的结束标点不同。
- 10. ``repeated_punctuation`` 重复标点。
- 11. ``extra_space_after_punctuation`` 标点符号后有多余空格。
- 12. ``missing_space_after_punctuation`` 标点符号后遗漏空格。

设计原则：
- 所有 detector 采用统一签名 ``(source_text, target_text, source_html, target_html)``，
  用不上 html 的规则可忽略参数，方便注册到同一张检测表里。
- 检测结果统一为 :class:`CleanedLocalIssue`，写入 ``segment_qa_issues`` 表时按
  ``provider`` 字段区分标记类（``tag``）和标点类（``punctuation``），与既有拼写语法
  QA 记录互不干扰。
- 生成 QA 结果、句段保存、批量替换译文时都可以通过 :func:`check_segments_local_qa`
  按项目设置的启用规则集合一次性刷新，未启用的规则历史遗留问题会被标记 ``resolved``。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import FileRecord, Project, Segment, SegmentQAIssue
from app.services.spelling_grammar_qa import (
    QA_ISSUE_STATUS_OPEN,
    QA_ISSUE_STATUS_RESOLVED,
    QA_RULE_ENDING_PUNCTUATION_MISMATCH,
    QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION,
    QA_RULE_MISSING_SPACE_AFTER_PUNCTUATION,
    QA_RULE_PAIRED_PUNCTUATION_MISSING,
    QA_RULE_REPEATED_PUNCTUATION,
    QA_RULE_TARGET_PLACEHOLDER_MISSING,
    QA_RULE_TARGET_TAG_MISSING,
    QA_RULE_TARGET_WITHOUT_TAG,
    QA_RULE_UNMATCHED_CLOSING_TAG,
    QA_RULE_UNMATCHED_OPENING_TAG,
    load_quality_qa_settings,
    target_text_hash,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量与规则分组
# ---------------------------------------------------------------------------

LOCAL_QA_PROVIDER_TAG = "tag"
LOCAL_QA_PROVIDER_PUNCTUATION = "punctuation"

TAG_QA_RULE_KEYS: tuple[str, ...] = (
    QA_RULE_TARGET_WITHOUT_TAG,
    QA_RULE_TARGET_TAG_MISSING,
    QA_RULE_UNMATCHED_CLOSING_TAG,
    QA_RULE_UNMATCHED_OPENING_TAG,
    QA_RULE_TARGET_PLACEHOLDER_MISSING,
)

PUNCTUATION_QA_RULE_KEYS: tuple[str, ...] = (
    QA_RULE_PAIRED_PUNCTUATION_MISSING,
    QA_RULE_ENDING_PUNCTUATION_MISMATCH,
    QA_RULE_REPEATED_PUNCTUATION,
    QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION,
    QA_RULE_MISSING_SPACE_AFTER_PUNCTUATION,
)

LOCAL_QA_RULE_KEYS: tuple[str, ...] = TAG_QA_RULE_KEYS + PUNCTUATION_QA_RULE_KEYS


# ---------------------------------------------------------------------------
# 格式标签与占位符正则
# ---------------------------------------------------------------------------

# 与 document_exporter 保持一致的格式标签集合。
FORMAT_TAG_RE = re.compile(
    r"<(?P<slash>/?)(?P<name>b|strong|i|em|u|s|strike|del|sub|sup)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_ALIAS = {
    "b": "b", "strong": "b",
    "i": "i", "em": "i",
    "u": "u",
    "s": "s", "strike": "s", "del": "s",
    "sub": "sub",
    "sup": "sup",
}
PLACEHOLDER_RE = re.compile(r"⟦(?P<name>MATH_\d+|LB_\d+)⟧")


# ---------------------------------------------------------------------------
# 成对标点与结束标点定义
# ---------------------------------------------------------------------------

# 成对标点分组：把半/全角、弯/直等价形式合并为一组，避免中→英把
# 全角括号改为半角括号被误判为"标点丢失"。
# 每组格式：(display_open, display_close, opens, closes, ambiguous)
# ambiguous 中的字符同时充当开始与结束（如半角 " 或 '），扫描时按奇偶交替判定。
_PAIRED_GROUPS: tuple[tuple[str, str, frozenset[str], frozenset[str], frozenset[str]], ...] = (
    ("(", ")", frozenset("(（"), frozenset(")）"), frozenset()),
    ("[", "]", frozenset("[【"), frozenset("]】"), frozenset()),
    ("{", "}", frozenset("{"), frozenset("}"), frozenset()),
    ("《", "》", frozenset("《〈"), frozenset("》〉"), frozenset()),
    ("「", "」", frozenset("「『"), frozenset("」』"), frozenset()),
    # 双引号：弯引号 “ ” „ 与半角 " 视作同族；半角是 ambiguous。
    ("“", "”", frozenset("“„"), frozenset("”"), frozenset('"')),
    # 单引号：类似处理。
    ("‘", "’", frozenset("‘"), frozenset("’"), frozenset("'")),
)

ENDING_PUNCTUATION_EQUIVALENCE: dict[str, str] = {
    ".": ".",
    "。": ".",
    "!": "!",
    "！": "!",
    "?": "?",
    "？": "?",
    "…": "…",
    "‥": "…",
    ";": ";",
    "；": ";",
    ":": ":",
    "：": ":",
}
ENDING_PUNCTUATION_CHARS = frozenset(ENDING_PUNCTUATION_EQUIVALENCE.keys())
REPEATED_PUNCTUATION_CHARS = frozenset(".,;:!?、，。；：！？")
WESTERN_PUNCTUATION_FOR_SPACING = frozenset(",;:!?.")
FULLWIDTH_PUNCTUATION_FOR_SPACING = frozenset("，。；：！？、")


# ---------------------------------------------------------------------------
# 统一的检测结果模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CleanedLocalIssue:
    rule_key: str
    provider: str
    severity: str
    message: str
    short_message: str
    rule_id: str
    offset: int
    length: int
    context_text: str = ""
    replacements: tuple[str, ...] = field(default_factory=tuple)
    rule_category: str = ""
    issue_type: str = ""

    def fingerprint(self, text_hash: str) -> tuple[str, str, str, int, int, str]:
        return (
            text_hash,
            self.rule_key,
            self.rule_id,
            self.offset,
            self.length,
            self.message,
        )


LocalQADetector = Callable[[str, str, str, str], list[CleanedLocalIssue]]


# ---------------------------------------------------------------------------
# 公共工具函数
# ---------------------------------------------------------------------------


def _make_context(text: str, offset: int, length: int, radius: int = 12) -> str:
    start = max(0, offset - radius)
    end = min(len(text), offset + length + radius)
    return text[start:end]


def _first_target_end_offset(target_text: str) -> int:
    if not target_text:
        return 0
    return max(0, len(target_text) - 1)


def _is_western_letter_or_digit(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    if 0x30 <= code <= 0x39:
        return True
    if 0x41 <= code <= 0x5A:
        return True
    if 0x61 <= code <= 0x7A:
        return True
    if 0x00C0 <= code <= 0x024F:
        return True
    return False


# ---------------------------------------------------------------------------
# 标记 / 占位符相关工具
# ---------------------------------------------------------------------------


def _iter_format_tags(html: str) -> list[tuple[str, str, int, int]]:
    tags: list[tuple[str, str, int, int]] = []
    for match in FORMAT_TAG_RE.finditer(html or ""):
        slash = match.group("slash") or ""
        raw_name = match.group("name").lower()
        name = _TAG_ALIAS.get(raw_name, raw_name)
        tags.append((slash, name, match.start(), match.end() - match.start()))
    return tags


def _tag_counts(html: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for slash, name, _offset, _length in _iter_format_tags(html):
        if slash:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def _has_any_format_tag(html: str | None) -> bool:
    if not html:
        return False
    return bool(FORMAT_TAG_RE.search(html))


def _extract_placeholder_map(text: str) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for match in PLACEHOLDER_RE.finditer(text or ""):
        result.setdefault(match.group("name"), []).append(match.start())
    return result


def _build_html_to_plain_offset_map(html: str) -> dict[int, int]:
    if not html:
        return {}
    result: dict[int, int] = {}
    plain_cursor = 0
    i = 0
    n = len(html)
    while i < n:
        ch = html[i]
        if ch == "<":
            end = html.find(">", i)
            if end == -1:
                break
            result[i] = plain_cursor
            i = end + 1
            continue
        plain_cursor += 1
        i += 1
    return result


# ---------------------------------------------------------------------------
# 标记 / 占位符规则（对应设置面板 1-5）
# ---------------------------------------------------------------------------


# 规则 1：译文无标记
def detect_target_without_tag(
    source_text: str,
    target_text: str,
    source_html: str,
    target_html: str,
) -> list[CleanedLocalIssue]:
    if not _has_any_format_tag(source_html):
        return []
    if _has_any_format_tag(target_html):
        return []
    if not (target_html or "").strip() and not (target_text or "").strip():
        return []
    length = max(1, len(target_text) if target_text else 1)
    return [
        CleanedLocalIssue(
            rule_key=QA_RULE_TARGET_WITHOUT_TAG,
            provider=LOCAL_QA_PROVIDER_TAG,
            severity="medium",
            message="源文包含格式标记（如加粗、斜体等），译文没有任何格式标记。",
            short_message="译文无标记",
            rule_id="TARGET_WITHOUT_TAG",
            offset=0,
            length=min(length, max(1, len(target_text) or 1)),
            context_text=_make_context(target_text, 0, length),
            rule_category="tag",
            issue_type="format",
        )
    ]


# 规则 2：译文标记丢失
def detect_target_tag_missing(
    source_text: str,
    target_text: str,
    source_html: str,
    target_html: str,
) -> list[CleanedLocalIssue]:
    if not _has_any_format_tag(source_html):
        return []
    if not (target_html or "").strip():
        return []
    if not _has_any_format_tag(target_html):
        return []  # 交由 target_without_tag 规则统一处理
    source_counts = _tag_counts(source_html)
    target_counts = _tag_counts(target_html)
    issues: list[CleanedLocalIssue] = []
    for name, source_count in source_counts.items():
        target_count = target_counts.get(name, 0)
        if target_count >= source_count:
            continue
        missing = source_count - target_count
        offset = _first_target_end_offset(target_text)
        issues.append(
            CleanedLocalIssue(
                rule_key=QA_RULE_TARGET_TAG_MISSING,
                provider=LOCAL_QA_PROVIDER_TAG,
                severity="medium",
                message=(
                    f"源文包含 {source_count} 个 <{name}> 标记，"
                    f"译文仅 {target_count} 个（缺少 {missing} 个）。"
                ),
                short_message="译文标记丢失",
                rule_id=f"TAG_MISSING:{name}",
                offset=offset,
                length=1,
                context_text=_make_context(target_text, offset, 1),
                replacements=(f"<{name}>…</{name}>",),
                rule_category="tag",
                issue_type="format",
            )
        )
    return issues


# 规则 3：结束标记无匹配的开始标记
def detect_unmatched_closing_tag(
    source_text: str,
    target_text: str,
    source_html: str,
    target_html: str,
) -> list[CleanedLocalIssue]:
    if not (target_html or "").strip():
        return []
    issues: list[CleanedLocalIssue] = []
    stack: list[tuple[str, int, int]] = []
    plain_positions = _build_html_to_plain_offset_map(target_html)
    for slash, name, offset, _length in _iter_format_tags(target_html):
        plain_offset = plain_positions.get(offset, min(offset, max(0, len(target_text) - 1)))
        if not slash:
            stack.append((name, offset, plain_offset))
            continue
        if stack and stack[-1][0] == name:
            stack.pop()
            continue
        issues.append(
            CleanedLocalIssue(
                rule_key=QA_RULE_UNMATCHED_CLOSING_TAG,
                provider=LOCAL_QA_PROVIDER_TAG,
                severity="high",
                message=f"译文包含未匹配的结束标记 </{name}>。",
                short_message="结束标记无匹配",
                rule_id=f"UNMATCHED_CLOSE:{name}",
                offset=plain_offset,
                length=1,
                context_text=_make_context(target_text, plain_offset, 1),
                replacements=(f"<{name}>",),
                rule_category="tag",
                issue_type="format",
            )
        )
    return issues


# 规则 4：开始标记无匹配的结束标记
def detect_unmatched_opening_tag(
    source_text: str,
    target_text: str,
    source_html: str,
    target_html: str,
) -> list[CleanedLocalIssue]:
    if not (target_html or "").strip():
        return []
    issues: list[CleanedLocalIssue] = []
    stack: list[tuple[str, int, int]] = []
    plain_positions = _build_html_to_plain_offset_map(target_html)
    for slash, name, offset, _length in _iter_format_tags(target_html):
        plain_offset = plain_positions.get(offset, min(offset, max(0, len(target_text) - 1)))
        if not slash:
            stack.append((name, offset, plain_offset))
            continue
        if stack and stack[-1][0] == name:
            stack.pop()
    for name, _html_offset, plain_offset in stack:
        issues.append(
            CleanedLocalIssue(
                rule_key=QA_RULE_UNMATCHED_OPENING_TAG,
                provider=LOCAL_QA_PROVIDER_TAG,
                severity="high",
                message=f"译文包含未闭合的开始标记 <{name}>。",
                short_message="开始标记无匹配",
                rule_id=f"UNMATCHED_OPEN:{name}",
                offset=plain_offset,
                length=1,
                context_text=_make_context(target_text, plain_offset, 1),
                replacements=(f"</{name}>",),
                rule_category="tag",
                issue_type="format",
            )
        )
    return issues


# 规则 5：译文占位符标记丢失
def detect_target_placeholder_missing(
    source_text: str,
    target_text: str,
    source_html: str,
    target_html: str,
) -> list[CleanedLocalIssue]:
    source_map = _extract_placeholder_map(source_text or "")
    if not source_map:
        return []
    target_map = _extract_placeholder_map(target_text or "")
    issues: list[CleanedLocalIssue] = []
    for name, source_positions in source_map.items():
        target_positions = target_map.get(name, [])
        if len(target_positions) >= len(source_positions):
            continue
        offset = _first_target_end_offset(target_text)
        issues.append(
            CleanedLocalIssue(
                rule_key=QA_RULE_TARGET_PLACEHOLDER_MISSING,
                provider=LOCAL_QA_PROVIDER_TAG,
                severity="high",
                message=(
                    f"译文缺少占位符 ⟦{name}⟧（源文出现 {len(source_positions)} 次，"
                    f"译文仅 {len(target_positions)} 次）。"
                ),
                short_message="占位符丢失",
                rule_id=f"PLACEHOLDER_MISSING:{name}",
                offset=offset,
                length=1,
                context_text=_make_context(target_text, offset, 1),
                replacements=(f"⟦{name}⟧",),
                rule_category="tag",
                issue_type="format",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# 标点规则辅助
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PairedGroupStats:
    pairs: int
    unmatched_open_offset: int  # -1 表示无
    unmatched_close_offset: int  # -1 表示无


def _analyze_paired_group(
    text: str,
    opens: frozenset[str],
    closes: frozenset[str],
    ambiguous: frozenset[str],
) -> _PairedGroupStats:
    """扫描 text，统计该分组的完整成对数量与首个未匹配的位置。

    - opens/closes 中的字符：栈式匹配。
    - ambiguous 中的字符：按同字符奇偶数交替视作开始/结束。
    - 未匹配的 open 表示"缺少结束标记"；未匹配的 close 表示"缺少开始标记"。
    """
    stack: list[int] = []
    ambiguous_open: dict[str, int] = {}
    pairs = 0
    first_unmatched_close = -1
    for idx, ch in enumerate(text):
        if ch in ambiguous:
            slot = ambiguous_open.pop(ch, None)
            if slot is None:
                ambiguous_open[ch] = idx
            else:
                pairs += 1
            continue
        if ch in opens:
            stack.append(idx)
            continue
        if ch in closes:
            if stack:
                stack.pop()
                pairs += 1
            elif first_unmatched_close == -1:
                first_unmatched_close = idx
    if stack:
        first_unmatched_open = stack[0]
    elif ambiguous_open:
        first_unmatched_open = min(ambiguous_open.values())
    else:
        first_unmatched_open = -1
    return _PairedGroupStats(
        pairs=pairs,
        unmatched_open_offset=first_unmatched_open,
        unmatched_close_offset=first_unmatched_close,
    )


def _last_meaningful_char(text: str) -> tuple[int, str]:
    for i in range(len(text) - 1, -1, -1):
        ch = text[i]
        if not ch.isspace():
            return i, ch
    return -1, ""


# ---------------------------------------------------------------------------
# 标点规则（对应设置面板 8-12）
# ---------------------------------------------------------------------------


# 规则 8：成对标点符号丢失
def detect_paired_punctuation_missing(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
) -> list[CleanedLocalIssue]:
    """按"归一化分组"比较源文和译文的成对标点，半/全角、弯/直视作同族。

    比如原文用全角 `（VIN）`、译文用半角 `(VIN)` 不会误报，只有真正整对
    丢失或结构不平衡时才报告。
    """
    if not target_text:
        return []
    issues: list[CleanedLocalIssue] = []
    source = source_text or ""

    for display_open, display_close, opens, closes, ambiguous in _PAIRED_GROUPS:
        source_stats = _analyze_paired_group(source, opens, closes, ambiguous)
        target_stats = _analyze_paired_group(target_text, opens, closes, ambiguous)

        source_has_group = (
            source_stats.pairs > 0
            or source_stats.unmatched_open_offset >= 0
            or source_stats.unmatched_close_offset >= 0
        )
        if not source_has_group:
            continue

        # 译文缺少结束标记（有 open 没 close）
        if target_stats.unmatched_open_offset >= 0:
            idx = target_stats.unmatched_open_offset
            issues.append(
                CleanedLocalIssue(
                    rule_key=QA_RULE_PAIRED_PUNCTUATION_MISSING,
                    provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                    severity="medium",
                    message=f"存在 “{display_open}” 但缺少匹配的 “{display_close}”。",
                    short_message="成对标点缺失",
                    rule_id=f"PAIRED_MISSING_CLOSE:{display_open}{display_close}",
                    offset=idx,
                    length=1,
                    context_text=_make_context(target_text, idx, 1),
                    replacements=(f"{display_open}…{display_close}",),
                    rule_category="punctuation",
                    issue_type="style",
                )
            )
        # 译文缺少开始标记（有 close 没 open）
        if target_stats.unmatched_close_offset >= 0:
            idx = target_stats.unmatched_close_offset
            issues.append(
                CleanedLocalIssue(
                    rule_key=QA_RULE_PAIRED_PUNCTUATION_MISSING,
                    provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                    severity="medium",
                    message=f"存在 “{display_close}” 但缺少匹配的 “{display_open}”。",
                    short_message="成对标点缺失",
                    rule_id=f"PAIRED_MISSING_OPEN:{display_open}{display_close}",
                    offset=idx,
                    length=1,
                    context_text=_make_context(target_text, idx, 1),
                    replacements=(f"{display_open}…{display_close}",),
                    rule_category="punctuation",
                    issue_type="style",
                )
            )
        # 源文有完整成对但译文整对缺失
        if (
            source_stats.pairs > 0
            and target_stats.pairs < source_stats.pairs
            and target_stats.unmatched_open_offset < 0
            and target_stats.unmatched_close_offset < 0
        ):
            idx = max(0, len(target_text) - 1)
            issues.append(
                CleanedLocalIssue(
                    rule_key=QA_RULE_PAIRED_PUNCTUATION_MISSING,
                    provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                    severity="medium",
                    message=f"原文包含成对的 “{display_open}{display_close}”，译文缺失。",
                    short_message="成对标点缺失",
                    rule_id=f"PAIRED_MISSING_BOTH:{display_open}{display_close}",
                    offset=idx,
                    length=1,
                    context_text=_make_context(target_text, idx, 1),
                    replacements=(f"{display_open}…{display_close}",),
                    rule_category="punctuation",
                    issue_type="style",
                )
            )
    return issues


# 规则 9：原文和译文的结束标点不同
def detect_ending_punctuation_mismatch(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
) -> list[CleanedLocalIssue]:
    if not source_text or not target_text:
        return []
    source_idx, source_end = _last_meaningful_char(source_text)
    target_idx, target_end = _last_meaningful_char(target_text)
    if source_idx < 0 or target_idx < 0:
        return []
    source_key = ENDING_PUNCTUATION_EQUIVALENCE.get(source_end)
    target_key = ENDING_PUNCTUATION_EQUIVALENCE.get(target_end)
    if source_key is None:
        return []
    if target_key is None:
        return [
            CleanedLocalIssue(
                rule_key=QA_RULE_ENDING_PUNCTUATION_MISMATCH,
                provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                severity="low",
                message=f"原文以 “{source_end}” 结尾，译文缺少对应的结束标点。",
                short_message="结束标点不一致",
                rule_id=f"ENDING_MISSING:{source_key}",
                offset=target_idx,
                length=len(target_end),
                context_text=_make_context(target_text, target_idx, len(target_end)),
                replacements=(source_end,),
                rule_category="punctuation",
                issue_type="style",
            )
        ]
    if source_key != target_key:
        return [
            CleanedLocalIssue(
                rule_key=QA_RULE_ENDING_PUNCTUATION_MISMATCH,
                provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                severity="low",
                message=f"原文以 “{source_end}” 结尾，译文以 “{target_end}” 结尾。",
                short_message="结束标点不一致",
                rule_id=f"ENDING_DIFFERENT:{source_key}->{target_key}",
                offset=target_idx,
                length=len(target_end),
                context_text=_make_context(target_text, target_idx, len(target_end)),
                replacements=(source_end,),
                rule_category="punctuation",
                issue_type="style",
            )
        ]
    return []


# 规则 10：重复标点
def detect_repeated_punctuation(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
) -> list[CleanedLocalIssue]:
    if not target_text:
        return []
    issues: list[CleanedLocalIssue] = []
    n = len(target_text)
    i = 0
    while i < n:
        ch = target_text[i]
        if ch not in REPEATED_PUNCTUATION_CHARS:
            i += 1
            continue
        j = i + 1
        while j < n and target_text[j] == ch:
            j += 1
        run_len = j - i
        if run_len >= 2:
            # 允许三点省略号
            if ch == "." and run_len == 3:
                i = j
                continue
            issues.append(
                CleanedLocalIssue(
                    rule_key=QA_RULE_REPEATED_PUNCTUATION,
                    provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                    severity="low",
                    message=f"重复的标点 “{ch}” 连续出现 {run_len} 次。",
                    short_message="重复标点",
                    rule_id=f"REPEATED:{ch}",
                    offset=i,
                    length=run_len,
                    context_text=_make_context(target_text, i, run_len),
                    replacements=(ch,),
                    rule_category="punctuation",
                    issue_type="style",
                )
            )
        i = j
    return issues


# 规则 11：标点符号后有多余空格
def detect_extra_space_after_punctuation(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
) -> list[CleanedLocalIssue]:
    if not target_text:
        return []
    issues: list[CleanedLocalIssue] = []
    n = len(target_text)
    i = 0
    while i < n:
        ch = target_text[i]
        if ch in WESTERN_PUNCTUATION_FOR_SPACING:
            k = i + 1
            spaces = 0
            while k < n and target_text[k] == " ":
                spaces += 1
                k += 1
            if spaces >= 2:
                issues.append(
                    CleanedLocalIssue(
                        rule_key=QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION,
                        provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                        severity="low",
                        message=f"标点 “{ch}” 后出现多余空格。",
                        short_message="多余空格",
                        rule_id=f"EXTRA_SPACE:{ch}",
                        offset=i + 1,
                        length=spaces,
                        context_text=_make_context(target_text, i, 1 + spaces),
                        replacements=(" ",),
                        rule_category="punctuation",
                        issue_type="style",
                    )
                )
            i = k
            continue
        if ch in FULLWIDTH_PUNCTUATION_FOR_SPACING:
            if i + 1 < n and target_text[i + 1] == " ":
                k = i + 1
                spaces = 0
                while k < n and target_text[k] == " ":
                    spaces += 1
                    k += 1
                issues.append(
                    CleanedLocalIssue(
                        rule_key=QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION,
                        provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                        severity="low",
                        message=f"全角标点 “{ch}” 后不应出现空格。",
                        short_message="多余空格",
                        rule_id=f"EXTRA_SPACE_FULLWIDTH:{ch}",
                        offset=i + 1,
                        length=spaces,
                        context_text=_make_context(target_text, i, 1 + spaces),
                        replacements=("",),
                        rule_category="punctuation",
                        issue_type="style",
                    )
                )
                i = k
                continue
        i += 1
    return issues


# 规则 12：标点符号后遗漏空格
def detect_missing_space_after_punctuation(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
) -> list[CleanedLocalIssue]:
    if not target_text:
        return []
    issues: list[CleanedLocalIssue] = []
    n = len(target_text)
    for i, ch in enumerate(target_text):
        if ch not in WESTERN_PUNCTUATION_FOR_SPACING:
            continue
        if i == n - 1:
            continue
        next_char = target_text[i + 1]
        if next_char.isspace():
            continue
        if next_char in ")]}>”’」』》〉":
            continue
        if next_char in ".,;:!?":
            continue
        if ch in ".," and i > 0 and target_text[i - 1].isdigit() and next_char.isdigit():
            continue
        if _is_western_letter_or_digit(next_char):
            issues.append(
                CleanedLocalIssue(
                    rule_key=QA_RULE_MISSING_SPACE_AFTER_PUNCTUATION,
                    provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                    severity="low",
                    message=f"标点 “{ch}” 后遗漏空格。",
                    short_message="遗漏空格",
                    rule_id=f"MISSING_SPACE:{ch}",
                    offset=i,
                    length=1,
                    context_text=_make_context(target_text, i, 1),
                    replacements=(f"{ch} ",),
                    rule_category="punctuation",
                    issue_type="style",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# 规则注册表
# ---------------------------------------------------------------------------

LOCAL_QA_DETECTORS: dict[str, LocalQADetector] = {
    QA_RULE_TARGET_WITHOUT_TAG: detect_target_without_tag,
    QA_RULE_TARGET_TAG_MISSING: detect_target_tag_missing,
    QA_RULE_UNMATCHED_CLOSING_TAG: detect_unmatched_closing_tag,
    QA_RULE_UNMATCHED_OPENING_TAG: detect_unmatched_opening_tag,
    QA_RULE_TARGET_PLACEHOLDER_MISSING: detect_target_placeholder_missing,
    QA_RULE_PAIRED_PUNCTUATION_MISSING: detect_paired_punctuation_missing,
    QA_RULE_ENDING_PUNCTUATION_MISMATCH: detect_ending_punctuation_mismatch,
    QA_RULE_REPEATED_PUNCTUATION: detect_repeated_punctuation,
    QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION: detect_extra_space_after_punctuation,
    QA_RULE_MISSING_SPACE_AFTER_PUNCTUATION: detect_missing_space_after_punctuation,
}

_RULE_PROVIDER: dict[str, str] = {
    **{key: LOCAL_QA_PROVIDER_TAG for key in TAG_QA_RULE_KEYS},
    **{key: LOCAL_QA_PROVIDER_PUNCTUATION for key in PUNCTUATION_QA_RULE_KEYS},
}


def _provider_for_rule(rule_key: str) -> str:
    return _RULE_PROVIDER.get(rule_key, LOCAL_QA_PROVIDER_PUNCTUATION)


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------


def _existing_issue_fingerprint(issue: SegmentQAIssue) -> tuple[str, str, str, int, int, str]:
    return (
        issue.target_text_hash or "",
        issue.rule_key or "",
        issue.rule_id or "",
        int(issue.offset or 0),
        int(issue.length or 0),
        issue.message or "",
    )


def _apply_cleaned_issues_for_rule(
    db: Session,
    *,
    file_record: FileRecord,
    project: Project | None,
    segment: Segment,
    rule_key: str,
    text_hash: str,
    cleaned_issues: list[CleanedLocalIssue],
) -> bool:
    provider = _provider_for_rule(rule_key)
    existing_issues = (
        db.query(SegmentQAIssue)
        .filter(
            SegmentQAIssue.segment_id == segment.id,
            SegmentQAIssue.rule_key == rule_key,
            SegmentQAIssue.provider == provider,
        )
        .all()
    )
    existing_by_fingerprint = {
        _existing_issue_fingerprint(issue): issue for issue in existing_issues
    }
    next_fingerprints = {issue.fingerprint(text_hash) for issue in cleaned_issues}
    changed = False
    now = datetime.now()

    for existing in existing_issues:
        if (
            _existing_issue_fingerprint(existing) not in next_fingerprints
            and existing.status != QA_ISSUE_STATUS_RESOLVED
        ):
            existing.status = QA_ISSUE_STATUS_RESOLVED
            existing.updated_at = now
            changed = True

    for cleaned in cleaned_issues:
        fingerprint = cleaned.fingerprint(text_hash)
        existing = existing_by_fingerprint.get(fingerprint)
        replacements_json = json.dumps(list(cleaned.replacements), ensure_ascii=False)
        if existing is None:
            db.add(
                SegmentQAIssue(
                    project_id=getattr(project, "id", None),
                    file_record_id=file_record.id,
                    segment_id=segment.id,
                    sentence_id=segment.sentence_id,
                    rule_key=cleaned.rule_key,
                    provider=cleaned.provider,
                    language="",
                    severity=cleaned.severity,
                    message=cleaned.message,
                    short_message=cleaned.short_message,
                    rule_id=cleaned.rule_id[:120],
                    rule_category=cleaned.rule_category,
                    issue_type=cleaned.issue_type,
                    context_text=cleaned.context_text,
                    offset=cleaned.offset,
                    length=cleaned.length,
                    replacements=replacements_json,
                    target_text_hash=text_hash,
                    status=QA_ISSUE_STATUS_OPEN,
                )
            )
            changed = True
            continue

        if existing.status == QA_ISSUE_STATUS_RESOLVED:
            existing.status = QA_ISSUE_STATUS_OPEN
            changed = True
        existing.severity = cleaned.severity
        existing.short_message = cleaned.short_message
        existing.context_text = cleaned.context_text
        existing.replacements = replacements_json
        existing.rule_category = cleaned.rule_category
        existing.issue_type = cleaned.issue_type
        existing.updated_at = now

    if changed:
        segment.updated_at = now
    return changed


def _resolve_enabled_local_rules(project: Project | None) -> set[str]:
    settings = load_quality_qa_settings(project)
    rules = settings.get("rules") or {}
    enabled: set[str] = set()
    for key in LOCAL_QA_RULE_KEYS:
        rule = rules.get(key)
        if isinstance(rule, dict) and bool(rule.get("enabled")):
            enabled.add(key)
    return enabled


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------


def check_segments_local_qa(
    db: Session,
    *,
    file_record: FileRecord,
    segments: list[Segment],
    rule_keys: Iterable[str] | None = None,
) -> int:
    """针对给定句段运行所有本地 QA 规则。

    - ``rule_keys=None`` 时按项目设置解析启用集合。
    - 未启用的规则历史遗留问题会被自动标记为 ``resolved``。
    - 返回本次实际发生更新的句段数量。
    """
    project = file_record.project or (
        db.query(Project).filter(Project.id == file_record.project_id).first()
        if file_record.project_id
        else None
    )
    enabled = set(rule_keys) if rule_keys is not None else _resolve_enabled_local_rules(project)
    enabled &= set(LOCAL_QA_RULE_KEYS)
    if not segments:
        return 0

    changed_count = 0
    for segment in segments:
        source_text = segment.source_text or ""
        target_text = segment.target_text or ""
        source_html = segment.source_html or ""
        target_html = segment.target_html or ""
        text_hash = target_text_hash(target_text)
        for rule_key in LOCAL_QA_RULE_KEYS:
            if rule_key not in enabled:
                if _apply_cleaned_issues_for_rule(
                    db,
                    file_record=file_record,
                    project=project,
                    segment=segment,
                    rule_key=rule_key,
                    text_hash=text_hash,
                    cleaned_issues=[],
                ):
                    changed_count += 1
                continue
            detector = LOCAL_QA_DETECTORS[rule_key]
            try:
                cleaned = detector(source_text, target_text, source_html, target_html)
            except Exception:
                logger.exception(
                    "Local QA detector failed rule=%s segment_id=%s",
                    rule_key,
                    segment.id,
                )
                continue
            if _apply_cleaned_issues_for_rule(
                db,
                file_record=file_record,
                project=project,
                segment=segment,
                rule_key=rule_key,
                text_hash=text_hash,
                cleaned_issues=cleaned,
            ):
                changed_count += 1

    if changed_count:
        db.commit()
    return changed_count


def run_local_qa_for_segment_ids(file_record_id: UUID, segment_ids: list[UUID]) -> int:
    if not segment_ids:
        return 0
    with SessionLocal() as db:
        file_record = db.query(FileRecord).filter(FileRecord.id == file_record_id).first()
        if not file_record:
            return 0
        segments = (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record_id,
                Segment.id.in_(list(dict.fromkeys(segment_ids))),
            )
            .all()
        )
        return check_segments_local_qa(db, file_record=file_record, segments=segments)


def run_local_qa_for_project(project_id: UUID) -> int:
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return 0
        enabled = _resolve_enabled_local_rules(project)
        if not enabled:
            return 0
        files = db.query(FileRecord).filter(FileRecord.project_id == project_id).all()
        changed_count = 0
        for file_record in files:
            segments = (
                db.query(Segment)
                .filter(Segment.file_record_id == file_record.id)
                .all()
            )
            changed_count += check_segments_local_qa(
                db,
                file_record=file_record,
                segments=segments,
                rule_keys=enabled,
            )
        return changed_count
