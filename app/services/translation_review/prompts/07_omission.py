"""§7 避免漏译（AI 部分：并列动词、限定词、逐项列出、长句状语等）"""
from __future__ import annotations
from app.services.translation_review.prompts.shared import _OUTPUT_SCHEMA, build_block_header, format_block

_RULES = '''
【检查类别】避免漏译（§7）

重点：
§7.1 两个或以上并列动词/名词都应译出
  示例：动员部署 → mobilize and organize（两词都要）
  示例：受理群众投诉举报33起 → Thirty-three complaints and reports were received（投诉与举报是两件事）
§7.3 数字前限定词不应省略（超…、不少于…、约…、至少…）
§7.4 活动主题如无官方英文，应意译，不省略
§7.5 长句中的状语成分（根据…培训）不应漏掉
§7.6 并列列举的各条目有自身含义，不应笼统合并为"each grade"等
§7.7 非明显重复的内容不得省译（如原文前半句没有翻译）

注意：仅报告确定漏译的情况；疑似但无把握的，confidence 设为 low。
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
