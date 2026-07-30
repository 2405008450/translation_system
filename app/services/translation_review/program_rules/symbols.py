"""
程序规则 — 英文符号与中文符号（类别 1：机械可判定部分）
对应规则文档 §1.1–§1.7

返回格式：
    Finding 字典列表，每条包含：
        rule_ref, quote, replace_anchor, suggested_value, reason, confidence
    quote == '' 时表示整句都需审查（不能高亮特定片段）
"""
from __future__ import annotations

import re
from typing import TypedDict


class Finding(TypedDict):
    rule_ref: str
    quote: str
    replace_anchor: str
    suggested_value: str
    reason: str
    confidence: str   # high | medium | low


# ─── 常用中文标点 → 对应英文标点 ───────────────────────────
_CN_PUNC_MAP: dict[str, str] = {
    "，": ",",
    "。": ".",
    "！": "!",
    "？": "?",
    "；": ";",
    "：": ":",
    "（": "(",
    "）": ")",
    "【": "[",
    "】": "]",
    "『": '"',
    "』": '"',
    "「": '"',
    "」": '"',
    "《": '"',
    "》": '"',
    "、": ",",
    "…": "...",
}

# 不视作错误的中文字符（行首书名号用于说明书名，不做替换）
_ALLOWED_CN_CHARS = frozenset()


def check_symbols(source_text: str, target_text: str) -> list[Finding]:  # noqa: ARG001
    """
    检查译文中的英文符号合规情况。
    source_text 目前只作扩展预留，部分规则以后可能对照原文。
    """
    findings: list[Finding] = []

    _check_chinese_punctuation(target_text, findings)
    _check_ampersand_spaces(target_text, findings)
    _check_slash_spaces(target_text, findings)
    _check_em_dash(target_text, findings)
    _check_en_dash(target_text, findings)
    _check_phone_bracket_space(target_text, findings)
    _check_nested_brackets(target_text, findings)

    return findings


# ─── 1.1 译文不得使用中文标点 ──────────────────────────────

_CN_PUNC_RE = re.compile(
    "[" + re.escape("".join(_CN_PUNC_MAP.keys())) + "·]"  # · 单独处理为 AI 类别
)


def _check_chinese_punctuation(text: str, findings: list[Finding]) -> None:
    for m in _CN_PUNC_RE.finditer(text):
        char = m.group()
        if char == "·":
            continue  # § 1.5 中文间隔号转换逻辑复杂，交给 AI 类别
        replacement = _CN_PUNC_MAP.get(char, "")
        findings.append(Finding(
            rule_ref="1.1",
            quote=char,
            replace_anchor=char,
            suggested_value=replacement,
            reason=f"译文不得使用中文标点符号「{char}」，应改用对应英文标点「{replacement}」",
            confidence="high",
        ))


# ─── 1.2 & 前后各空一格（固定搭配如 R&D 除外） ──────────────

# 匹配 & 左边没有空格，或右边没有空格（但不是固定搭配 R&D / S&P 等）
_AMP_NOSPACE_RE = re.compile(r"(?<!\s)&(?!\s)|(?<=\s)&(?!\s)|(?<!\s)&(?=\s)")
_AMP_FIXED_RE = re.compile(r"\b[A-Z0-9]&[A-Z0-9]\b")   # R&D, S&P …


def _check_ampersand_spaces(text: str, findings: list[Finding]) -> None:
    for m in _AMP_NOSPACE_RE.finditer(text):
        start = m.start()
        # 检查是否属于固定搭配（前后各一字符构成缩写）
        window = text[max(0, start - 1): start + 2]
        if _AMP_FIXED_RE.search(window):
            continue
        findings.append(Finding(
            rule_ref="1.2",
            quote=m.group(),
            replace_anchor=m.group(),
            suggested_value=" & ",
            reason="& 前后各需空一格（固定搭配如 R&D 除外）",
            confidence="high",
        ))


# ─── 1.2 / 前后不空格 ──────────────────────────────────────

_SLASH_SPACE_RE = re.compile(r"\s/\s|(?<!\s)/\s|\s/(?!\s)")


def _check_slash_spaces(text: str, findings: list[Finding]) -> None:
    for m in _SLASH_SPACE_RE.finditer(text):
        findings.append(Finding(
            rule_ref="1.2",
            quote=m.group(),
            replace_anchor=m.group(),
            suggested_value="/",
            reason="/ 前后均不需要空格",
            confidence="high",
        ))


# ─── 1.4 破折号统一用 em dash（—，U+2014），非连字符或双连字符 ──

# 匹配 -- 或单独的 - 作为句中破折号（不是连字符/连接词/负号）
# 规则：前后有空格且两侧都有内容则视为破折号
_WRONG_DASH_RE = re.compile(r"(?<=\w)\s*--?\s*(?=\w)|(?<=\s)--(?=\s)")


def _check_em_dash(text: str, findings: list[Finding]) -> None:
    for m in _WRONG_DASH_RE.finditer(text):
        findings.append(Finding(
            rule_ref="1.4",
            quote=m.group(),
            replace_anchor=m.group(),
            suggested_value="—",
            reason="破折号应使用 em dash（—），不用 - 或 --",
            confidence="medium",
        ))


# ─── 1.6 年份区间用 en dash（–，U+2013），前后不空格 ──────────

# 匹配 xxxx - xxxx 或 xxxx–xxxx 中的长横线写法
_YEAR_RANGE_RE = re.compile(r"\b(\d{4})\s*[-—]\s*(\d{4})\b")


def _check_en_dash(text: str, findings: list[Finding]) -> None:
    for m in _YEAR_RANGE_RE.finditer(text):
        original = m.group()
        if "–" in original and " " not in original:
            continue   # 已经正确
        y1, y2 = m.group(1), m.group(2)
        correct = f"{y1}–{y2}"
        findings.append(Finding(
            rule_ref="1.6",
            quote=original,
            replace_anchor=original,
            suggested_value=correct,
            reason="年份区间应使用 en dash（–）且前后不加空格",
            confidence="high",
        ))


# ─── 1.3 电话括号后空一格 ──────────────────────────────────

# 形如 (0755) 不带空格后接数字
_PHONE_RE = re.compile(r"\(\d+\)(?! )\d")


def _check_phone_bracket_space(text: str, findings: list[Finding]) -> None:
    for m in _PHONE_RE.finditer(text):
        original = m.group()
        # 在 ) 后插入空格
        fixed = original.replace(")", ") ", 1)
        findings.append(Finding(
            rule_ref="1.3",
            quote=original,
            replace_anchor=original,
            suggested_value=fixed,
            reason="电话括号后需空一格，如 (021) 38969999",
            confidence="high",
        ))


# ─── 1.7 括号内套括号：外圆括号、内方括号 ─────────────────────

# 检测 (... (...) ...) 嵌套情况
_NESTED_PARENS_RE = re.compile(r"\([^()]*\([^()]*\)[^()]*\)")


def _check_nested_brackets(text: str, findings: list[Finding]) -> None:
    for m in _NESTED_PARENS_RE.finditer(text):
        outer = m.group()
        # 内层已经用了方括号则跳过
        if "[" in outer and "]" in outer:
            continue
        # 构造建议：把最内层 () 替换为 []
        suggested = re.sub(r"\(([^()]*)\)", r"[\1]", outer, count=1)
        if suggested == outer:
            continue
        findings.append(Finding(
            rule_ref="1.7",
            quote=outer,
            replace_anchor=outer,
            suggested_value=suggested,
            reason="括号内套括号时，外层用圆括号，内层应改用方括号",
            confidence="medium",
        ))
