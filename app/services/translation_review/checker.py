"""
翻译内容校对 — LLM 批次检查器

把规则文本 + 一批句段（50条）发给 LLM，让它：
1. 按规则逐条检查原文/译文
2. 自行归纳违反的规则类别（category）
3. 返回结构化 findings

输出 schema（每条句段一项，有问题时 findings 非空）：
[
  {
    "seq": 0,
    "sid": "seg-000001",
    "has_issue": true,
    "findings": [
      {
        "category": "数字格式",          // LLM 自行归纳的类别名称
        "rule_ref": "3.1",               // 规则编号（如有）
        "quote": "原样引用译文片段",
        "replace_anchor": "精确锚点",
        "suggested_value": "建议替换值",
        "suggested_target_text": "",    // full-replace 时填整句
        "reason": "违规原因（简洁）",
        "severity": "error|warning|suggestion",
        "confidence": "high|medium|low"
      }
    ]
  }
]
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models import Segment
from app.services.llm_service import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseValidationError,
    request_chat_completion,
)
from app.services.translation_review.llm_gate import llm_gate

logger = logging.getLogger(__name__)

_NO_ISSUE_HINTS = [
    "no error", "no issue", "没有发现", "未发现", "无错误", "all correct", "符合规范",
]

_SYSTEM_PROMPT = "你是资深翻译审校专家。请严格按照用户给出的翻译规则检查译文，只输出要求的 JSON 数组，不输出其他内容。"

_OUTPUT_SCHEMA = """
请严格按以下 JSON 数组格式输出，长度必须等于输入条数，每项对应同序号条目：
[
  {
    "seq": 0,
    "sid": "<原样回传输入中的 sid>",
    "has_issue": false,
    "findings": []
  },
  {
    "seq": 1,
    "sid": "<原样回传输入中的 sid>",
    "has_issue": true,
    "findings": [
      {
        "category": "类别名称（由你根据规则内容自行归纳，如「数字格式」「大小写」「专有名词」等）",
        "rule_ref": "对应规则编号（如 3.1，若规则无编号则留空）",
        "quote": "译文中有问题的精确片段（必须是译文原文子串，不得修改）",
        "replace_anchor": "可直接替换的精确锚点（必须是译文原文子串；无法精确定位时留空）",
        "suggested_value": "锚点对应的替换值（锚点模式时使用；无法给出时留空）",
        "suggested_target_text": "完整译文改写建议（需要整句改写时填写；锚点模式留空）",
        "reason": "违反规则的简洁理由（50字以内）",
        "severity": "error",
        "confidence": "high"
      }
    ]
  }
]

重要约束：
1. seq 和 sid 必须原样回传，不得改动。
2. quote 和 replace_anchor 必须是译文的原样片段，禁止修改任何字符。
3. 每条译文可能有多个问题，全部列在 findings 中。
4. 若无问题，findings 为空数组，has_issue 为 false。
5. severity 取 error / warning / suggestion 之一。
6. confidence 取 high / medium / low 之一。
"""


def _build_user_prompt(rules_text: str, batch: list[dict]) -> str:
    rules_section = f"【翻译规则】\n{rules_text.strip()}\n\n"
    items_section = "【待检查的原文/译文对】\n"
    for item in batch:
        items_section += (
            f"[{item['seq']}] <sid={item['sid']}>\n"
            f"  原文：{item['source_text']}\n"
            f"  译文：{item['target_text']}\n\n"
        )
    return rules_section + items_section + _OUTPUT_SCHEMA


def _safe_parse(content: str) -> list[dict[str, Any]]:
    if not content or not content.strip():
        return []
    stripped_lower = content.strip().lower()
    if any(hint in stripped_lower for hint in _NO_ISSUE_HINTS) and "{" not in content:
        return []
    try:
        cleaned = re.sub(r"```json|```", "", content).strip()
        m = re.search(r"\[.*\]", cleaned, re.S)
        candidate = m.group() if m else cleaned
        parsed = json.loads(candidate)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except Exception:  # noqa: BLE001
        pass
    return []


async def run_llm_check_batch(
    *,
    rules_text: str,
    segments: list[Segment],
    provider: str = "auto",
    model: str | None = None,
) -> list[dict[str, Any]]:
    """
    把 segments 发给 LLM，返回所有 findings（已展开为每条一个 dict）。
    findings 格式：{sid, category, rule_ref, quote, replace_anchor,
                     suggested_value, suggested_target_text, reason,
                     severity, confidence}
    """
    if not segments:
        return []

    # 构造批次 payload
    batch = [
        {
            "seq": i,
            "sid": seg.sentence_id,
            "source_text": (seg.source_text or "")[:600],
            "target_text": (seg.target_text or "")[:600],
        }
        for i, seg in enumerate(segments)
        if (seg.target_text or "").strip()  # 空译文跳过
    ]
    if not batch:
        return []

    user_prompt = _build_user_prompt(rules_text, batch)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": user_prompt},
    ]

    # 建立 seq → sid 映射用于校验
    seq_to_sid: dict[int, str] = {item["seq"]: item["sid"] for item in batch}

    # 最多重试一次
    raw_list: list[dict] = []
    for attempt in range(2):
        try:
            async with llm_gate():
                completion = await request_chat_completion(
                    messages=messages,
                    provider=provider,
                    model_override=model,
                    temperature=0,
                )
            raw_list = _safe_parse(completion.content)
            if raw_list:
                break
        except (LLMConfigurationError, LLMRequestError, LLMResponseValidationError) as exc:
            logger.warning("translation_review checker attempt %d failed: %s", attempt + 1, exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("translation_review checker unexpected error: %s", exc)
            break

    if not raw_list:
        return []

    # 展开 findings，校验 seq/sid，过滤无问题条目
    findings: list[dict[str, Any]] = []
    for entry in raw_list:
        if not isinstance(entry, dict) or not entry.get("has_issue"):
            continue
        seq = entry.get("seq")
        sid = str(entry.get("sid", ""))
        if seq is None:
            continue
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            continue
        # sid 校验
        expected_sid = seq_to_sid.get(seq)
        if expected_sid is None or str(expected_sid) != sid:
            logger.warning(
                "translation_review checker: seq=%s sid mismatch expected=%s got=%s, dropping",
                seq, expected_sid, sid,
            )
            continue
        for f in (entry.get("findings") or []):
            if not isinstance(f, dict):
                continue
            findings.append({
                "sid": sid,
                "category": (f.get("category") or "其他")[:40],
                "rule_ref": (f.get("rule_ref") or "")[:20],
                "quote": (f.get("quote") or "")[:500],
                "replace_anchor": (f.get("replace_anchor") or "")[:500],
                "suggested_value": (f.get("suggested_value") or "")[:2000],
                "suggested_target_text": (f.get("suggested_target_text") or "")[:4000],
                "reason": (f.get("reason") or "")[:1000],
                "severity": f.get("severity", "warning"),
                "confidence": f.get("confidence", "medium"),
            })

    return findings
