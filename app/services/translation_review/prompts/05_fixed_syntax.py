"""§5 固定句法"""
from __future__ import annotations
from app.services.translation_review.prompts.shared import _OUTPUT_SCHEMA, build_block_header, format_block

_RULES = '''
【检查类别】固定句法（§5）

§5.1 无主语句：
  - 标题：不得以动词或 -ing 开头，必须转为被动式（名词化短语或被动语态）
    示例：职业教育提质赋能加快推进 → Promotion of Quality Improvement and Empowerment of Vocational Education Was Accelerated
  - 正文：补主语（一般为省份/城市名）并与被动交替使用，切勿用祈使句或 -ing 开头
§5.2 截至……末 → By the end of…
§5.4 居XXX前五 → ranked among the top five in… / 居XXX第一 → ranked first in…
§5.5 "XX会议"不宜直接作主语 → 改为被动或 The attendees…
§5.6 "XX省/市"不宜直接作主语 → 改为 There were… / The province…
'''

def build_messages(payloads: list[dict]) -> list[dict]:
    if not payloads:
        return []
    sample = payloads[0]
    header = build_block_header(sample.get("file_name", ""), sample.get("source_language", ""), sample.get("target_language", ""))
    block_text = format_block(payloads)
    user_content = f"{header}{_RULES}\n\n以下是原文/译文对：\n\n{block_text}\n\n{_OUTPUT_SCHEMA}"
    return [
        {"role": "system", "content": "你是资深地方志英译审校专家，只输出要求的 JSON 数组。"},
        {"role": "user", "content": user_content},
    ]
