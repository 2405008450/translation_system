from __future__ import annotations

import json
from typing import Any

from app.services.llm_service import request_chat_completion
from app.services.translation_review.llm_gate import llm_gate

from .dp import AlignPair
from .parser import AlignUnit


def _validate_index_response(payload: Any, src_count: int, tgt_count: int) -> list[tuple[list[int], list[int]]] | None:
    if isinstance(payload, dict):
        payload = payload.get("pairs")
    if not isinstance(payload, list):
        return None
    used_src: set[int] = set()
    used_tgt: set[int] = set()
    result: list[tuple[list[int], list[int]]] = []
    for item in payload:
        if not isinstance(item, dict) or set(item) - {"s", "t"}:
            return None
        source = item.get("s", [])
        target = item.get("t", [])
        if not isinstance(source, list) or not isinstance(target, list) or not source and not target:
            return None
        if any(type(index) is not int or index < 0 or index >= src_count for index in source):
            return None
        if any(type(index) is not int or index < 0 or index >= tgt_count for index in target):
            return None
        if used_src.intersection(source) or used_tgt.intersection(target):
            return None
        used_src.update(source)
        used_tgt.update(target)
        result.append((source, target))
    # 未返回的下标由程序补成显式漏译/增译，保证完整覆盖。
    result.extend(([index], []) for index in range(src_count) if index not in used_src)
    result.extend(([], [index]) for index in range(tgt_count) if index not in used_tgt)
    return result


async def refine_hard_block(
    src: list[AlignUnit], tgt: list[AlignUnit], fallback: list[AlignPair], *,
    provider: str = "auto", model_override: str | None = None,
) -> list[AlignPair]:
    system = (
        "你是文本对齐工具。只输出 JSON 对象，格式为 {\"pairs\":[{\"s\":[0],\"t\":[0]}]}。"
        "绝对不要输出、复述或改写任何原文或译文；对象中不得出现 s、t、pairs 之外的字段。"
    )
    source_lines = "\n".join(f"S{i}: {unit.text}" for i, unit in enumerate(src))
    target_lines = "\n".join(f"T{i}: {unit.text}" for i, unit in enumerate(tgt))
    user = (
        f"源侧（共 {len(src)} 条）：\n{source_lines}\n\n译侧（共 {len(tgt)} 条）：\n{target_lines}\n\n"
        "每个下标最多出现一次。漏译的 t 为空数组，增译的 s 为空数组。只返回 JSON。"
    )
    for _ in range(2):
        try:
            async with llm_gate():
                completion = await request_chat_completion(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    provider=provider, model_override=model_override,
                    response_format={"type": "json_object"}, temperature=0, allow_fallback=True,
                )
            parsed = _validate_index_response(json.loads(completion.content), len(src), len(tgt))
            if parsed is None:
                continue
            return [AlignPair(
                [src[index].index for index in source], [tgt[index].index for index in target],
                0.6 if source and target else 0.3, method="llm_boundary",
                features={"op": f"{len(source)}-{len(target)}", "llm_indices_only": True},
            ) for source, target in parsed]
        except Exception:  # LLM 永远只是可选优化，任何失败都回落 DP。
            continue
    return fallback


def needs_llm_refinement(pairs: list[AlignPair]) -> bool:
    if not pairs:
        return False
    if any(pair.features.get("op") in {"2-2", "1-3", "3-1"} for pair in pairs):
        return True
    consecutive_low = 0
    for pair in pairs:
        consecutive_low = consecutive_low + 1 if pair.confidence_level == "low" else 0
        if consecutive_low >= 2:
            return True
    return sum(float(pair.features.get("total_cost", 0)) for pair in pairs) / len(pairs) > 4.0
