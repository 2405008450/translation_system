"""
提示词 — §1 英文符号与中文符号（AI 部分：§1.5 中文间隔号 · 的转换）
程序规则已处理：§1.1 中文标点、§1.2 &//, §1.3 电话括号,
               §1.4 em dash, §1.6 en dash, §1.7 括号嵌套
AI 负责：§1.5 中文间隔号 · 的语义判断与转换
"""
from __future__ import annotations

from app.services.translation_review.prompts.shared import (
    _OUTPUT_SCHEMA,
    build_block_header,
    format_block,
)

_RULES = """
【检查类别】英文符号 — §1.5 中文间隔号（·）的转换

规则 §1.5：中文的间隔号 · 在英文中无类似应用，应根据语义转换：
  §1.5.1 表示并列关系（如"再设计·联万物"）→ 逗号、分号、& 或句号之一（按语义选最合适的）
    示例："再设计·联万物" → "Redesign & Reconnect"
    示例："践行先行示范·共建美丽湾区" → "Establish Pilot Demonstration Area; Co-build Beautiful Greater Bay Area"
  §1.5.2 表示逻辑从属关系（如"中山大学·深圳校区"）→ 去掉·，直接连写或用空格
    示例："中山大学·深圳校区" → "Sun Yat-sen University Shenzhen Campus"
    示例："《变化中的中国·生活因你而火热》" → "Changing China: Life Is Wonderful Because of You"（从属关系用冒号）

注意：只检查译文中是否还保留了"·"或错误处理了"·"所代表的关系。
"""


def build_messages(payloads: list[dict]) -> list[dict]:
    if not payloads:
        return []
    sample = payloads[0]
    header = build_block_header(
        sample.get("file_name", ""),
        sample.get("source_language", ""),
        sample.get("target_language", ""),
    )
    block_text = format_block(payloads)
    user_content = (
        f"{header}"
        f"{_RULES}\n\n"
        "以下是含「·」的原文/译文对，请检查 · 是否已被正确转换：\n\n"
        f"{block_text}\n\n"
        f"{_OUTPUT_SCHEMA}"
    )
    return [
        {"role": "system", "content": "你是资深地方志英译审校专家，只输出要求的 JSON 数组。"},
        {"role": "user", "content": user_content},
    ]
