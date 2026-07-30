"""
程序规则 — 避免漏译（类别 7：机械可判定部分）
对应规则文档 §7.2

机械规则：历史纪年 + 公元年双写检查。
原文含「XX年（YYYY年）」格式，译文只出现其中一个。
其余漏译判断（并列动词、限定词等）交给 AI。
"""
from __future__ import annotations

import re

from app.services.translation_review.program_rules.symbols import Finding

# 匹配原文中的历史纪年 + 公元年：如「秦始皇三十三年（公元前214年）」
_SOURCE_ERA_YEAR_RE = re.compile(
    r"[^\s，。！？；：]{2,8}[年]"   # 历史纪年（非阿拉伯数字）
    r"[（(]"
    r"(?:公元前?\s*)?(\d{2,4})[年]?"
    r"[）)]"
)

# 匹配译文中的公元年：BC/AD + 数字，或 4 位数字独立出现
_TARGET_YEAR_RE = re.compile(r"\b(\d{3,4})\s*(?:BC|AD|BCE|CE)?\b")

# 匹配历史纪年格式在译文中的体现：
# "the Xth year of the Y era" / "Year X of the Z"
_TARGET_ERA_RE = re.compile(
    r"\bthe\s+(?:\d+(?:st|nd|rd|th)?|first|second|third|fourth|fifth|"
    r"sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|Xth|Nth)\s+year\b"
    r"|\bYear\s+\d+\b"
    r"|\bera\b",
    re.IGNORECASE,
)


def check_omission(source_text: str, target_text: str) -> list[Finding]:
    findings: list[Finding] = []
    _check_era_year_dual_translation(source_text, target_text, findings)
    return findings


def _check_era_year_dual_translation(
    source_text: str,
    target_text: str,
    findings: list[Finding],
) -> None:
    """
    §7.2 历史纪年与公元年均应译出，不应只翻译其中一个。
    """
    for m in _SOURCE_ERA_YEAR_RE.finditer(source_text):
        era_year_str = m.group(1)  # 公元年数字

        # 译文是否有公元年
        has_ad_year = bool(_TARGET_YEAR_RE.search(target_text))
        # 译文是否有历史纪年表达
        has_era_text = bool(_TARGET_ERA_RE.search(target_text))

        if has_ad_year and has_era_text:
            continue  # 两者都有，正确

        missing_parts: list[str] = []
        if not has_era_text:
            missing_parts.append("历史纪年（如 the Xth year of the Y era）")
        if not has_ad_year:
            missing_parts.append(f"公元年（{era_year_str}）")

        if missing_parts:
            findings.append(Finding(
                rule_ref="7.2",
                quote="",
                replace_anchor="",
                suggested_value="",
                reason=(
                    f"原文含历史纪年与公元年（{m.group()}），"
                    f"译文缺少：{'、'.join(missing_parts)}，应两者均译出"
                ),
                confidence="medium",
            ))
