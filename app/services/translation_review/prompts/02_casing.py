"""
提示词 — §2 大小写（AI 部分：§2.6 双引号内大小写的三分支判定）
程序规则已处理：标题实词/介词大小写, Party, dynasty, 冒号后, hyphen
AI 负责：§2.6 双引号内的大小写（三分支：专名 / 概括性简写 / 普通引用）
"""
from __future__ import annotations

from app.services.translation_review.prompts.shared import (
    _OUTPUT_SCHEMA,
    build_block_header,
    format_block,
)

_RULES = '''
【检查类别】大小写 — §2.6 双引号内的大小写

规则：
§2.6.1 双引号内是明显的专名（如"深圳生态环境保护高峰论坛"），应实词首字母大写。
§2.6.2 概括性简写（如"一区两核多园"）分两种：
  a. 可直译且读者能看懂 → 保留双引号，不大写（如"one zone, two cores and multiple parks"）
  b. 无法直译 → 查证后意译，不保留双引号（如"双随机、一公开"监管机制 →
     … regulatory mechanism, where both inspectors and inspected targets were randomly selected…）
§2.6.3 非专名、非概括性简写的普通引用（如"大国土""大资源"）→ 不大写，保留双引号
§2.6.4 原文明显误用双引号 → 译文删除双引号（如第五届"广州合唱节" → the Fifth Guangzhou Choral Festival）

正确示例：
  ✓ "深圳读书月" → "Shenzhen Reading Month"（专名，大写）
  ✓ 形成"一区两核多园"发展新布局 → …forming a new development layout of "one zone, two cores and multiple parks"（可直译，不大写）
  ✓ "大国土""大资源" → "large land" and "large resources"（普通引用，不大写）
  ✓ 第五届"广州合唱节" → the Fifth Guangzhou Choral Festival（误用引号，删除）

注意：
- 仅检查双引号内的大小写问题，不报告其他类别。
- 如无把握区分哪一类，confidence 设为 low，不要强行判定。
'''


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
        "以下是含双引号的原文/译文对，请检查双引号内的大小写是否符合规则：\n\n"
        f"{block_text}\n\n"
        f"{_OUTPUT_SCHEMA}"
    )
    return [
        {"role": "system", "content": "你是资深地方志英译审校专家，只输出要求的 JSON 数组。"},
        {"role": "user", "content": user_content},
    ]
