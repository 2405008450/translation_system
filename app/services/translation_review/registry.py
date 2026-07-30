"""
Agent 注册表 —— 10 个类别的声明式配置
"""
from __future__ import annotations

import re as _re_module
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class CategoryAgent:
    key: str
    """稳定标识，落库用"""
    index: int
    """对应规则文档章节号"""
    label: str
    severity: str
    """error | warning | suggestion"""
    mode: str
    """program_only | ai_only | program_then_ai"""
    apply_mode: str
    """anchor | full | manual"""
    batch_size: int = 12
    needs_context: bool = False
    needs_terms: bool = False
    allow_web_verify: bool = False
    program_rule: Callable | None = None
    """(source_text, target_text, **kwargs) → list[Finding]"""
    ai_input_filter: Callable | None = None
    """(payloads: list[dict]) → list[dict]  短路过滤：返回需送 LLM 的子集"""
    rule_refs: tuple[str, ...] = field(default_factory=tuple)


# ─── ai_input_filter helpers ──────────────────────────────

def _symbols_ai_filter(payloads: list[dict]) -> list[dict]:
    """§1.5 中文间隔号（·）需 AI 判断如何转换。"""
    return [p for p in payloads if "·" in (p.get("target_text") or "")]


def _casing_ai_filter(payloads: list[dict]) -> list[dict]:
    """§2.6 双引号内大小写交给 AI。"""
    return [p for p in payloads if '"' in (p.get("target_text") or "")]


def _number_format_ai_filter(payloads: list[dict]) -> list[dict]:
    """§3.7.2 含 + 号特殊数字、经纬度交给 AI。"""
    return [
        p for p in payloads
        if _re_module.search(r"\d\+\d|°", p.get("source_text") or "")
    ]


_PROPER_RE = _re_module.compile(r"\b[A-Z][a-z]{2,}\b")


def _proper_noun_ai_filter(payloads: list[dict]) -> list[dict]:
    """含疑似专名（大写词）的句段送 AI。"""
    return [p for p in payloads if _PROPER_RE.search(p.get("target_text") or "")]


# ─── program_rule loaders（延迟导入，避免循环） ──────────────

def _symbols_rule(source_text: str, target_text: str, **_kw):
    from app.services.translation_review.program_rules.symbols import check_symbols
    return check_symbols(source_text, target_text)


def _casing_rule(source_text: str, target_text: str, **kw):
    from app.services.translation_review.program_rules.casing import check_casing
    return check_casing(source_text, target_text, block_type=kw.get("block_type", "paragraph"))


def _number_format_rule(source_text: str, target_text: str, **_kw):
    from app.services.translation_review.program_rules.number_format import check_number_format
    return check_number_format(source_text, target_text)


def _proper_noun_rule(source_text: str, target_text: str, **_kw):
    from app.services.translation_review.program_rules.proper_noun import check_proper_noun
    return check_proper_noun(source_text, target_text)


def _omission_rule(source_text: str, target_text: str, **_kw):
    from app.services.translation_review.program_rules.omission import check_omission
    return check_omission(source_text, target_text)


# ─── 注册表 ───────────────────────────────────────────────

AGENT_REGISTRY: list[CategoryAgent] = [
    CategoryAgent(
        key="tense",
        index=0,
        label="时态",
        severity="error",
        mode="ai_only",
        apply_mode="anchor",
        batch_size=12,
        needs_context=True,
        rule_refs=("0.1", "0.2"),
    ),
    CategoryAgent(
        key="symbols",
        index=1,
        label="英文符号与中文符号",
        severity="error",
        mode="program_then_ai",
        apply_mode="anchor",
        batch_size=15,
        rule_refs=("1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"),
        ai_input_filter=_symbols_ai_filter,
        program_rule=_symbols_rule,
    ),
    CategoryAgent(
        key="casing",
        index=2,
        label="大小写",
        severity="error",
        mode="program_then_ai",
        apply_mode="anchor",
        batch_size=12,
        rule_refs=("2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9"),
        ai_input_filter=_casing_ai_filter,
        program_rule=_casing_rule,
    ),
    CategoryAgent(
        key="number_format",
        index=3,
        label="数字格式",
        severity="error",
        mode="program_then_ai",
        apply_mode="anchor",
        batch_size=15,
        rule_refs=("3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7", "3.8", "3.9", "3.10"),
        ai_input_filter=_number_format_ai_filter,
        program_rule=_number_format_rule,
    ),
    CategoryAgent(
        key="proper_noun",
        index=4,
        label="专有名词",
        severity="error",
        mode="program_then_ai",
        apply_mode="anchor",
        batch_size=8,
        needs_terms=True,
        allow_web_verify=True,
        rule_refs=("4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "4.10", "4.11"),
        ai_input_filter=_proper_noun_ai_filter,
        program_rule=_proper_noun_rule,
    ),
    CategoryAgent(
        key="fixed_syntax",
        index=5,
        label="固定句法",
        severity="warning",
        mode="ai_only",
        apply_mode="full",
        batch_size=10,
        needs_context=True,
        rule_refs=("5.1", "5.2", "5.3", "5.4", "5.5", "5.6"),
    ),
    CategoryAgent(
        key="noun_merge",
        index=6,
        label="相同格式名词的合并",
        severity="warning",
        mode="ai_only",
        apply_mode="anchor",
        batch_size=12,
        rule_refs=("6",),
    ),
    CategoryAgent(
        key="omission",
        index=7,
        label="避免漏译",
        severity="error",
        mode="program_then_ai",
        apply_mode="full",
        batch_size=10,
        needs_context=True,
        rule_refs=("7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7"),
        program_rule=_omission_rule,
    ),
    CategoryAgent(
        key="comprehension",
        index=8,
        label="原文理解",
        severity="error",
        mode="ai_only",
        apply_mode="full",
        batch_size=8,
        needs_context=True,
        rule_refs=("8.1", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "8.9"),
    ),
    CategoryAgent(
        key="syntax_polish",
        index=9,
        label="句法优化",
        severity="suggestion",
        mode="ai_only",
        apply_mode="full",
        batch_size=8,
        needs_context=True,
        rule_refs=("9.1", "9.2", "9.3"),
    ),
]

AGENT_BY_KEY: dict[str, CategoryAgent] = {a.key: a for a in AGENT_REGISTRY}


def get_agents(enabled_keys: list[str] | None = None) -> list[CategoryAgent]:
    """返回启用的 Agent 列表，None 表示全部。"""
    if enabled_keys is None:
        return list(AGENT_REGISTRY)
    key_set = set(enabled_keys)
    return [a for a in AGENT_REGISTRY if a.key in key_set]
