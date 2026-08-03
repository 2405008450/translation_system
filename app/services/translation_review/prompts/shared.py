"""
共用提示词工具函数
"""
from __future__ import annotations

import json


_OUTPUT_SCHEMA = """
请严格按照以下 JSON 数组格式输出，长度必须等于输入条数，每项对应同序号条目：
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
        "rule_ref": "X.Y",
        "quote": "译文中有问题的精确片段（必须是译文原文子串，不得修改）",
        "replace_anchor": "可直接替换的精确锚点（必须是译文原文子串，可与 quote 相同；无法精确定位时留空）",
        "suggested_value": "锚点对应的替换值（仅 anchor 模式使用）",
        "suggested_target_text": "完整译文改写建议（仅 full 模式使用，如无则留空）",
        "reason": "违反的规则编号与具体理由（限 100 字内）",
        "confidence": "high"
      }
    ]
  }
]

重要约束：
1. seq 和 sid 必须原样回传，不得改动。
2. quote 和 replace_anchor 必须是译文的原样片段，禁止增删任何字符（含空格标点）。
3. 只检查本次指定的类别，其他类别的问题不要报告。
4. 每个 finding 的 confidence 取 high / medium / low。
5. 若无问题，findings 为空数组，has_issue 为 false。
6. 一条译文可能有多个问题，全部列出。
"""


def build_block_header(file_name: str, source_language: str, target_language: str) -> str:
    src = source_language or "中文"
    tgt = target_language or "英文"
    return f"【本批文件】{file_name}  源语言：{src} → 目标语言：{tgt}\n\n"


def format_payload_item(p: dict, index: int) -> str:
    ctx = ""
    if p.get("prev_text"):
        ctx += f"  上文: {p['prev_text']}\n"
    if p.get("next_text"):
        ctx += f"  下文: {p['next_text']}\n"
    terms = p.get("terms_context", "")
    term_line = f"  术语: {terms}\n" if terms else ""
    heading_flag = "<heading=true>" if p.get("is_heading") else "<heading=false>"
    return (
        f"[{index}] <sid={p['sid']}> {heading_flag}\n"
        f"  原文: {p.get('source_text', '')}\n"
        f"  译文: {p.get('target_text', '')}\n"
        f"{ctx}"
        f"{term_line}"
    )


def format_block(payloads: list[dict]) -> str:
    return "\n".join(format_payload_item(p, p["seq"]) for p in payloads)
