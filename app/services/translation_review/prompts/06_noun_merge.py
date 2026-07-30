"""§6 相同格式名词的合并"""
from __future__ import annotations
from app.services.translation_review.prompts.shared import _OUTPUT_SCHEMA, build_block_header, format_block

_RULES = '''
【检查类别】相同格式名词的合并（§6）

规则：并列的同类专名应合并，共享一个小写的中心词复数。
示例：
  广深、机荷、沿江、南光、龙大 5 条高速公路
  → Guangzhou-Shenzhen, Airport-He'ao Village, Guangzhou-Shenzhen Yanjiang, Nanshan-Guangming and Longhua-Dalingshan expressways
  （expressways 小写）

  西江和北江 → Xijiang and Beijiang rivers（rivers 小写）

注意：中心词必须小写（expressways, rivers, districts 等），不大写。
仅报告未合并或中心词大写的情况。
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
