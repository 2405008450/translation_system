"""§8 原文理解"""
from __future__ import annotations
from app.services.translation_review.prompts.shared import _OUTPUT_SCHEMA, build_block_header, format_block

_RULES = '''
【检查类别】原文理解（§8）

§8.1 地理范围不要扩大化（清远市市区 ≠ 整个清远市）
§8.2 程度副词（较快）不是比较级（faster），注意区分
§8.3 并列成分不要错误合并（新增排放量以及工程削减量 → increases in emissions AND reductions in projects）
§8.4 修饰语的修饰对象不要混淆
§8.5 同字不同义的应联系上下文判断（如"老区"=老革命根据地，不是旧城区）
§8.6 个别不完全理解的词应查证正确含义（如"三资企业"≠ foreign-funded companies）
§8.7 看似普通的词应根据所在领域理解（如"放宽全口径跨境融资模式"需理解金融语境）
§8.8 逻辑关系应联系上下文（积分存储、积分捐赠和兑换：后者是用积分换物，不是积分之间互换）
§8.9 原文看起来有歧义的应查证

注意：只报告明显理解错误；存疑但无法确认时不报告（或 confidence=low）。
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
