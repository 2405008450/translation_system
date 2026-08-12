"""本地质量保证规则集合（无外部依赖，与 LanguageTool 拼写语法 QA 并列使用）。

覆盖项目 QA 规则中的以下条目（编号与项目设置面板对应，第 6/7 条
分别为 LanguageTool 拼写/语法与术语不一致，由其他模块处理）：

标记与占位符（对比 source_html / target_html / source_text / target_text）
- 1. ``target_without_tag`` 源文包含格式标记但译文完全没有格式标记。
- 2. ``target_tag_missing`` 源文的某类格式标记数量多于译文（如 <b> 被漏掉）。
- 3. ``unmatched_closing_tag`` 译文里出现未匹配的结束标记（``</b>``）。
- 4. ``unmatched_opening_tag`` 译文里出现未闭合的开始标记（``<b>``）。
- 5. ``target_placeholder_missing`` ``⟦MATH_n⟧`` / ``⟦LB_n⟧`` 等占位符在译文中丢失。

标点（仅对比源文与译文文本）
- 8.  ``paired_punctuation_missing`` 成对标点符号丢失。
- 9.  ``ending_punctuation_mismatch`` 原文和译文的结束标点不同。
- 10. ``repeated_punctuation`` 重复标点。
- 11. ``extra_space_after_punctuation`` 标点符号后有多余空格。
- 12. ``missing_space_after_punctuation`` 标点符号后遗漏空格。
- 13. ``punctuation_leading_extra_space`` 半角标点前有多余空格。
- 14. ``punctuation_leading_missing_space`` 法语等语言 ``！？：；`` 前应有空格却缺失。
- 15. ``multiple_spaces`` 句中连续 2 个及以上空格。
- 16. ``segment_trailing_extra_space`` 句段末尾多余空格。
- 28. ``consecutive_duplicate_words`` 连续重复单词（如 ``the the``），仅对非 CJK 目标语言启用。

大小写与整段（拉丁字母目标语言相关，仅在源/目标均非 CJK 时启用）：
- 17. ``source_target_initial_case_mismatch`` 原文首字母大写而译文小写（或反之）。
- 18. ``target_word_multiple_upper_initials`` 译文单词中出现异常驼峰（``HelloWorld``、``iPhone``）。
- 19. ``source_target_same_word_case_mismatch`` 原文和译文中同一个单词的首字母大小写不一致。
- 29. ``source_target_identical`` 原文与译文完全一致（且源/目标语言不同）。

字数长度（对比源/译字数比例，需要 threshold 百分比参数，默认 50）：
- 20. ``target_word_count_exceeds_source`` 译文字数超过原文字数的 X%。
- 21. ``target_word_count_below_source`` 译文字数少于原文字数的 X%。
- 22. ``source_target_word_count_gap_too_large`` 译文与原文字数任一方向相差过大（综合版）。

内容一致性（从源/译分别抽取 token，做 multiset 比对）：
- 24. ``number_mismatch`` 原文和译文数字不一致。
- 25. ``parameter_mismatch`` 原文与译文占位参数（``{name}``、``{count}``、``%s``、``%(x)s`` 等）不一致。
- 26. ``email_mismatch`` 原文与译文邮件地址不一致。
- 27. ``link_mismatch`` 原文和译文链接（http/https）不一致。
- 30. ``special_symbol_mismatch`` 特殊符号（® ™ © 货币符号等）不一致。

跨句段上下文一致性（需要项目级预扫描）：
- 23. ``context_translation_mismatch`` 同一原文在项目内被译成不同译文（跨句段扫描）。

设计原则：
- 所有 detector 采用统一签名 ``(source_text, target_text, source_html, target_html,
  target_language, source_language, rule_settings)``，用不上参数的规则可忽略。
- 需要项目侧参数（例如阈值）的规则从 ``rule_settings`` 里读取。
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
from typing import Any, Callable, Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import FileRecord, Project, Segment, SegmentQAIssue
from app.services.spelling_grammar_qa import (
    QA_ISSUE_STATUS_OPEN,
    QA_ISSUE_STATUS_RESOLVED,
    QA_RULE_CONSECUTIVE_DUPLICATE_WORDS,
    QA_RULE_CONTEXT_TRANSLATION_MISMATCH,
    QA_RULE_ENDING_PUNCTUATION_MISMATCH,
    QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION,
    QA_RULE_EMAIL_MISMATCH,
    QA_RULE_LINK_MISMATCH,
    QA_RULE_MISSING_SPACE_AFTER_PUNCTUATION,
    QA_RULE_MULTIPLE_SPACES,
    QA_RULE_NUMBER_MISMATCH,
    QA_RULE_PAIRED_PUNCTUATION_MISSING,
    QA_RULE_PARAMETER_MISMATCH,
    QA_RULE_PUNCTUATION_LEADING_EXTRA_SPACE,
    QA_RULE_PUNCTUATION_LEADING_MISSING_SPACE,
    QA_RULE_REPEATED_PUNCTUATION,
    QA_RULE_SPECIAL_SYMBOL_MISMATCH,
    QA_RULE_SEGMENT_TRAILING_EXTRA_SPACE,
    QA_RULE_SOURCE_TARGET_IDENTICAL,
    QA_RULE_SOURCE_TARGET_INITIAL_CASE_MISMATCH,
    QA_RULE_SOURCE_TARGET_SAME_WORD_CASE_MISMATCH,
    QA_RULE_SOURCE_TARGET_WORD_COUNT_GAP_TOO_LARGE,
    QA_RULE_TARGET_PLACEHOLDER_MISSING,
    QA_RULE_TARGET_TAG_MISSING,
    QA_RULE_TARGET_WITHOUT_TAG,
    QA_RULE_TARGET_WORD_COUNT_BELOW_SOURCE,
    QA_RULE_TARGET_WORD_COUNT_EXCEEDS_SOURCE,
    QA_RULE_TARGET_WORD_MULTIPLE_UPPER_INITIALS,
    QA_RULE_UNMATCHED_CLOSING_TAG,
    QA_RULE_UNMATCHED_OPENING_TAG,
    QUALITY_QA_RULE_THRESHOLD_DEFAULTS,
    load_quality_qa_settings,
    target_text_hash,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量与规则分组
# ---------------------------------------------------------------------------

LOCAL_QA_PROVIDER_TAG = "tag"
LOCAL_QA_PROVIDER_PUNCTUATION = "punctuation"
LOCAL_QA_PROVIDER_CASE = "case"
LOCAL_QA_PROVIDER_LENGTH = "length"
LOCAL_QA_PROVIDER_CONTENT = "content"
LOCAL_QA_PROVIDER_CONTEXT = "context"

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
    QA_RULE_PUNCTUATION_LEADING_EXTRA_SPACE,
    QA_RULE_PUNCTUATION_LEADING_MISSING_SPACE,
    QA_RULE_MULTIPLE_SPACES,
    QA_RULE_SEGMENT_TRAILING_EXTRA_SPACE,
    QA_RULE_CONSECUTIVE_DUPLICATE_WORDS,
)

CASE_QA_RULE_KEYS: tuple[str, ...] = (
    QA_RULE_SOURCE_TARGET_INITIAL_CASE_MISMATCH,
    QA_RULE_TARGET_WORD_MULTIPLE_UPPER_INITIALS,
    QA_RULE_SOURCE_TARGET_SAME_WORD_CASE_MISMATCH,
    QA_RULE_SOURCE_TARGET_IDENTICAL,
)

LENGTH_QA_RULE_KEYS: tuple[str, ...] = (
    QA_RULE_TARGET_WORD_COUNT_EXCEEDS_SOURCE,
    QA_RULE_TARGET_WORD_COUNT_BELOW_SOURCE,
    QA_RULE_SOURCE_TARGET_WORD_COUNT_GAP_TOO_LARGE,
)

CONTENT_QA_RULE_KEYS: tuple[str, ...] = (
    QA_RULE_NUMBER_MISMATCH,
    QA_RULE_PARAMETER_MISMATCH,
    QA_RULE_EMAIL_MISMATCH,
    QA_RULE_LINK_MISMATCH,
    QA_RULE_SPECIAL_SYMBOL_MISMATCH,
)

CONTEXT_QA_RULE_KEYS: tuple[str, ...] = (
    QA_RULE_CONTEXT_TRANSLATION_MISMATCH,
)

LOCAL_QA_RULE_KEYS: tuple[str, ...] = (
    TAG_QA_RULE_KEYS
    + PUNCTUATION_QA_RULE_KEYS
    + CASE_QA_RULE_KEYS
    + LENGTH_QA_RULE_KEYS
    + CONTENT_QA_RULE_KEYS
    + CONTEXT_QA_RULE_KEYS
)


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


LocalQADetector = Callable[..., list[CleanedLocalIssue]]


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
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
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
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
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
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
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
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
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
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
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
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
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
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
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
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
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
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
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
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    if not target_text:
        return []

    # 邮箱和 URL 内的点号属于 token 本身，不能当作遗漏空格处理。
    # 这些正则定义在内容一致性规则区；detector 实际执行时模块已完成初始化。
    protected_ranges = tuple(
        match.span()
        for pattern in (_EMAIL_TOKEN_RE, _URL_TOKEN_RE)
        for match in pattern.finditer(target_text)
    )
    issues: list[CleanedLocalIssue] = []
    n = len(target_text)
    for i, ch in enumerate(target_text):
        if ch not in WESTERN_PUNCTUATION_FOR_SPACING:
            continue
        if any(start <= i < end for start, end in protected_ranges):
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
# 空格与重复词规则辅助（对应设置面板 13-16、28）
# ---------------------------------------------------------------------------


# 法语类语言：`!?;:` 前应使用（不间断）空格。
_FRENCH_LIKE_LANG_PREFIXES: tuple[str, ...] = ("fr",)
# 常见 CJK 语言前缀，用于跳过基于"单词分隔"的规则（如连续重复单词）。
_CJK_LANG_PREFIXES: tuple[str, ...] = ("zh", "ja", "ko", "yue", "wuu")
# 半角标点：前面出现空格属于英文/多数西欧语言里的错误。
_WESTERN_LEADING_PUNCTUATION: frozenset[str] = frozenset(",.;:!?)]}")
# 法语要求前置空格的半/全角标点集合。
_FRENCH_LEADING_SPACED_PUNCTUATION: frozenset[str] = frozenset("!?;:！？；：")
# 视作"空格"的字符（含常规空格、不间断空格、窄不间断空格、制表符）。
_SPACE_LIKE_CHARS: frozenset[str] = frozenset(" \t\u00A0\u202F")


def _normalize_language_code(code: str | None) -> str:
    return (code or "").strip().lower().replace("_", "-")


def _language_has_prefix(code: str | None, prefixes: tuple[str, ...]) -> bool:
    normalized = _normalize_language_code(code)
    if not normalized:
        return False
    for prefix in prefixes:
        if normalized == prefix or normalized.startswith(f"{prefix}-"):
            return True
    return False


def _is_french_like_language(code: str | None) -> bool:
    return _language_has_prefix(code, _FRENCH_LIKE_LANG_PREFIXES)


def _is_cjk_language(code: str | None) -> bool:
    return _language_has_prefix(code, _CJK_LANG_PREFIXES)


# 规则 13：标点符号前有多余空格
def detect_punctuation_leading_extra_space(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """半角标点（如 ``,`` ``.`` ``;`` ``:`` ``!`` ``?``）前不应出现空格。

    法语等语言允许在 ``!?;:`` 前加空格，此时该情形由规则 14 处理，本规则跳过。
    URL/数字内部的 ``.`` 前不会误报（因为其前也不会有空格）。
    """
    if not target_text:
        return []
    french_like = _is_french_like_language(target_language)
    issues: list[CleanedLocalIssue] = []
    n = len(target_text)
    i = 1
    while i < n:
        ch = target_text[i]
        if ch not in _WESTERN_LEADING_PUNCTUATION:
            i += 1
            continue
        # 法语类语言的 !?;: 前允许空格
        if french_like and ch in _FRENCH_LEADING_SPACED_PUNCTUATION:
            i += 1
            continue
        # 向前收集所有空格
        start = i
        while start > 0 and target_text[start - 1] in _SPACE_LIKE_CHARS:
            start -= 1
        if start == i:
            i += 1
            continue
        space_length = i - start
        # 句首/仅由空格构成的前缀不算异常（例如首字符前的空格由规则 15/16 处理）
        if start == 0:
            i += 1
            continue
        issues.append(
            CleanedLocalIssue(
                rule_key=QA_RULE_PUNCTUATION_LEADING_EXTRA_SPACE,
                provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                severity="low",
                message=f"标点 “{ch}” 前不应出现空格。",
                short_message="标点前多余空格",
                rule_id=f"LEADING_EXTRA_SPACE:{ch}",
                offset=start,
                length=space_length,
                context_text=_make_context(target_text, start, space_length + 1),
                replacements=("",),
                rule_category="punctuation",
                issue_type="style",
            )
        )
        i += 1
    return issues


# 规则 14：标点符号前遗漏空格（法语等语言）
def detect_punctuation_leading_missing_space(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """法语等语言要求 ``!`` ``?`` ``;`` ``:`` 前使用（不间断）空格。

    非法语目标语言不启用本规则；已存在任意空格（普通空格、``NBSP`` 等）视为合规。
    """
    if not target_text:
        return []
    if not _is_french_like_language(target_language):
        return []
    issues: list[CleanedLocalIssue] = []
    for i, ch in enumerate(target_text):
        if ch not in _FRENCH_LEADING_SPACED_PUNCTUATION:
            continue
        if i == 0:
            continue
        prev = target_text[i - 1]
        if prev in _SPACE_LIKE_CHARS:
            continue
        # 前一个字符若也是标点/关闭括号，则不算漏空格（多标点串通常由规则 10/11 覆盖）
        if prev in ")]}»›”’":
            continue
        issues.append(
            CleanedLocalIssue(
                rule_key=QA_RULE_PUNCTUATION_LEADING_MISSING_SPACE,
                provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                severity="low",
                message=f"标点 “{ch}” 前需要空格（法语等语言）。",
                short_message="标点前缺空格",
                rule_id=f"LEADING_MISSING_SPACE:{ch}",
                offset=i,
                length=1,
                context_text=_make_context(target_text, i, 1),
                replacements=(f" {ch}",),
                rule_category="punctuation",
                issue_type="style",
            )
        )
    return issues


# 规则 15：多个空格
_MULTIPLE_SPACES_RE = re.compile(r"  +")


def detect_multiple_spaces(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """句中连续 2 个及以上普通空格。

    - 只关心 ASCII 空格 ``0x20``，制表符/换行不算。
    - 若相同位置的空格紧跟在半角标点后（例如 ``, ``），可能被规则 11 触发；此处
      仍会报告，两条规则会各自记录一条问题，方便用户按需勾选。
    """
    if not target_text:
        return []
    issues: list[CleanedLocalIssue] = []
    for match in _MULTIPLE_SPACES_RE.finditer(target_text):
        start = match.start()
        length = match.end() - start
        issues.append(
            CleanedLocalIssue(
                rule_key=QA_RULE_MULTIPLE_SPACES,
                provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                severity="low",
                message=f"句中出现 {length} 个连续空格。",
                short_message="多个空格",
                rule_id="MULTIPLE_SPACES",
                offset=start,
                length=length,
                context_text=_make_context(target_text, start, length),
                replacements=(" ",),
                rule_category="punctuation",
                issue_type="style",
            )
        )
    return issues


# 规则 16：句段末尾多余空格
def detect_segment_trailing_extra_space(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """句段末尾包含 1 个或多个空白字符（含 tab、NBSP）。"""
    if not target_text:
        return []
    stripped = target_text.rstrip(" \t\u00A0\u202F")
    if stripped == target_text:
        return []
    trailing_len = len(target_text) - len(stripped)
    offset = len(stripped)
    return [
        CleanedLocalIssue(
            rule_key=QA_RULE_SEGMENT_TRAILING_EXTRA_SPACE,
            provider=LOCAL_QA_PROVIDER_PUNCTUATION,
            severity="low",
            message=f"句段末尾有 {trailing_len} 个多余空格。",
            short_message="末尾多余空格",
            rule_id="TRAILING_SPACE",
            offset=offset,
            length=trailing_len,
            context_text=_make_context(target_text, offset, trailing_len),
            replacements=("",),
            rule_category="punctuation",
            issue_type="style",
        )
    ]


# 规则 28：连续重复单词
# 用空白边界匹配任意被空格分隔的相同 token，兼容英语 "the the" 与中文 "很 很" 等。
_DUPLICATE_WORD_RE = re.compile(
    r"(?<!\S)(\S+)([ \t\u00A0]+)\1(?!\S)",
    re.UNICODE | re.IGNORECASE,
)
# 至少含一个字母或 CJK 字符（跳过纯数字、纯符号的相邻重复，如 "1 1"、"* *"）。
_DUPLICATE_WORD_MEANINGFUL_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def detect_consecutive_duplicate_words(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """检测被空格分隔的连续重复单词（``the the cat`` / ``很 很 好`` 等）。

    - 匹配任何被空白分隔且完全相同的 token，兼容拉丁字母词与 CJK 单字。
    - token 至少要包含一个字母或 CJK 字符（避免 ``1 1`` / ``* *`` 之类误报）。
    - 若原文本身就是相邻重复的同一个词（例如英文原文 ``very very``），则跳过。
    """
    if not target_text:
        return []
    source_lower = (source_text or "").lower()
    issues: list[CleanedLocalIssue] = []
    for match in _DUPLICATE_WORD_RE.finditer(target_text):
        word = match.group(1)
        if not _DUPLICATE_WORD_MEANINGFUL_RE.search(word):
            continue
        span_text = match.group(0)
        # 原文也存在同样的连续重复，视为设计意图，跳过。
        if span_text.lower() in source_lower:
            continue
        offset = match.start()
        length = match.end() - offset
        issues.append(
            CleanedLocalIssue(
                rule_key=QA_RULE_CONSECUTIVE_DUPLICATE_WORDS,
                provider=LOCAL_QA_PROVIDER_PUNCTUATION,
                severity="medium",
                message=f"连续重复单词 “{word}”。",
                short_message="连续重复单词",
                rule_id=f"DUPLICATE_WORD:{word.lower()}",
                offset=offset,
                length=length,
                context_text=_make_context(target_text, offset, length),
                replacements=(word,),
                rule_category="punctuation",
                issue_type="style",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# 大小写与整段规则辅助（对应设置面板 17-19、29）
# ---------------------------------------------------------------------------


_LATIN_LETTER_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]{2,}")
_ANY_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def _has_latin_case_support(code: str | None) -> bool:
    """判断目标/源语言是否具备"大小写"概念（用于决定是否启用大小写类规则）。"""
    if _is_cjk_language(code):
        return False
    normalized = _normalize_language_code(code)
    if not normalized:
        # 未知语言默认不启用，避免误报
        return False
    return True


def _first_latin_letter(text: str) -> tuple[int, str] | None:
    match = _LATIN_LETTER_RE.search(text or "")
    if not match:
        return None
    return match.start(), match.group(0)


# 规则 17：原文和译文首字母大小写不一致
def detect_source_target_initial_case_mismatch(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """比较源文与译文第一个拉丁字母的大小写。

    仅在双方均具备"大小写"概念时启用（跳过 CJK 源或目标语言）。
    """
    if not source_text or not target_text:
        return []
    if not _has_latin_case_support(target_language):
        return []
    if not _has_latin_case_support(source_language):
        return []
    s_hit = _first_latin_letter(source_text)
    t_hit = _first_latin_letter(target_text)
    if s_hit is None or t_hit is None:
        return []
    _s_idx, s_ch = s_hit
    t_idx, t_ch = t_hit
    if s_ch.isupper() == t_ch.isupper():
        return []
    expected = s_ch.upper() if s_ch.isupper() else s_ch.lower()
    return [
        CleanedLocalIssue(
            rule_key=QA_RULE_SOURCE_TARGET_INITIAL_CASE_MISMATCH,
            provider=LOCAL_QA_PROVIDER_CASE,
            severity="low",
            message=(
                f"原文首字母 “{s_ch}”，译文首字母 “{t_ch}”，大小写不一致。"
            ),
            short_message="首字母大小写不一致",
            rule_id="INITIAL_CASE_MISMATCH",
            offset=t_idx,
            length=1,
            context_text=_make_context(target_text, t_idx, 1),
            replacements=(expected,),
            rule_category="case",
            issue_type="style",
        )
    ]


# 规则 18：译文一个单词中有多个大写首字母
def detect_target_word_multiple_upper_initials(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """检测译文里的异常驼峰单词（``HelloWorld``、``iPhone``）。

    - 目标语言需具备大小写概念，否则跳过。
    - 全大写缩写（``URL``、``API``）不算异常。
    - 源文中若已按同样拼写出现该单词（产品名/术语），跳过。
    """
    if not target_text:
        return []
    if not _has_latin_case_support(target_language):
        return []
    source_words = {m.group(0) for m in _LATIN_WORD_RE.finditer(source_text or "")}
    issues: list[CleanedLocalIssue] = []
    seen: set[str] = set()
    for match in _LATIN_WORD_RE.finditer(target_text):
        word = match.group(0)
        if word in source_words:
            continue
        if word.isupper() or word.islower():
            continue
        uppers_after_start = sum(1 for ch in word[1:] if ch.isupper())
        starts_lower_with_upper = word[0].islower() and any(ch.isupper() for ch in word[1:])
        if uppers_after_start == 0 and not starts_lower_with_upper:
            continue
        if word in seen:
            continue
        seen.add(word)
        offset = match.start()
        length = match.end() - offset
        issues.append(
            CleanedLocalIssue(
                rule_key=QA_RULE_TARGET_WORD_MULTIPLE_UPPER_INITIALS,
                provider=LOCAL_QA_PROVIDER_CASE,
                severity="low",
                message=f"单词 “{word}” 内含多个大写首字母。",
                short_message="异常驼峰",
                rule_id=f"MULTI_UPPER_INITIAL:{word}",
                offset=offset,
                length=length,
                context_text=_make_context(target_text, offset, length),
                replacements=(word.capitalize(),),
                rule_category="case",
                issue_type="style",
            )
        )
    return issues


# 规则 19：原文和译文的同一单词首字母有不同的大小写
def detect_source_target_same_word_case_mismatch(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """同一拉丁字母单词在原文与译文中的首字母大小写不一致时报告。

    - 双方都需具备大小写概念。
    - 若同一单词在源文里既有大写又有小写（例如首句大写、句中小写），不报。
    - 全大写缩写与全小写词跳过，只关注"首字母"的大小写切换。
    """
    if not source_text or not target_text:
        return []
    if not (_has_latin_case_support(target_language) and _has_latin_case_support(source_language)):
        return []
    source_variants: dict[str, set[bool]] = {}
    for match in _LATIN_WORD_RE.finditer(source_text):
        word = match.group(0)
        # 跳过全大写缩写（URL、API），不作为大小写对齐基准
        if word.isupper():
            continue
        source_variants.setdefault(word.lower(), set()).add(word[0].isupper())
    if not source_variants:
        return []
    issues: list[CleanedLocalIssue] = []
    reported_words: set[str] = set()
    for match in _LATIN_WORD_RE.finditer(target_text):
        word = match.group(0)
        if word.isupper():
            continue
        key = word.lower()
        variants = source_variants.get(key)
        if not variants:
            continue
        # 源文里同时存在大小写两种，视为设计意图。
        if len(variants) > 1:
            continue
        source_is_upper = next(iter(variants))
        target_is_upper = word[0].isupper()
        if source_is_upper == target_is_upper:
            continue
        if key in reported_words:
            continue
        reported_words.add(key)
        offset = match.start()
        length = match.end() - offset
        expected = word.capitalize() if source_is_upper else word[0].lower() + word[1:]
        issues.append(
            CleanedLocalIssue(
                rule_key=QA_RULE_SOURCE_TARGET_SAME_WORD_CASE_MISMATCH,
                provider=LOCAL_QA_PROVIDER_CASE,
                severity="low",
                message=(
                    f"单词 “{word}” 的首字母大小写与原文不一致。"
                ),
                short_message="同词首字母大小写不一致",
                rule_id=f"SAME_WORD_CASE:{key}",
                offset=offset,
                length=length,
                context_text=_make_context(target_text, offset, length),
                replacements=(expected,),
                rule_category="case",
                issue_type="style",
            )
        )
    return issues


# 规则 29：原文与译文相同
def detect_source_target_identical(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """整段照抄没翻。

    - 源语言与目标语言相同（例如同语言校对场景）时不报。
    - 译文里若不含任何字母（纯数字、纯符号、纯 URL 等）不报。
    """
    if not source_text or not target_text:
        return []
    src_lang = _normalize_language_code(source_language)
    tgt_lang = _normalize_language_code(target_language)
    if src_lang and tgt_lang:
        # 仅比较主语言代码 (fr / fr-FR 视为一致)
        if src_lang.split("-", 1)[0] == tgt_lang.split("-", 1)[0]:
            return []
    stripped_source = source_text.strip()
    stripped_target = target_text.strip()
    if not stripped_source or not stripped_target:
        return []
    if stripped_source != stripped_target:
        return []
    if not _ANY_LETTER_RE.search(stripped_target):
        return []
    offset = len(target_text) - len(target_text.lstrip())
    length = max(1, len(stripped_target))
    return [
        CleanedLocalIssue(
            rule_key=QA_RULE_SOURCE_TARGET_IDENTICAL,
            provider=LOCAL_QA_PROVIDER_CASE,
            severity="medium",
            message="译文与原文完全一致，可能整段未翻译。",
            short_message="原文与译文相同",
            rule_id="SOURCE_TARGET_IDENTICAL",
            offset=offset,
            length=length,
            context_text=_make_context(target_text, offset, length),
            replacements=(),
            rule_category="case",
            issue_type="style",
        )
    ]


# ---------------------------------------------------------------------------
# 字数长度规则辅助（对应设置面板 20-22）
# ---------------------------------------------------------------------------


# CJK 单元：全部 Han + 平假 + 片假 + 谚文范围。
_CJK_CHAR_RE = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\uAC00-\uD7AF\uF900-\uFAFF]"
)
_WHITESPACE_RUN_RE = re.compile(r"\S+")


def _count_language_units(text: str, language: str | None) -> int:
    """按语言类型统计"字数"：CJK 按非空白字符数，其余按空白分词的词数。"""
    if not text:
        return 0
    if _is_cjk_language(language):
        # 优先按 CJK 字符数（更符合"中文字数"感官），若不含 CJK 则退回非空白字符数。
        cjk = len(_CJK_CHAR_RE.findall(text))
        if cjk > 0:
            return cjk
        return sum(1 for ch in text if not ch.isspace())
    return len(_WHITESPACE_RUN_RE.findall(text))


def _resolve_threshold(rule_key: str, rule_settings: dict[str, Any] | None) -> int:
    default_value = int(QUALITY_QA_RULE_THRESHOLD_DEFAULTS.get(rule_key, 50))
    if not isinstance(rule_settings, dict):
        return default_value
    raw = rule_settings.get("threshold")
    if raw is None:
        return default_value
    try:
        value = int(round(float(raw)))
    except (TypeError, ValueError):
        return default_value
    if value < 1:
        return 1
    if value > 500:
        return 500
    return value


def _build_length_issue(
    rule_key: str,
    message: str,
    short_message: str,
    rule_id: str,
    target_text: str,
) -> CleanedLocalIssue:
    length = max(1, len(target_text))
    return CleanedLocalIssue(
        rule_key=rule_key,
        provider=LOCAL_QA_PROVIDER_LENGTH,
        severity="medium",
        message=message,
        short_message=short_message,
        rule_id=rule_id,
        offset=0,
        length=length,
        context_text=_make_context(target_text, 0, min(length, 60)),
        replacements=(),
        rule_category="length",
        issue_type="style",
    )


# 规则 20：译文字数超过原文字数的 X%
def detect_target_word_count_exceeds_source(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """译文字数比原文多出 ``threshold%``（默认 50%）时报告。"""
    source_count = _count_language_units(source_text, source_language)
    target_count = _count_language_units(target_text, target_language)
    if source_count <= 0 or target_count <= 0:
        return []
    threshold = _resolve_threshold(QA_RULE_TARGET_WORD_COUNT_EXCEEDS_SOURCE, rule_settings)
    limit = source_count * (100 + threshold) / 100.0
    if target_count <= limit:
        return []
    return [
        _build_length_issue(
            rule_key=QA_RULE_TARGET_WORD_COUNT_EXCEEDS_SOURCE,
            message=(
                f"译文字数 {target_count} 超过原文 {source_count} 的 {threshold}%（限 {int(limit)}）。"
            ),
            short_message="译文过长",
            rule_id=f"LENGTH_EXCEEDS:{threshold}",
            target_text=target_text,
        )
    ]


# 规则 21：译文字数少于原文字数的 X%
def detect_target_word_count_below_source(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """译文字数比原文少 ``threshold%``（默认 50%）时报告。"""
    source_count = _count_language_units(source_text, source_language)
    target_count = _count_language_units(target_text, target_language)
    if source_count <= 0 or target_count <= 0:
        return []
    threshold = _resolve_threshold(QA_RULE_TARGET_WORD_COUNT_BELOW_SOURCE, rule_settings)
    threshold = min(threshold, 100)  # 少于 100% 才有意义
    limit = source_count * (100 - threshold) / 100.0
    if target_count >= limit:
        return []
    return [
        _build_length_issue(
            rule_key=QA_RULE_TARGET_WORD_COUNT_BELOW_SOURCE,
            message=(
                f"译文字数 {target_count} 少于原文 {source_count} 的 {threshold}%（需 ≥ {int(limit)}）。"
            ),
            short_message="译文过短",
            rule_id=f"LENGTH_BELOW:{threshold}",
            target_text=target_text,
        )
    ]


# 规则 22：译文与原文字数相差过大（综合上下限）
def detect_source_target_word_count_gap_too_large(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """译文与原文字数任一方向的偏差超过 ``threshold%`` 时报告。"""
    source_count = _count_language_units(source_text, source_language)
    target_count = _count_language_units(target_text, target_language)
    if source_count <= 0 or target_count <= 0:
        return []
    threshold = _resolve_threshold(QA_RULE_SOURCE_TARGET_WORD_COUNT_GAP_TOO_LARGE, rule_settings)
    diff = abs(target_count - source_count) * 100.0 / source_count
    if diff <= threshold:
        return []
    direction = "过长" if target_count > source_count else "过短"
    return [
        _build_length_issue(
            rule_key=QA_RULE_SOURCE_TARGET_WORD_COUNT_GAP_TOO_LARGE,
            message=(
                f"译文字数 {target_count} 与原文 {source_count} 相差 {diff:.0f}%，"
                f"超过阈值 {threshold}%（{direction}）。"
            ),
            short_message="译文与原文字数相差过大",
            rule_id=f"LENGTH_GAP:{threshold}",
            target_text=target_text,
        )
    ]


# ---------------------------------------------------------------------------
# 内容一致性规则辅助（对应设置面板 24-27、30）
# ---------------------------------------------------------------------------


# 数字：整数/小数/千分位（例如 "2.5"、"2,000.5"、"2024"）。
_NUMBER_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)+|\d+")
# 参数占位符：``{name}`` / ``{count}`` / ``{user.id}`` / ``{0}`` 等。
_PLACEHOLDER_BRACE_RE = re.compile(r"\{[A-Za-z0-9_][\w.\-]*\}")
# printf 风格：``%s`` / ``%d`` / ``%3d`` / ``%(name)s``。
_PLACEHOLDER_PRINTF_RE = re.compile(r"%\([\w.]+\)[sdifxoefgc]|%[-+#0 ]?\d*(?:\.\d+)?[sdifxoefgcp]")
# 邮箱。
_EMAIL_TOKEN_RE = re.compile(r"[\w.%+\-]+@[\w.\-]+\.[A-Za-z]{2,}")
# URL（http/https，含 ``www.`` 简写）。正则先宽松匹配，再由提取函数
# 去掉自然语言中的句末标点；不能直接排除点号，否则会截断域名和文件路径。
_URL_TOKEN_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'\u3000\u3001\u3002\uff0c\uff01\uff1f]+",
    re.IGNORECASE,
)
_URL_TRAILING_SENTENCE_MARKS = ".,;:!?，。；：！？、"
# 特殊符号：® ™ © § ¶ 以及 Unicode 货币符号范围。
_SPECIAL_SYMBOL_RE = re.compile(
    "[\u0024"  # $
    "\u00A9\u00AE\u2122\u00A7\u00B6"  # © ® ™ § ¶
    "\u00A2-\u00A5"  # ¢ £ ¤ ¥
    "\u20A0-\u20CF"  # ₠-₿
    "]",
)


def _extract_tokens(pattern: re.Pattern[str], text: str) -> list[str]:
    if not text:
        return []
    return [m.group(0) for m in pattern.finditer(text)]


def _extract_placeholder_tokens(text: str) -> list[str]:
    """同时抽取 {name} 与 %s 两类占位符。"""
    return _extract_tokens(_PLACEHOLDER_BRACE_RE, text) + _extract_tokens(_PLACEHOLDER_PRINTF_RE, text)


def _diff_multiset(source_tokens: Iterable[str], target_tokens: Iterable[str]) -> tuple[list[str], list[str]]:
    from collections import Counter

    src_counter = Counter(source_tokens)
    tgt_counter = Counter(target_tokens)
    missing = list((src_counter - tgt_counter).elements())
    extra = list((tgt_counter - src_counter).elements())
    return missing, extra


def _format_token_list(items: list[str], limit: int = 5) -> str:
    if not items:
        return "-"
    shown = ", ".join(f"“{tok}”" for tok in items[:limit])
    if len(items) > limit:
        shown += f" 等 {len(items)} 项"
    return shown


def _build_content_mismatch_issue(
    rule_key: str,
    label: str,
    missing: list[str],
    extra: list[str],
    target_text: str,
    first_token: str | None,
) -> CleanedLocalIssue:
    parts: list[str] = []
    if missing:
        parts.append(f"原文有译文缺失：{_format_token_list(missing)}")
    if extra:
        parts.append(f"译文多出原文没有的：{_format_token_list(extra)}")
    message = f"{label}不一致（{'；'.join(parts)}）。"
    # 优先定位到译文里多出的那个 token；否则用整段。
    offset = 0
    length = max(1, len(target_text))
    if first_token and target_text:
        idx = target_text.find(first_token)
        if idx >= 0:
            offset = idx
            length = len(first_token)
    return CleanedLocalIssue(
        rule_key=rule_key,
        provider=LOCAL_QA_PROVIDER_CONTENT,
        severity="medium",
        message=message,
        short_message=f"{label}不一致",
        rule_id=f"CONTENT_MISMATCH:{rule_key}",
        offset=offset,
        length=length,
        context_text=_make_context(target_text, offset, min(length, 60)),
        replacements=(),
        rule_category="content",
        issue_type="style",
    )


def _detect_token_mismatch(
    rule_key: str,
    label: str,
    source_text: str,
    target_text: str,
    extractor: Callable[[str], list[str]],
) -> list[CleanedLocalIssue]:
    if not source_text and not target_text:
        return []
    source_tokens = extractor(source_text or "")
    target_tokens = extractor(target_text or "")
    if not source_tokens and not target_tokens:
        return []
    missing, extra = _diff_multiset(source_tokens, target_tokens)
    if not missing and not extra:
        return []
    first_token = extra[0] if extra else None
    return [
        _build_content_mismatch_issue(
            rule_key=rule_key,
            label=label,
            missing=missing,
            extra=extra,
            target_text=target_text or "",
            first_token=first_token,
        )
    ]


# 规则 24：原文和译文数字不一致
def detect_number_mismatch(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    return _detect_token_mismatch(
        QA_RULE_NUMBER_MISMATCH,
        "数字",
        source_text,
        target_text,
        lambda text: _extract_tokens(_NUMBER_TOKEN_RE, text),
    )


# 规则 25：原文与译文参数占位符不一致
def detect_parameter_mismatch(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    return _detect_token_mismatch(
        QA_RULE_PARAMETER_MISMATCH,
        "参数占位符",
        source_text,
        target_text,
        _extract_placeholder_tokens,
    )


# 规则 26：原文与译文邮件信息不一致
def detect_email_mismatch(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    return _detect_token_mismatch(
        QA_RULE_EMAIL_MISMATCH,
        "邮件地址",
        source_text,
        target_text,
        lambda text: _extract_tokens(_EMAIL_TOKEN_RE, text),
    )


# 规则 27：原文和译文链接信息不一致
def detect_link_mismatch(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    return _detect_token_mismatch(
        QA_RULE_LINK_MISMATCH,
        "链接",
        source_text,
        target_text,
        # 英文句点等句末标点不属于链接；先保留域名内部的点号，再仅从尾部剥离。
        lambda text: [
            token.rstrip(_URL_TRAILING_SENTENCE_MARKS)
            for token in _extract_tokens(_URL_TOKEN_RE, text)
        ],
    )


# 规则 30：特殊符号不一致
def detect_special_symbol_mismatch(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    return _detect_token_mismatch(
        QA_RULE_SPECIAL_SYMBOL_MISMATCH,
        "特殊符号",
        source_text,
        target_text,
        lambda text: _extract_tokens(_SPECIAL_SYMBOL_RE, text),
    )


# ---------------------------------------------------------------------------
# 跨句段上下文一致性辅助（对应设置面板 23）
# ---------------------------------------------------------------------------


_CONTEXT_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_context(text: str | None) -> str:
    if not text:
        return ""
    return _CONTEXT_WHITESPACE_RE.sub(" ", text).strip()


def build_project_context_index(
    db: Session,
    project_id: UUID | None,
    *,
    min_source_length: int = 2,
) -> dict[str, dict[str, list[UUID]]]:
    """扫描项目内所有已翻译句段，聚合"相同规范化原文 → 多种规范化译文"。

    返回结构：``{norm_source: {norm_target: [segment_id, ...]}}``；只保留至少
    包含 2 种不同 norm_target 的原文条目，方便 detector 直接判定。
    """
    if project_id is None:
        return {}
    rows = (
        db.query(Segment.id, Segment.source_text, Segment.target_text)
        .join(FileRecord, FileRecord.id == Segment.file_record_id)
        .filter(FileRecord.project_id == project_id)
        .filter(Segment.source_text.isnot(None))
        .filter(Segment.target_text.isnot(None))
        .all()
    )
    index: dict[str, dict[str, list[UUID]]] = {}
    for seg_id, src, tgt in rows:
        norm_src = _normalize_for_context(src)
        norm_tgt = _normalize_for_context(tgt)
        if len(norm_src) < min_source_length or not norm_tgt:
            continue
        variants = index.setdefault(norm_src, {})
        variants.setdefault(norm_tgt, []).append(seg_id)
    # 只保留存在 >= 2 种不同译文的原文；避免每次 detector 都判定长度。
    return {src: variants for src, variants in index.items() if len(variants) >= 2}


# 规则 23：翻译与上下文匹配不一致
def detect_context_translation_mismatch(
    source_text: str,
    target_text: str,
    source_html: str = "",
    target_html: str = "",
    target_language: str = "",
    source_language: str = "",
    rule_settings: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> list[CleanedLocalIssue]:
    """同一原文在项目里被翻成多种不同译文时报告。

    - 需要通过 ``project_context["source_target_variants"]`` 传入项目级索引。
    - 规范化去掉首尾空白并折叠内部空白，其它字符保持原样（大小写敏感）。
    - 若当前译文本身还没落到索引里（例如新提交尚未 commit），仍会尝试对当前
      新译文进行比对，减少"刚保存的一句反而不报"的时序问题。
    """
    if not source_text or not target_text:
        return []
    if not isinstance(project_context, dict):
        return []
    index = project_context.get("source_target_variants")
    if not isinstance(index, dict):
        return []
    norm_src = _normalize_for_context(source_text)
    if not norm_src:
        return []
    variants = index.get(norm_src)
    if not variants:
        return []
    norm_tgt = _normalize_for_context(target_text)
    if not norm_tgt:
        return []
    # 组合当前译文与索引里已知译文，计算 unique variants 数量。
    unique_targets = set(variants.keys())
    unique_targets.add(norm_tgt)
    if len(unique_targets) < 2:
        return []
    other_variants = [v for v in unique_targets if v != norm_tgt]
    other_display = ", ".join(f"“{tok[:40]}”" for tok in other_variants[:3])
    if len(other_variants) > 3:
        other_display += f" 等 {len(other_variants)} 种"
    return [
        CleanedLocalIssue(
            rule_key=QA_RULE_CONTEXT_TRANSLATION_MISMATCH,
            provider=LOCAL_QA_PROVIDER_CONTEXT,
            severity="medium",
            message=(
                f"同一原文在项目内有多种译文：当前 “{norm_tgt[:40]}”，"
                f"其它 {other_display}。"
            ),
            short_message="项目内译文不一致",
            rule_id="CONTEXT_TRANSLATION_MISMATCH",
            offset=0,
            length=max(1, len(target_text)),
            context_text=_make_context(target_text, 0, min(len(target_text), 60)),
            replacements=(),
            rule_category="context",
            issue_type="style",
        )
    ]


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
    QA_RULE_PUNCTUATION_LEADING_EXTRA_SPACE: detect_punctuation_leading_extra_space,
    QA_RULE_PUNCTUATION_LEADING_MISSING_SPACE: detect_punctuation_leading_missing_space,
    QA_RULE_MULTIPLE_SPACES: detect_multiple_spaces,
    QA_RULE_SEGMENT_TRAILING_EXTRA_SPACE: detect_segment_trailing_extra_space,
    QA_RULE_CONSECUTIVE_DUPLICATE_WORDS: detect_consecutive_duplicate_words,
    QA_RULE_SOURCE_TARGET_INITIAL_CASE_MISMATCH: detect_source_target_initial_case_mismatch,
    QA_RULE_TARGET_WORD_MULTIPLE_UPPER_INITIALS: detect_target_word_multiple_upper_initials,
    QA_RULE_SOURCE_TARGET_SAME_WORD_CASE_MISMATCH: detect_source_target_same_word_case_mismatch,
    QA_RULE_SOURCE_TARGET_IDENTICAL: detect_source_target_identical,
    QA_RULE_TARGET_WORD_COUNT_EXCEEDS_SOURCE: detect_target_word_count_exceeds_source,
    QA_RULE_TARGET_WORD_COUNT_BELOW_SOURCE: detect_target_word_count_below_source,
    QA_RULE_SOURCE_TARGET_WORD_COUNT_GAP_TOO_LARGE: detect_source_target_word_count_gap_too_large,
    QA_RULE_NUMBER_MISMATCH: detect_number_mismatch,
    QA_RULE_PARAMETER_MISMATCH: detect_parameter_mismatch,
    QA_RULE_EMAIL_MISMATCH: detect_email_mismatch,
    QA_RULE_LINK_MISMATCH: detect_link_mismatch,
    QA_RULE_SPECIAL_SYMBOL_MISMATCH: detect_special_symbol_mismatch,
    QA_RULE_CONTEXT_TRANSLATION_MISMATCH: detect_context_translation_mismatch,
}

_RULE_PROVIDER: dict[str, str] = {
    **{key: LOCAL_QA_PROVIDER_TAG for key in TAG_QA_RULE_KEYS},
    **{key: LOCAL_QA_PROVIDER_PUNCTUATION for key in PUNCTUATION_QA_RULE_KEYS},
    **{key: LOCAL_QA_PROVIDER_CASE for key in CASE_QA_RULE_KEYS},
    **{key: LOCAL_QA_PROVIDER_LENGTH for key in LENGTH_QA_RULE_KEYS},
    **{key: LOCAL_QA_PROVIDER_CONTENT for key in CONTENT_QA_RULE_KEYS},
    **{key: LOCAL_QA_PROVIDER_CONTEXT for key in CONTEXT_QA_RULE_KEYS},
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
    commit: bool = True,
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

    target_language = (getattr(file_record, "target_language", None) or "").strip()
    source_language = (getattr(file_record, "source_language", None) or "").strip()
    project_settings = load_quality_qa_settings(project)
    rules_settings_map: dict[str, dict[str, Any]] = {
        str(key): dict(value) if isinstance(value, dict) else {}
        for key, value in (project_settings.get("rules") or {}).items()
    }
    project_context: dict[str, Any] | None = None
    if QA_RULE_CONTEXT_TRANSLATION_MISMATCH in enabled:
        project_id = getattr(file_record, "project_id", None)
        project_context = {
            "source_target_variants": build_project_context_index(db, project_id),
        }
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
                cleaned = detector(
                    source_text,
                    target_text,
                    source_html,
                    target_html,
                    target_language=target_language,
                    source_language=source_language,
                    rule_settings=rules_settings_map.get(rule_key),
                    project_context=project_context,
                )
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
        if commit:
            db.commit()
        else:
            db.flush()
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
