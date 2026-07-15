"""L5-LLM 二次校验：对灰区合并句一次性问 LLM 是否真的是一句。

设计要点：
- 只处理 merge_confidence 落在灰区（默认 0.40~0.70）的合并句
- 一次 LLM 调用批量校验，成本可控
- LLM 返回哪些句子应该拆开，模块负责把它们拆回单实体节点
- 失败兜底：LLM 超时/JSON 错误/关闭 → 不改变原有句子

对外主入口：`verify_and_split_sentences(nodes, ...)`，返回**新的** node 列表。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Iterable, List, Optional, Sequence

from app.config import get_settings
from app.services.adapters.models import BlockNode, NodeType
from app.services.llm_service import request_chat_completion, LLMServiceError


logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are an assistant that checks whether short CAD drawing labels "
    "have been correctly merged into single sentences. "
    "You will receive a JSON array of candidates. Each candidate has an id "
    "and a merged text. Return a JSON array of objects with fields:\n"
    "- id: the same id\n"
    "- keep_merged: true if the merged text is a single coherent sentence/label;\n"
    "  false if it should actually be broken into multiple independent labels.\n"
    "Only reply with the JSON array, no prose. Judge by meaning, not layout."
)


def _select_candidates(nodes: Sequence[BlockNode]) -> List[BlockNode]:
    settings = get_settings()
    low = getattr(settings, "dwg_llm_verify_min_confidence", 0.40)
    high = getattr(settings, "dwg_llm_verify_max_confidence", 0.70)
    cap = getattr(settings, "dwg_llm_verify_max_items", 60)
    picked: List[BlockNode] = []
    for node in nodes:
        meta = node.metadata or {}
        if not meta.get("is_merged"):
            continue
        conf = meta.get("merge_confidence")
        if not isinstance(conf, (int, float)):
            continue
        if low <= conf < high:
            picked.append(node)
        if len(picked) >= cap:
            break
    return picked


def _parse_llm_response(raw: str) -> dict[str, bool]:
    """把 LLM 返回的 JSON 数组解析成 {id: keep_merged} 映射。宽容各种脏输出。"""
    if not raw:
        return {}
    text = raw.strip()
    # 有的模型会用 ```json ... ``` 包裹
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取第一个 [...] 段
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return {}
        try:
            payload = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    if not isinstance(payload, list):
        return {}
    out: dict[str, bool] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        keep = item.get("keep_merged")
        if isinstance(sid, str) and isinstance(keep, bool):
            out[sid] = keep
    return out


def _split_node_back(node: BlockNode) -> List[BlockNode]:
    """把一个合并 BlockNode 按 original_entities 拆回单实体节点。

    original_entities 是 dxf_adapter 在合并时以 JSON 一并存进 metadata 的，
    每项包含 handle/text/x/y/height/width/entity_type/rotation。
    """
    meta = dict(node.metadata or {})
    raw = meta.get("original_entities")
    if not raw:
        # 无原始信息可回滚，只能保留原节点
        return [node]
    try:
        entities = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [node]
    if not isinstance(entities, list) or not entities:
        return [node]

    layer = meta.get("layer", "0")
    scope = meta.get("scope", "")
    sentence_id_prefix = meta.get("sentence_id", "sentence_llm_split")

    out: List[BlockNode] = []
    for idx, ent in enumerate(entities):
        text = (ent.get("text") or "").strip()
        if not text:
            continue
        handle = ent.get("handle") or ""
        entity_type = ent.get("entity_type") or "TEXT"
        new_meta = {
            "entity_type": entity_type,
            "handle": handle,
            "layer": layer,
            "scope": scope,
            "is_merged": False,
            "merged_handles": [handle] if handle else [],
            "merged_count": 1,
            "sentence_id": f"{sentence_id_prefix}_llm_{idx}",
            "merge_confidence": 1.0,
            "llm_split_from": meta.get("sentence_id", ""),
            "primary_x": ent.get("x", 0.0),
            "primary_y": ent.get("y", 0.0),
            "primary_height": ent.get("height", 2.5),
        }
        out.append(BlockNode(
            node_type=NodeType.PARAGRAPH,
            text_content=text,
            metadata=new_meta,
        ))
    return out or [node]


async def _call_llm(candidates: List[BlockNode]) -> dict[str, bool]:
    """向 LLM 提问批量校验，返回 {sentence_id: keep_merged}。失败返回空 dict。"""
    payload = [
        {
            "id": (node.metadata or {}).get("sentence_id", ""),
            "text": node.text_content or "",
        }
        for node in candidates
    ]
    settings = get_settings()
    model_override = getattr(settings, "dwg_llm_verify_model", "") or None
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        result = await request_chat_completion(
            messages=messages,
            model_override=model_override,
            response_format={"type": "json_object"} if model_override else None,
            temperature=0.0,
            settings=settings,
        )
    except LLMServiceError as exc:
        logger.warning("L5-LLM 二次校验失败(服务异常)：%s", exc)
        return {}
    except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都退回不改
        logger.warning("L5-LLM 二次校验失败：%s", exc)
        return {}
    return _parse_llm_response(result.content)


def verify_and_split_sentences(nodes: Sequence[BlockNode]) -> List[BlockNode]:
    """同步入口。开关关闭 / 没有候选 / LLM 失败时原样返回。"""
    settings = get_settings()
    if not getattr(settings, "dwg_llm_verify_enabled", False):
        return list(nodes)
    candidates = _select_candidates(nodes)
    if not candidates:
        return list(nodes)

    logger.info("L5-LLM 二次校验：命中灰区候选 %d 句，发起批量校验", len(candidates))

    try:
        loop = asyncio.new_event_loop()
        try:
            verdict = loop.run_until_complete(_call_llm(candidates))
        finally:
            loop.close()
    except RuntimeError:
        # 已在事件循环内（罕见）：跑不了，直接跳过
        logger.warning("L5-LLM 二次校验跳过：当前上下文有活跃事件循环")
        return list(nodes)

    if not verdict:
        return list(nodes)

    split_count = 0
    total_new = 0
    result: List[BlockNode] = []
    for node in nodes:
        sid = (node.metadata or {}).get("sentence_id", "")
        keep = verdict.get(sid, True)
        if keep:
            result.append(node)
            continue
        split_nodes = _split_node_back(node)
        if len(split_nodes) > 1:
            split_count += 1
            total_new += len(split_nodes)
        result.extend(split_nodes)

    logger.info(
        "L5-LLM 二次校验：LLM 判定拆分 %d 句 → %d 个独立节点",
        split_count, total_new,
    )
    return result
