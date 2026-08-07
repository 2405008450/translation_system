from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import DocumentAlignmentPair, DocumentAlignmentUnit, ProofreadingBatch, Project, User
from app.services.language_pairs import normalize_language_code, require_language_pair

from .anchors import build_anchor_blocks
from .dp import AlignPair, align_block
from .llm_boundary import needs_llm_refinement, refine_hard_block
from .parser import AlignUnit, parse_side


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _language_ratio(source_language: str, target_language: str) -> float:
    source_zh = source_language.lower().startswith("zh")
    target_zh = target_language.lower().startswith("zh")
    if source_zh and not target_zh:
        return 1.6
    if target_zh and not source_zh:
        return 0.625
    return 1.0


def preview_document_pair(source_bytes: bytes, source_filename: str, target_bytes: bytes, target_filename: str) -> dict:
    source = parse_side(source_bytes, source_filename, "sentence")
    target = parse_side(target_bytes, target_filename, "sentence")
    return {
        "source": _structure_summary(source, source_filename),
        "target": _structure_summary(target, target_filename),
        "supported_granularities": ["sentence", "paragraph"],
    }


def _structure_summary(units: list[AlignUnit], filename: str) -> dict:
    types: dict[str, int] = {}
    for unit in units:
        types[unit.block_type] = types.get(unit.block_type, 0) + 1
    return {"filename": filename, "unit_count": len(units), "block_types": types, "character_count": sum(unit.char_len for unit in units)}


def _source_cache_path(batch_id: UUID, filename: str) -> Path:
    suffix = Path(filename).suffix.lower() or ".bin"
    path = Path(get_settings().file_storage_dir) / "alignment_sources" / f"{batch_id}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def create_alignment_batch(
    db: Session, *, project: Project, current_user: User,
    source_bytes: bytes, source_filename: str, target_bytes: bytes, target_filename: str,
    source_language: str, target_language: str, granularity: str = "sentence",
    use_llm_for_hard_blocks: bool = False,
) -> ProofreadingBatch:
    if project.workflow_template_id != "proofread":
        raise ValueError("只有“校对”工作流项目可以创建双文档对齐批次。")
    source_language = normalize_language_code(source_language, field_label="源语言") or ""
    target_language = normalize_language_code(target_language, field_label="目标语言") or ""
    require_language_pair(source_language, target_language)
    src_units = parse_side(source_bytes, source_filename, granularity)
    tgt_units = parse_side(target_bytes, target_filename, granularity)
    if not src_units and not tgt_units:
        raise ValueError("两份文档均未解析出可对齐内容。")
    batch = ProofreadingBatch(
        project_id=project.id, created_by_id=current_user.id, filename=source_filename,
        file_hash=hashlib.sha256(source_bytes + b"\0" + target_bytes).hexdigest(),
        source_language=source_language, target_language=target_language,
        batch_kind="document_pair", alignment_status="aligning", status="aligning",
        message="文档已解析，正在生成对齐草稿。",
        config_json=json.dumps({
            "granularity": granularity, "target_filename": target_filename,
            "use_llm_for_hard_blocks": use_llm_for_hard_blocks,
        }, ensure_ascii=False),
    )
    db.add(batch)
    db.flush()
    _source_cache_path(batch.id, source_filename).write_bytes(source_bytes)
    for side, units in (("source", src_units), ("target", tgt_units)):
        db.add_all(DocumentAlignmentUnit(
            batch_id=batch.id, side=side, unit_index=unit.index, text=unit.text,
            para_index=unit.para_index, block_type=unit.block_type, block_index=unit.block_index,
            row_index=unit.row_index, cell_index=unit.cell_index, numbering=unit.numbering,
        ) for unit in units)
    db.commit()
    return batch


def _orm_units(db: Session, batch_id: UUID, side: str) -> list[AlignUnit]:
    rows = db.query(DocumentAlignmentUnit).filter_by(batch_id=batch_id, side=side).order_by(DocumentAlignmentUnit.unit_index).all()
    # 特征由权威提取器重新计算，units 表只保存人工编辑所需快照。
    from app.services.normalizer import normalize_text
    from app.services.number_check.normalizer_total import extract_numbers
    return [AlignUnit(
        index=row.unit_index, text=row.text, norm_text=normalize_text(row.text), para_index=row.para_index,
        block_type=row.block_type, block_index=row.block_index, row_index=row.row_index, cell_index=row.cell_index,
        numbering=row.numbering, char_len=max(1, len(normalize_text(row.text))), numbers=tuple(extract_numbers(row.text)),
        is_heading=row.block_type == "heading",
    ) for row in rows]


async def _compute_pairs(batch: ProofreadingBatch, src: list[AlignUnit], tgt: list[AlignUnit]) -> list[AlignPair]:
    config = json.loads(batch.config_json or "{}")
    ratio = _language_ratio(batch.source_language, batch.target_language)
    result: list[AlignPair] = []
    for src_slice, tgt_slice, anchor_method in build_anchor_blocks(src, tgt):
        block_src, block_tgt = src[src_slice], tgt[tgt_slice]
        pairs = align_block(block_src, block_tgt, lang_ratio=ratio)
        if anchor_method.startswith("anchor_") and len(pairs) == 1:
            pairs[0].method = anchor_method
            pairs[0].confidence = max(pairs[0].confidence, 0.92)
        elif config.get("use_llm_for_hard_blocks") and needs_llm_refinement(pairs):
            pairs = await refine_hard_block(block_src, block_tgt, pairs)
        result.extend(pairs)
    return result


def _join_text(indices: Iterable[int], units: dict[int, AlignUnit]) -> str:
    return "\n".join(units[index].text for index in indices if index in units)


def _store_pairs(
    db: Session, batch: ProofreadingBatch, pairs: list[AlignPair], *,
    locked_signatures: set[tuple[tuple[int, ...], tuple[int, ...]]] | None = None,
) -> None:
    src = {unit.index: unit for unit in _orm_units(db, batch.id, "source")}
    tgt = {unit.index: unit for unit in _orm_units(db, batch.id, "target")}
    db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).delete(synchronize_session=False)
    for order, pair in enumerate(pairs):
        first = src.get(pair.src_indices[0]) if pair.src_indices else tgt.get(pair.tgt_indices[0])
        signature = (tuple(pair.src_indices), tuple(pair.tgt_indices))
        db.add(DocumentAlignmentPair(
            batch_id=batch.id, pair_order=order, src_indices=json.dumps(pair.src_indices), tgt_indices=json.dumps(pair.tgt_indices),
            source_text=_join_text(pair.src_indices, src), target_text=_join_text(pair.tgt_indices, tgt),
            confidence=pair.confidence, confidence_level=pair.confidence_level, method=pair.method,
            features=json.dumps(pair.features, ensure_ascii=False), block_type=first.block_type if first else "paragraph",
            block_index=first.block_index if first else 0, row_index=first.row_index if first else None,
            cell_index=first.cell_index if first else None,
            locked=signature in (locked_signatures or set()),
        ))
    batch.total_segments = sum(bool(pair.src_indices) for pair in pairs)
    batch.skipped_segments = sum(bool(pair.src_indices) and not pair.tgt_indices for pair in pairs)
    batch.alignment_status = "draft"
    batch.status = "draft"
    batch.progress = 100
    batch.message = "对齐草稿已生成，请检查低置信度配对。"
    db.commit()


def run_alignment_batch(batch_id: UUID) -> None:
    with SessionLocal() as db:
        batch = db.get(ProofreadingBatch, batch_id)
        if not batch:
            return
        try:
            src, tgt = _orm_units(db, batch.id, "source"), _orm_units(db, batch.id, "target")
            locked_rows = db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id, locked=True).order_by(DocumentAlignmentPair.pair_order).all()
            locked_signatures = {
                (tuple(json.loads(row.src_indices or "[]")), tuple(json.loads(row.tgt_indices or "[]")))
                for row in locked_rows
            }
            if not locked_rows:
                pairs = asyncio.run(_compute_pairs(batch, src, tgt))
            else:
                pairs = []
                src_cursor = tgt_cursor = 0
                for row in locked_rows:
                    src_indices = json.loads(row.src_indices or "[]")
                    tgt_indices = json.loads(row.tgt_indices or "[]")
                    src_start = min(src_indices) if src_indices else src_cursor
                    tgt_start = min(tgt_indices) if tgt_indices else tgt_cursor
                    if src_start < src_cursor or tgt_start < tgt_cursor:
                        raise ValueError("锁定配对的顺序发生交叉，无法作为重跑锚点。")
                    pairs.extend(asyncio.run(_compute_pairs(batch, src[src_cursor:src_start], tgt[tgt_cursor:tgt_start])))
                    pairs.append(AlignPair(src_indices, tgt_indices, 1.0, method="manual", features={"locked_anchor": True}))
                    src_cursor = max(src_indices) + 1 if src_indices else src_start
                    tgt_cursor = max(tgt_indices) + 1 if tgt_indices else tgt_start
                pairs.extend(asyncio.run(_compute_pairs(batch, src[src_cursor:], tgt[tgt_cursor:])))
            _store_pairs(db, batch, pairs, locked_signatures=locked_signatures)
        except Exception as exc:
            db.rollback()
            batch = db.get(ProofreadingBatch, batch_id)
            if batch:
                batch.alignment_status = "failed"
                batch.status = "failed"
                batch.error_message = str(exc)
                batch.finished_at = _utcnow_naive()
                db.commit()


def serialize_pair(pair: DocumentAlignmentPair) -> dict:
    return {
        "id": str(pair.id), "pair_order": pair.pair_order,
        "src_indices": json.loads(pair.src_indices or "[]"), "tgt_indices": json.loads(pair.tgt_indices or "[]"),
        "source_text": pair.source_text, "target_text": pair.target_text,
        "confidence": pair.confidence, "confidence_level": pair.confidence_level,
        "method": pair.method, "features": json.loads(pair.features or "{}"), "locked": pair.locked,
        "block_type": pair.block_type, "block_index": pair.block_index,
        "row_index": pair.row_index, "cell_index": pair.cell_index,
    }


def validate_pair_integrity(db: Session, batch_id: UUID) -> None:
    pairs = db.query(DocumentAlignmentPair).filter_by(batch_id=batch_id).order_by(DocumentAlignmentPair.pair_order).all()
    src_seen: list[int] = []
    tgt_seen: list[int] = []
    for expected, pair in enumerate(pairs):
        pair.pair_order = expected
        src_seen.extend(json.loads(pair.src_indices or "[]"))
        tgt_seen.extend(json.loads(pair.tgt_indices or "[]"))
    src_all = [row.unit_index for row in db.query(DocumentAlignmentUnit).filter_by(batch_id=batch_id, side="source").all()]
    tgt_all = [row.unit_index for row in db.query(DocumentAlignmentUnit).filter_by(batch_id=batch_id, side="target").all()]
    if sorted(src_seen) != sorted(src_all) or len(src_seen) != len(set(src_seen)):
        raise ValueError("原文单元必须且只能属于一个配对。")
    if sorted(tgt_seen) != sorted(tgt_all) or len(tgt_seen) != len(set(tgt_seen)):
        raise ValueError("译文单元必须且只能属于一个配对。")


def refresh_pair_text(db: Session, pair: DocumentAlignmentPair) -> None:
    src = {unit.index: unit for unit in _orm_units(db, pair.batch_id, "source")}
    tgt = {unit.index: unit for unit in _orm_units(db, pair.batch_id, "target")}
    pair.source_text = _join_text(json.loads(pair.src_indices or "[]"), src)
    pair.target_text = _join_text(json.loads(pair.tgt_indices or "[]"), tgt)
    pair.method = "manual"
    pair.confidence = 1.0 if pair.locked else 0.8
    pair.confidence_level = "high"
