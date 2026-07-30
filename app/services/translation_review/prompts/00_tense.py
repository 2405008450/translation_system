"""
提示词 — §0 时态
规则原文摘录（§0.1–§0.2）：
  0.1 一般用过去时或过去完成时。但如属客观事实描述，则用一般现在时。
      客观事实示例：深圳市是依山面海、风光秀丽的海滨城市，自然景观和人文景观较为丰富。
  0.2 图片描述也和正文规则保持一致，因图片中的事件/状态已经是过去。
      例：图为老师在授课示范 → In this photo, a teacher gave a demonstration.
"""
from __future__ import annotations

from app.services.translation_review.prompts.shared import (
    _OUTPUT_SCHEMA,
    build_block_header,
    format_block,
)

_RULES = """
【检查类别】时态（§0）

规则：
- §0.1 除客观事实描述（如某城市地理特征、自然现象、固定规律）外，地方志翻译一律用过去时或过去完成时。
- §0.2 图片说明同正文——图片里的事件/状态发生在过去，应用过去时。
- 常见错误：把历史事件、行政行为、活动成果等用一般现在时翻译。

正确示例：
  ✓ 深圳市是依山面海的海滨城市 → Shenzhen is a coastal city...（客观事实，现在时正确）
  ✓ 图为老师在授课示范 → In this photo, a teacher gave a demonstration.（图片说明，过去时）
  ✓ 全省城市内涝点有453个 → There were 453 spots prone to drainage flooding...（统计数据，过去时）

错误示例：
  ✗ 会议决定增加投入 → The meeting decides to increase investment.（应为 decided）
  ✗ 图为市长在讲话 → In this photo, the Mayor is speaking.（应为 was speaking / spoke）

边界：
- 纯引用名言、文件名称内部，时态依原文，不算错误。
- 若无法判断（如标题、纯名词短语），不要报告。
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
        "以下是待检查的原文/译文对：\n\n"
        f"{block_text}\n\n"
        f"{_OUTPUT_SCHEMA}"
    )
    return [
        {"role": "system", "content": "你是资深地方志英译审校专家，只输出要求的 JSON 数组。"},
        {"role": "user", "content": user_content},
    ]
