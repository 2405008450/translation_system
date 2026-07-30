"""§3 数字格式 AI 部分：§3.7.2 含义数字意译、经纬度特例"""
from __future__ import annotations
from app.services.translation_review.prompts.shared import _OUTPUT_SCHEMA, build_block_header, format_block

_RULES = '''
【检查类别】数字格式 — §3.7.2 含义数字 & 经纬度

§3.7.2 双引号中带含义的数字（如"10+1"个区、"9+2"城市群）
  → 必须查证其背后含义后意译，不能直接保留数字。
  示例："10+1"个区指深汕特别合作区 → Shenzhen-Shanwei Special Cooperation Zone（舍弃"10+1"）
  示例："9+2"城市群指大湾区9个内地城市加港澳 → the nine mainland cities and Hong Kong and Macao in the Greater Bay Area

§3.7.1 经纬度格式：秒用两个单引号（''），不是双引号（"）。
  示例：22°26'59'' N to 22°51'49'' N（末尾是两个单引号，不是双引号）

§3.7.4 PM2.5 中的 2.5 应为下标（译文可保持 PM2.5，不要写成 PM 2.5 或 PM-2.5）

只检查上述三个子规则，其他数字格式由程序规则处理。
'''

def build_messages(payloads: list[dict]) -> list[dict]:
    if not payloads:
        return []
    sample = payloads[0]
    header = build_block_header(sample.get("file_name", ""), sample.get("source_language", ""), sample.get("target_language", ""))
    block_text = format_block(payloads)
    user_content = f"{header}{_RULES}\n\n以下是含特殊数字（含义数字 / 经纬度 / PM2.5）的原文/译文对：\n\n{block_text}\n\n{_OUTPUT_SCHEMA}"
    return [
        {"role": "system", "content": "你是资深地方志英译审校专家，只输出要求的 JSON 数组。"},
        {"role": "user", "content": user_content},
    ]
