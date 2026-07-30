"""
程序规则 — 数字格式（类别 3：机械可判定部分）
对应规则文档 §3.1–§3.10

说明：
- 只检查英文书写格式合规；数值与原文是否一致由「数字专检」负责。
- 歧义项（经纬度、"10+1" 含义数字等）交给 AI。
"""
from __future__ import annotations

import re

from app.services.translation_review.program_rules.symbols import Finding

# ─── 3.1 / 3.8 数字 0-9 拼写，10+ 用阿拉伯数字 ──────────────

_DIGIT_WORDS = {
    "zero", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine",
}
# 10 及以上用了英文单词（不含 ten 单独作序号是合法的：the ten steps）
_LARGE_WORD_NUMBER_RE = re.compile(
    r"\b(eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
    r"|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    r"|hundred|thousand|million|billion|trillion)\b",
    re.IGNORECASE,
)

# 数字 1-9 用了阿拉伯数字（但允许：科学单位如 7 μg, 序号, 日期, 金额, 百分比等）
_LONE_DIGIT_RE = re.compile(r"(?<![.\d])\b([1-9])\b(?!\s*[%°μkKMBTFGHzmΩ℃'/]|st\b|nd\b|rd\b|th\b|/)")


def check_number_format(source_text: str, target_text: str) -> list[Finding]:  # noqa: ARG001
    findings: list[Finding] = []
    _check_sentence_start_digit(target_text, findings)
    _check_percentage_point(target_text, findings)
    _check_date_format(target_text, findings)
    _check_currency_format(target_text, findings)
    _check_measure_unit_abbr(target_text, findings)
    _check_odd_hyphen(target_text, findings)
    _check_fraction_spelling(target_text, findings)
    _check_roman_phase(target_text, findings)
    return findings


# ─── 3.9 不能用阿拉伯数字开头一个句子 ────────────────────────

_SENTENCE_START_DIGIT_RE = re.compile(r"(^|[.!?]\s+)(\d)")


def _check_sentence_start_digit(text: str, findings: list[Finding]) -> None:
    for m in _SENTENCE_START_DIGIT_RE.finditer(text):
        digit = m.group(2)
        context = text[m.start(): m.start() + 20]
        findings.append(Finding(
            rule_ref="3.9",
            quote=context,
            replace_anchor=context,
            suggested_value="",  # 具体改法需上下文，留给人工
            reason=f"句首不得用阿拉伯数字「{digit}」开头，应拼写为英文单词",
            confidence="high",
        ))


# ─── 3.3 "percentage point(s)" 单复数 ────────────────────────

_PCT_POINT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s+(percentage point)(?!s)",
    re.IGNORECASE,
)

_PCT_POINT_PLURAL_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s+(percentage points)\b",
    re.IGNORECASE,
)


def _check_percentage_point(text: str, findings: list[Finding]) -> None:
    # 检查单数应复数
    for m in _PCT_POINT_RE.finditer(text):
        num_str = m.group(1)
        try:
            num = float(num_str)
        except ValueError:
            continue
        if num != 1.0:
            findings.append(Finding(
                rule_ref="3.3",
                quote=m.group(),
                replace_anchor=m.group(),
                suggested_value=m.group().rstrip() + "s",
                reason=f"数值 {num_str} 需要复数形式：percentage points",
                confidence="high",
            ))
    # 检查复数应单数
    for m in _PCT_POINT_PLURAL_RE.finditer(text):
        num_str = m.group(1)
        try:
            num = float(num_str)
        except ValueError:
            continue
        if num == 1.0:
            findings.append(Finding(
                rule_ref="3.3",
                quote=m.group(),
                replace_anchor=m.group(),
                suggested_value=m.group().replace("percentage points", "percentage point"),
                reason="数值为 1 应使用单数形式：percentage point",
                confidence="high",
            ))


# ─── 3.4 日期格式 ─────────────────────────────────────────

# 格式 DD/MM/YYYY 或 YYYY-MM-DD（应改为 Month DD, YYYY 或 Month YYYY）
_WRONG_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")


def _check_date_format(text: str, findings: list[Finding]) -> None:
    for m in _WRONG_DATE_RE.finditer(text):
        findings.append(Finding(
            rule_ref="3.4",
            quote=m.group(),
            replace_anchor=m.group(),
            suggested_value="",
            reason=f"日期「{m.group()}」应改为 Month DD, YYYY 格式（如 June 4, 1998）",
            confidence="medium",
        ))


# ─── 3.5 金额格式 ─────────────────────────────────────────

# 千分位缺失：纯数字 4 位以上没有逗号
_LARGE_NUMBER_NO_COMMA_RE = re.compile(r"\b(\d{4,})\b")
# 金额前缀检查：$ 或 USD/RMB 后的数字
_CURRENCY_RE = re.compile(r"(USD|RMB|\$)\s*(\d[\d,]*(?:\.\d+)?)")
# 小数超两位
_TOO_MANY_DECIMALS_RE = re.compile(r"\b(\d+\.\d{3,})\s*(million|billion|thousand)?\b", re.IGNORECASE)


def _check_currency_format(text: str, findings: list[Finding]) -> None:
    # 金额小数超两位
    for m in _TOO_MANY_DECIMALS_RE.finditer(text):
        amount = m.group(1)
        decimal_part = amount.split(".")[-1]
        if len(decimal_part) > 2:
            findings.append(Finding(
                rule_ref="3.5",
                quote=m.group(),
                replace_anchor=m.group(),
                suggested_value="",
                reason=f"金额小数超过两位（{amount}），应降级处理（如 million → thousand）",
                confidence="medium",
            ))

    # 4 位以上数字缺千分位（在 currency 上下文中）
    for m in _CURRENCY_RE.finditer(text):
        num_part = m.group(2).replace(",", "")
        if len(num_part) >= 4 and "," not in m.group(2):
            findings.append(Finding(
                rule_ref="3.5",
                quote=m.group(),
                replace_anchor=m.group(),
                suggested_value="",
                reason=f"金额数字应加千分位符（{m.group(2)}）",
                confidence="high",
            ))


# ─── 3.6 度量衡用全称，不用缩写 ──────────────────────────────

# 除了 kWh 等已知例外
_UNIT_ABBR_MAP = {
    r"\bkm\b": "kilometers",
    r"\bcm\b": "centimeters",
    r"\bmm\b": "millimeters",
    r"\bm\b(?!\w)": "meters",
    r"\bkg\b": "kilograms",
    r"\bg\b(?!\w)": "grams",
    r"\bm2\b": "square meters",
    r"\bm3\b": "cubic meters",
    r"\bkm2\b": "square kilometers",
    r"\bha\b": "hectares",
    r"\bt\b(?!\w)": "tons",
}
_UNIT_EXCEPTIONS_RE = re.compile(r"\bkWh\b|μg|μm|PM\d", re.IGNORECASE)


def _check_measure_unit_abbr(text: str, findings: list[Finding]) -> None:
    if _UNIT_EXCEPTIONS_RE.search(text):
        return   # 含例外单位，整句交给 AI 判断
    for pattern, full_name in _UNIT_ABBR_MAP.items():
        for m in re.finditer(pattern, text):
            findings.append(Finding(
                rule_ref="3.6",
                quote=m.group(),
                replace_anchor=m.group(),
                suggested_value=full_name,
                reason=f"度量衡应使用全称「{full_name}」，不用缩写「{m.group()}」",
                confidence="medium",
            ))


# ─── 3.10 X odd 缺 hyphen ───────────────────────────────

_ODD_NO_HYPHEN_RE = re.compile(r"\b(\d+)\s+odd\b", re.IGNORECASE)


def _check_odd_hyphen(text: str, findings: list[Finding]) -> None:
    for m in _ODD_NO_HYPHEN_RE.finditer(text):
        original = m.group()
        suggested = f"{m.group(1)}-odd"
        findings.append(Finding(
            rule_ref="3.10",
            quote=original,
            replace_anchor=original,
            suggested_value=suggested,
            reason="数字+odd 结构应加连字符，如 10-odd people",
            confidence="high",
        ))


# ─── 3.4 分数拼写（分数应用单词）────────────────────────────

_FRACTION_DIGIT_RE = re.compile(r"\b(\d+)/(\d+)\b")
_FRACTION_WORD_MAP = {
    (1, 2): "one half", (1, 3): "one third", (2, 3): "two thirds",
    (1, 4): "one fourth", (3, 4): "three fourths",
    (1, 5): "one fifth", (1, 6): "one sixth", (1, 8): "one eighth",
}


def _check_fraction_spelling(text: str, findings: list[Finding]) -> None:
    for m in _FRACTION_DIGIT_RE.finditer(text):
        try:
            num = int(m.group(1))
            den = int(m.group(2))
        except ValueError:
            continue
        word_form = _FRACTION_WORD_MAP.get((num, den))
        if word_form:
            findings.append(Finding(
                rule_ref="3.4",
                quote=m.group(),
                replace_anchor=m.group(),
                suggested_value=word_form,
                reason=f"分数应拼写为英文单词，如 {m.group()} → {word_form}",
                confidence="high",
            ))


# ─── 3.7.3 X 期 / Phase 后用大写罗马数字 ─────────────────────

_PHASE_DIGIT_RE = re.compile(r"\bPhase\s+(\d+)\b", re.IGNORECASE)
_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X"}


def _check_roman_phase(text: str, findings: list[Finding]) -> None:
    for m in _PHASE_DIGIT_RE.finditer(text):
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        roman = _ROMAN.get(num)
        if roman:
            correct = f"Phase {roman}"
            findings.append(Finding(
                rule_ref="3.7.3",
                quote=m.group(),
                replace_anchor=m.group(),
                suggested_value=correct,
                reason=f"Phase 后应用大写罗马数字，如 Phase {roman}",
                confidence="high",
            ))
