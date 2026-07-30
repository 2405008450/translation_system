"""
SegmentPayload 装配器

负责：
1. 按文件分组（绝不跨文件混批）
2. 为每个句段生成含上下文/术语/标题判定的 payload dict
3. 注入 seq + sid（AgentRunner 校验用）
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.models import FileRecord
from app.services.translation_review.program_rules.casing import is_likely_heading
from sqlalchemy.orm import Session


def build_payloads_for_file(
    db: Session,
    file_record: FileRecord,
    file_order: int,
    *,
    segment_scope: str = "all",
    needs_context: bool = False,
    needs_terms: bool = False,
    terms_context: str = "",
    seq_offset: int = 0,
) -> list[dict[str, Any]]:
    """
    为一个文件内的所有符合条件的句段构造 payload 列表。
    seq 是跨文件连续编号（由 seq_offset 累加）。
    """
    from app.services.translation_review.service import _load_segments_for_scope
    segments = _load_segments_for_scope(db, file_record, segment_scope)
    payloads: list[dict[str, Any]] = []

    for i, segment in enumerate(segments):
        block_type = getattr(segment, "block_type", "paragraph") or "paragraph"
        target = (segment.target_text or "").strip()
        if not target:
            continue  # 空译文无需检查

        payload: dict[str, Any] = {
            "seq": seq_offset + i,
            "sid": segment.sentence_id,
            "file_record_id": str(file_record.id),
            "file_name": file_record.filename or "",
            "file_order": file_order,
            "source_language": file_record.source_language or "",
            "target_language": file_record.target_language or "",
            "source_text": segment.source_text or "",
            "target_text": target,
            "block_type": block_type,
            "block_index": getattr(segment, "block_index", 0) or 0,
            "row_index": getattr(segment, "row_index", None),
            "cell_index": getattr(segment, "cell_index", None),
            "display_index": getattr(segment, "display_index", -1) or -1,
            "sequence_index": getattr(segment, "sequence_index", -1) or -1,
            "is_heading": is_likely_heading(target, block_type),
        }

        if needs_context and i > 0:
            prev = segments[i - 1]
            payload["prev_text"] = (prev.target_text or "")[:200]
        if needs_context and i < len(segments) - 1:
            nxt = segments[i + 1]
            payload["next_text"] = (nxt.target_text or "")[:200]

        if needs_terms and terms_context:
            payload["terms_context"] = terms_context[:800]

        payloads.append(payload)

    return payloads


def build_terms_context(file_record: FileRecord) -> str:
    """
    从文件绑定的术语中构造上下文字符串（供专有名词 Agent 使用）。
    实际实现中可从 term_bases 加载，这里先返回空（阶段 3 填充）。
    """
    return ""
