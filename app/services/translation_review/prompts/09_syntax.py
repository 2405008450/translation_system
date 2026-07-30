"""§9 句法优化"""
from __future__ import annotations
from app.services.translation_review.prompts.shared import _OUTPUT_SCHEMA, build_block_header, format_block

_RULES = '''
【检查类别】句法优化（§9）— 仅为建议（suggestion），不强制修改

§9.1 长句断句：多个无主语动词并列构成的长句，适当拆分，保持意群完整
  示例：优化……，把握……历史机遇，统筹……，加快……
  → Guangzhou optimized…, thus capturing a historic opportunity… Moreover, Guangzhou coordinated… and accelerated.
§9.2 句子不宜以"In 2019,…"等具体日期/年份开头，可调整至句中或句末
  示例：In 2019, 广州开发区优化企业登记建服工作机制……
  → The Guangzhou Economic and Technological Development District, in 2019, optimized…
§9.3 意译时原文意义应足够确定，不要出现意义模糊的"weak spots"等笼统表达

注意：该类别为建议性质，不参与批量应用，仅供审校参考。
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
