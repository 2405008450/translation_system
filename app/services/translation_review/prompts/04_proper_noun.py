"""§4 专有名词 AI 部分：机构名、地名、人名格式、同拼音地名等"""
from __future__ import annotations
from app.services.translation_review.prompts.shared import _OUTPUT_SCHEMA, build_block_header, format_block

_RULES = '''
【检查类别】专有名词（§4）

重点检查：
§4.1 人名：姓+名连写，如张三甲 → Zhang Sanjia
§4.2 地址格式：方向词放前（XX东/西/南/北/中 → East/West/South/North/Central XX）；
  "单元"/"室"= Unit；"座"= Tower（无官方译法时）；"层" = /F（如3/F）；路名用拼音不意译；
  大道=Avenue；"中山六路" → Sixth Zhongshan Road（序数词前置）
§4.5 机构名一定要用官方英文名（如中国工商银行 ≠ 中国工商银行股份有限公司）
§4.6 企业职位应查官网，不可自行翻译（如汇丰集团行政总裁=CEO of HSBC Group）
§4.8.1 带"江"的河名：西江 → Xijiang River；涌用拼音如东濠涌 → Donghaochong
§4.8.2 高速公路名需全称（如沈海高速 → Shenyang-Haikou Expressway）
§4.11 同拼音地名：如溪涌和西涌同拼音 → Xichong (溪涌) and Xichong (西涌)

无法网查的情况（联网未启用）：
- 若确定有问题但无法给出正确译法，reason 中写明"建议查证官方译法"，confidence 设为 low，apply_mode 保持 manual。

术语库上下文：
{{TERMS_CONTEXT}}
'''

def build_messages(payloads: list[dict]) -> list[dict]:
    if not payloads:
        return []
    sample = payloads[0]
    terms = sample.get("terms_context", "（无绑定术语库）")
    rules = _RULES.replace("{{TERMS_CONTEXT}}", terms or "（无绑定术语库）")
    header = build_block_header(sample.get("file_name", ""), sample.get("source_language", ""), sample.get("target_language", ""))
    block_text = format_block(payloads)
    user_content = f"{header}{rules}\n\n以下是原文/译文对，请检查专有名词翻译：\n\n{block_text}\n\n{_OUTPUT_SCHEMA}"
    return [
        {"role": "system", "content": "你是资深地方志英译审校专家，只输出要求的 JSON 数组。"},
        {"role": "user", "content": user_content},
    ]
