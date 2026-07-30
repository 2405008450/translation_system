"""
程序规则 — 专有名词（类别 4：机械可判定部分）
对应规则文档 §4.x

机械判定仅覆盖：缩写形式、禁止的拼音写法、已知的错误表达。
其余（机构名官方译法、地址格式等）交给 AI。
"""
from __future__ import annotations

import re

from app.services.translation_review.program_rules.symbols import Finding


def check_proper_noun(source_text: str, target_text: str) -> list[Finding]:  # noqa: ARG001
    findings: list[Finding] = []
    _check_abbreviation_format(target_text, findings)
    _check_southern_guangdong(target_text, findings)
    _check_lyu_spelling(target_text, findings)
    _check_mainland_expression(target_text, findings)
    return findings


# ─── 4.9 永远用全称，禁止 XXXXX("XX") 缩写介绍形式 ──────────

_ABBR_INTRO_RE = re.compile(r'\b([A-Z][A-Za-z\s]+)\s*\(["\']?([A-Z]{2,})["\']?\)')


def _check_abbreviation_format(text: str, findings: list[Finding]) -> None:
    for m in _ABBR_INTRO_RE.finditer(text):
        findings.append(Finding(
            rule_ref="4.9",
            quote=m.group(),
            replace_anchor=m.group(),
            suggested_value=m.group(1).strip(),
            reason=(
                "不要采取「全称（缩写）」的形式，请永远用全称。"
                f"建议去掉缩写部分：{m.group(2)}"
            ),
            confidence="medium",
        ))


# ─── 4.10.2 南粤 = Guangdong，不是 southern Guangdong ───────

_SOUTHERN_GD_RE = re.compile(r"\bsouthern\s+Guangdong\b", re.IGNORECASE)


def _check_southern_guangdong(text: str, findings: list[Finding]) -> None:
    for m in _SOUTHERN_GD_RE.finditer(text):
        findings.append(Finding(
            rule_ref="4.10.2",
            quote=m.group(),
            replace_anchor=m.group(),
            suggested_value="Guangdong",
            reason="南粤特指广东省，应译为 Guangdong，不是 southern Guangdong",
            confidence="high",
        ))


# ─── 4.1 "吕"的拼音用 Lv，不用 Lyu ─────────────────────────

_LYU_RE = re.compile(r"\bLyu\b")


def _check_lyu_spelling(text: str, findings: list[Finding]) -> None:
    for m in _LYU_RE.finditer(text):
        findings.append(Finding(
            rule_ref="4.1",
            quote=m.group(),
            replace_anchor=m.group(),
            suggested_value="Lv",
            reason="「吕」的拼音应拼写为 Lv，不用 Lyu",
            confidence="high",
        ))


# ─── 4.10.2 大陆 = Chinese mainland，不是 mainland China ──────

_MAINLAND_WRONG_RE = re.compile(r"\bmainland\s+China\b", re.IGNORECASE)


def _check_mainland_expression(text: str, findings: list[Finding]) -> None:
    for m in _MAINLAND_WRONG_RE.finditer(text):
        findings.append(Finding(
            rule_ref="4.10.2",
            quote=m.group(),
            replace_anchor=m.group(),
            suggested_value="Chinese mainland",
            reason="应译为「Chinese mainland」，不是「mainland China」",
            confidence="high",
        ))
