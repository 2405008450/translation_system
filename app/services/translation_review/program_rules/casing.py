"""
程序规则 — 大小写（类别 2：标题机械项）
对应规则文档 §2.1–§2.9（双引号内处理交给 AI 类别）

标题判定：启发式（无句末标点 + 相对较短 + block_type 非 table_cell）。
程序规则仅覆盖确定性强的条款；边界情况由 AI 二次判断。
"""
from __future__ import annotations

import re

from app.services.translation_review.program_rules.symbols import Finding

# ─── 标题识别 ──────────────────────────────────────────────

_HEADING_MAX_WORDS = 25  # 超过这个词数，即使无句末标点也不视作标题
_SENTENCE_END_RE = re.compile(r"[.!?;]\s*$")


def is_likely_heading(text: str, block_type: str = "paragraph") -> bool:
    """
    启发式标题判定。
    - 无句末标点（.!?;）
    - 词数 ≤ 25
    - block_type 不是 table_cell
    注意：只是启发式，不是确定性。提示词里让模型二次确认。
    """
    if block_type in ("table_cell", "textbox", "chart_text"):
        return False
    if _SENTENCE_END_RE.search(text.strip()):
        return False
    word_count = len(text.split())
    return word_count <= _HEADING_MAX_WORDS


# ─── 英文实词 vs 功能词 ────────────────────────────────────

# §2.8 标题中介词一律小写（无论长短）
_PREPOSITIONS = frozenset({
    "a", "an", "the",
    "at", "by", "for", "in", "of", "on", "to", "up",
    "as", "but", "nor", "or",
    "about", "above", "across", "after", "against", "along", "among",
    "around", "because", "before", "behind", "below", "beneath",
    "beside", "between", "beyond", "during", "except", "from",
    "inside", "into", "like", "near", "off", "out", "outside",
    "over", "past", "since", "than", "through", "throughout",
    "under", "until", "unto", "upon", "via", "with", "within",
    "without", "and", "yet", "so",
})

# 需要小写的拉丁词 §2.3 注释
_LATIN_WORDS = frozenset({"per", "capita", "et", "al", "ibid", "viz", "etc", "vs"})


def _should_capitalize_in_title(word: str, is_first: bool, is_last: bool) -> bool | None:
    """
    True: 应大写；False: 应小写；None: 无法确定（留给 AI）
    """
    clean = re.sub(r"[^A-Za-z'-]", "", word).lower()
    if not clean:
        return None
    if is_first or is_last:
        return True
    if clean in _LATIN_WORDS:
        return False
    if clean in _PREPOSITIONS:
        return False
    return True  # 实词 → 应大写


# ─── 公共检查入口 ──────────────────────────────────────────

def check_casing(
    source_text: str,  # noqa: ARG001 — 预留
    target_text: str,
    block_type: str = "paragraph",
) -> list[Finding]:
    findings: list[Finding] = []

    _check_party_capitalization(target_text, findings)
    _check_dynasty_format(target_text, findings)
    _check_colon_capitalization(target_text, findings)

    if is_likely_heading(target_text, block_type):
        _check_title_case(target_text, findings)

    return findings


# ─── 2.7 党（指中共）一律大写 ─────────────────────────────

_PARTY_WRONG_RE = re.compile(r"\bthe party\b", re.IGNORECASE)
_PARTY_CORRECT_RE = re.compile(r"\bthe Party\b")


def _check_party_capitalization(text: str, findings: list[Finding]) -> None:
    for m in _PARTY_WRONG_RE.finditer(text):
        match_str = m.group()
        # 允许 "the Party" 或 "The Party"（首字母可大可小，但 P 必须大写）
        if match_str.lower() == "the party" and match_str[-5] == "P":
            continue  # already correct
        findings.append(Finding(
            rule_ref="2.7",
            quote=match_str,
            replace_anchor=match_str,
            suggested_value="the Party" if match_str[0].islower() else "The Party",
            reason="「党」指中国共产党，Party 必须大写",
            confidence="high",
        ))


# ─── 2.5 朝代写法 the X dynasty ──────────────────────────

_DYNASTY_RE = re.compile(r"\b(the\s+)?([A-Z][a-z]+)\s+Dynasty\b")


def _check_dynasty_format(text: str, findings: list[Finding]) -> None:
    for m in _DYNASTY_RE.finditer(text):
        original = m.group()
        dynasty_name = m.group(2)
        correct = f"the {dynasty_name} dynasty"
        findings.append(Finding(
            rule_ref="2.5",
            quote=original,
            replace_anchor=original,
            suggested_value=correct,
            reason="朝代格式：the * dynasty，dynasty 小写且前加 the",
            confidence="high",
        ))


# ─── 2.9 冒号后首字母大写 ─────────────────────────────────

_COLON_LOWER_RE = re.compile(r":(\s+)([a-z])")


def _check_colon_capitalization(text: str, findings: list[Finding]) -> None:
    for m in _COLON_LOWER_RE.finditer(text):
        original = m.group()
        fixed = ": " + m.group(2).upper()
        findings.append(Finding(
            rule_ref="2.9",
            quote=original,
            replace_anchor=original,
            suggested_value=fixed,
            reason="冒号后的第一个字母需要大写",
            confidence="high",
        ))


# ─── 标题实词首字母大写检查（2.1）────────────────────────────

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _check_title_case(text: str, findings: list[Finding]) -> None:
    """
    检查标题内英文实词首字母是否大写（介词、功能词应小写）。
    对于含连字符的词 (§2.1)，hyphen 后的词根据前缀规则判断：
    - 前缀（pre-, re-, co- 等）后不大写
    - 其他情况大写
    """
    words = _WORD_RE.findall(text)
    if not words:
        return
    total = len(words)

    for i, word in enumerate(words):
        is_first = (i == 0)
        is_last = (i == total - 1)

        # 带连字符的词
        if "-" in word:
            _check_hyphenated_title_word(word, text, findings, is_first, is_last)
            continue

        should_cap = _should_capitalize_in_title(word, is_first, is_last)
        if should_cap is None:
            continue

        first_char = word[0]
        if should_cap and first_char.islower():
            findings.append(Finding(
                rule_ref="2.1",
                quote=word,
                replace_anchor=word,
                suggested_value=word[0].upper() + word[1:],
                reason=f"标题中实词「{word}」首字母应大写",
                confidence="medium",
            ))
        elif not should_cap and first_char.isupper() and not is_first and not is_last:
            findings.append(Finding(
                rule_ref="2.8",
                quote=word,
                replace_anchor=word,
                suggested_value=word.lower(),
                reason=f"标题中介词/冠词/连词「{word}」应小写（首尾词除外）",
                confidence="medium",
            ))


# 常见前缀（前缀后 hyphen 后不大写）
_PREFIX_RE = re.compile(
    r"^(pre|re|co|sub|non|anti|un|in|ex|semi|ultra|over|under|mid|multi|bi|tri|"
    r"macro|micro|mega|mini|neo|pan|para|post|pro|pseudo|quasi|super|trans|up|"
    r"vice|self|half|well|cross|full)\-",
    re.IGNORECASE,
)


def _check_hyphenated_title_word(
    word: str,
    text: str,
    findings: list[Finding],
    is_first: bool,
    is_last: bool,
) -> None:
    parts = word.split("-")
    if len(parts) < 2:
        return
    first_part = parts[0]
    rest_parts = parts[1:]

    # 检查首部分
    should_first = _should_capitalize_in_title(first_part, is_first, is_last)
    if should_first and first_part[0].islower():
        findings.append(Finding(
            rule_ref="2.1",
            quote=word,
            replace_anchor=word,
            suggested_value=word[0].upper() + word[1:],
            reason=f"标题中连字符词「{word}」首字母应大写",
            confidence="medium",
        ))

    # 检查 hyphen 后各部分
    is_prefix = bool(_PREFIX_RE.match(first_part + "-"))
    for part in rest_parts:
        if not part or not part[0].isalpha():
            continue
        if is_prefix:
            # 前缀后不大写
            if part[0].isupper():
                findings.append(Finding(
                    rule_ref="2.1",
                    quote=word,
                    replace_anchor=word,
                    suggested_value=first_part + "-" + part[0].lower() + part[1:],
                    reason=f"连字符前是前缀（{first_part}-），后面词根不大写，如 Pre-order",
                    confidence="medium",
                ))
        else:
            # 非前缀 hyphen 后大写
            if part[0].islower():
                findings.append(Finding(
                    rule_ref="2.1",
                    quote=word,
                    replace_anchor=word,
                    suggested_value=first_part + "-" + part[0].upper() + part[1:],
                    reason=f"标题中 hyphen 后的词（{part}）首字母应大写，如 Micro-Sized",
                    confidence="medium",
                ))
