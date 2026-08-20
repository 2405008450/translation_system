from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import DocumentAlignmentPair, DocumentAlignmentUnit, ProofreadingBatch, Project, User
from app.services.language_pairs import normalize_language_code, require_language_pair
from app.services.sentence_splitter import looks_like_numbered_heading

from .anchors import build_anchor_blocks, build_order_blocks
from .dp import AlignPair, BoundaryKey, SemanticSimilarity, _within_single_key, align_block
from .features import crosses_heading_boundary, has_structure_conflict
from .llm_boundary import needs_llm_refinement, refine_hard_block
from .hierarchical import (
    build_hierarchical_seed_pairs, repair_adjacent_bilingual_gaps,
    partition_running_matter, restore_running_matter_gaps,
)
from .parser import AlignUnit, assign_table_boundary_keys, parse_side
from .semantic import build_semantic_scorer

logger = logging.getLogger(__name__)


class AlignmentCanceled(Exception):
    """用户主动终止双文档对齐任务。"""


@dataclass(frozen=True)
class AlignmentBatchSnapshot:
    """对齐计算所需的批次快照；计算阶段不得持有 ORM 会话或数据库事务。"""

    id: UUID
    source_language: str
    target_language: str
    config_json: str | None


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _language_ratio(source_language: str, target_language: str) -> float:
    source_zh = source_language.lower().startswith("zh")
    target_zh = target_language.lower().startswith("zh")
    if source_zh and not target_zh:
        # char_len 按 Unicode 字符计数，英文还包含词间空格；中译英常见字符比约 3.5～5.0。
        # 旧值 1.6 更接近“中文字/英文词数”口径，会系统性偏好错误的 2→1 合并。
        return 4.0
    if target_zh and not source_zh:
        return 0.25
    return 1.0


def _calibrate_pair_confidence(
    pair: AlignPair, src: list[AlignUnit], tgt: list[AlignUnit], lang_ratio: float,
    *, use_structure_evidence: bool = True,
) -> None:
    """置信度表示完整边界可信度，而不只是路径稳定或主题相关。"""
    src_map, tgt_map = {unit.index: unit for unit in src}, {unit.index: unit for unit in tgt}
    reasons: list[str] = []
    if not pair.src_indices or not pair.tgt_indices:
        pair.confidence = min(pair.confidence, 0.35)
        return
    source = [src_map[index] for index in pair.src_indices]
    target = [tgt_map[index] for index in pair.tgt_indices]
    source_numbers = {number for unit in source for number in unit.numbers}
    target_numbers = {number for unit in target for number in unit.numbers}
    if source_numbers and target_numbers and not source_numbers.intersection(target_numbers):
        pair.confidence = min(pair.confidence, 0.44)
        reasons.append("数字集合互不相交")
    if use_structure_evidence and has_structure_conflict(source, target):
        pair.confidence = min(pair.confidence, 0.44)
        reasons.append("父级结构不一致")
    source_length = sum(unit.char_len for unit in source)
    target_length = sum(unit.char_len for unit in target)
    actual_ratio = target_length / max(1, source_length)
    if actual_ratio < lang_ratio * 0.25 or actual_ratio > lang_ratio * 2.5:
        pair.confidence = min(pair.confidence, 0.44)
        reasons.append("两侧长度显示可能串入或遗漏内容")
    semantic_score = pair.features.get("semantic_similarity")
    if isinstance(semantic_score, (int, float)):
        if semantic_score < 0.58:
            pair.confidence = min(pair.confidence, 0.44)
            reasons.append("语义证据不足")
        elif semantic_score < 0.68:
            pair.confidence = min(pair.confidence, 0.7)
            reasons.append("语义证据偏弱")
    if len(pair.src_indices) != 1 or len(pair.tgt_indices) != 1:
        pair.confidence = min(pair.confidence, 0.7)
        pair.features["boundary_granularity"] = "coarse"
        reasons.append("多单元范围匹配，句界未精确确认")
    if pair.method in {"semantic_gap_pair", "semantic_gap_repair", "llm_boundary", "llm_full_review"}:
        pair.confidence = min(pair.confidence, 0.7)
    if reasons:
        pair.features["confidence_reason"] = "；".join(dict.fromkeys(reasons))


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
    table_count = len({
        unit.block_index for unit in units if unit.block_type == "table_cell"
    })
    paragraph_count = len({
        (unit.block_type, unit.block_index) for unit in units
        if unit.block_type != "table_cell"
    })
    return {
        "filename": filename,
        "unit_count": len(units),
        "block_types": types,
        "character_count": sum(unit.char_len for unit in units),
        "paragraph_count": paragraph_count,
        "table_count": table_count,
    }


def _source_cache_path(batch_id: UUID, filename: str) -> Path:
    suffix = Path(filename).suffix.lower() or ".bin"
    path = Path(get_settings().file_storage_dir) / "alignment_sources" / f"{batch_id}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def target_cache_path(batch_id: UUID, filename: str, *, create_parent: bool = True) -> Path:
    """返回双文档批次的目标文档原件路径。

    源文档沿用历史文件名，目标文档增加明确后缀，避免同扩展名时互相覆盖。
    """
    suffix = Path(filename).suffix.lower() or ".bin"
    path = Path(get_settings().file_storage_dir) / "alignment_sources" / f"{batch_id}_target{suffix}"
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def create_alignment_batch(
    db: Session, *, project: Project, current_user: User,
    source_bytes: bytes, source_filename: str, target_bytes: bytes, target_filename: str,
    source_language: str, target_language: str, granularity: str = "sentence",
    use_llm_for_hard_blocks: bool = False, full_review: bool = True,
    alignment_strategy: str = "order_first",
) -> ProofreadingBatch:
    if project.workflow_template_id != "proofread":
        raise ValueError("只有“校对”工作流项目可以创建双文档对齐批次。")
    source_language = normalize_language_code(source_language, field_label="源语言") or ""
    target_language = normalize_language_code(target_language, field_label="目标语言") or ""
    require_language_pair(source_language, target_language)
    if alignment_strategy not in {"hierarchical_llm", "order_first", "structure_aware"}:
        raise ValueError("不支持的对齐策略。")
    src_units = parse_side(source_bytes, source_filename, granularity)
    tgt_units = parse_side(target_bytes, target_filename, granularity)
    if not src_units and not tgt_units:
        raise ValueError("两份文档均未解析出可对齐内容。")
    batch = ProofreadingBatch(
        project_id=project.id, created_by_id=current_user.id, filename=source_filename,
        file_hash=hashlib.sha256(source_bytes + b"\0" + target_bytes).hexdigest(),
        source_language=source_language, target_language=target_language,
        batch_kind="document_pair", alignment_status="aligning", workflow_stage="import", status="aligning",
        message="文档已解析，正在生成对齐草稿。",
        config_json=json.dumps({
            "granularity": granularity, "target_filename": target_filename,
            "use_llm_for_hard_blocks": use_llm_for_hard_blocks,
            "alignment_strategy": alignment_strategy,
            # 双文档入口默认使用已经通过超长表格文档验证的 V6 全量复核链路。
            # 这是批次级快照，避免部署环境的全局开关改变历史批次行为。
            "full_review": full_review,
            "full_review_model": get_settings().alignment_llm_full_review_model,
        }, ensure_ascii=False),
    )
    db.add(batch)
    db.flush()
    _source_cache_path(batch.id, source_filename).write_bytes(source_bytes)
    # 校对成品必须以译文原件为版式母版。旧实现只保存源文档，导致后续只能保留
    # 原文排版；这里独立保存目标原件，同时不改变既有对齐/双语导出路径。
    target_cache_path(batch.id, target_filename).write_bytes(target_bytes)
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
    units = []
    for row in rows:
        inferred_heading = (
            row.block_type in {"paragraph", "heading"}
            and (row.block_type == "heading" or looks_like_numbered_heading(row.text))
        )
        units.append(AlignUnit(
            index=row.unit_index, text=row.text, norm_text=normalize_text(row.text), para_index=row.para_index,
            block_type="heading" if inferred_heading else row.block_type,
            block_index=row.block_index, row_index=row.row_index, cell_index=row.cell_index,
            numbering=row.numbering, char_len=max(1, len(normalize_text(row.text))),
            numbers=tuple(extract_numbers(row.text)), is_heading=inferred_heading,
        ))
    return assign_table_boundary_keys(units)


AlignmentProgress = Callable[[int, int, str], None]


def _block_boundary_key(
    source: list[AlignUnit], target: list[AlignUnit], *, enabled: bool = True,
) -> tuple[BoundaryKey | None, str]:
    """选择当前窗口的表格硬边界；表格结构严重不对称时退到行级。"""
    if not enabled:
        return None, ""
    source_cells = {unit.cell_key for unit in source if unit.cell_key}
    target_cells = {unit.cell_key for unit in target if unit.cell_key}
    if not source_cells or not target_cells:
        return None, ""
    ratio = min(len(source_cells), len(target_cells)) / max(len(source_cells), len(target_cells))
    scope = "row" if ratio < 0.5 else "cell"

    def key(unit: AlignUnit) -> str:
        if unit.block_type != "table_cell":
            return ""
        return unit.row_key if scope == "row" else unit.cell_key

    return key, scope


async def _compute_hierarchical_pairs(
    batch: ProofreadingBatch, src: list[AlignUnit], tgt: list[AlignUnit], *,
    ratio: float, config: dict, settings: Any,
    progress_callback: AlignmentProgress | None,
) -> list[AlignPair]:
    """文档粗块先对应，再在块内执行 LLM 句段边界复核。"""
    pairs, source_ignored, target_ignored = build_hierarchical_seed_pairs(
        src, tgt, lang_ratio=ratio,
    )
    if progress_callback is not None:
        progress_callback(1, 1, "结构分块预对齐")

    full_review_enabled = bool(config.get("full_review", True))
    if full_review_enabled and pairs:
        pairs = await _review_all_alignment_pairs(
            pairs, src, tgt, ratio,
            model=str(config.get("full_review_model") or getattr(
                settings, "alignment_llm_full_review_model",
                "google/gemini-3.7-flash",
            )),
            max_pairs=max(1, int(getattr(settings, "alignment_llm_full_review_max_pairs", 28))),
            max_chars=max(1000, int(getattr(settings, "alignment_llm_full_review_max_chars", 18000))),
            table_max_pairs=max(1, int(getattr(
                settings, "alignment_llm_full_review_table_max_pairs", 24,
            ))),
            table_max_chars=max(1000, int(getattr(
                settings, "alignment_llm_full_review_table_max_chars", 9000,
            ))),
            overlap_pairs=max(0, int(getattr(
                settings, "alignment_llm_full_review_overlap_pairs", 2,
            ))),
            retry_min_pairs=max(2, int(getattr(
                settings, "alignment_llm_full_review_retry_min_pairs", 4,
            ))),
            max_output_tokens=max(256, int(getattr(
                settings, "alignment_llm_full_review_max_output_tokens", 4096,
            ))),
            concurrency=max(1, int(getattr(settings, "alignment_llm_full_review_concurrency", 4))),
            semantic_similarity=None,
            progress_callback=progress_callback,
            accept_validated_candidate=True,
        )
    pairs = repair_adjacent_bilingual_gaps(pairs, src, tgt)
    return restore_running_matter_gaps(
        pairs, source_ignored, target_ignored,
    )


async def _compute_pairs(
    batch: ProofreadingBatch | AlignmentBatchSnapshot,
    src: list[AlignUnit], tgt: list[AlignUnit],
    progress_callback: AlignmentProgress | None = None,
) -> list[AlignPair]:
    config = json.loads(batch.config_json or "{}")
    ratio = _language_ratio(batch.source_language, batch.target_language)
    settings = get_settings()
    # 历史批次没有该字段时继续走原有顺序策略；新建批次由 API 明确写入新默认值。
    alignment_strategy = str(config.get("alignment_strategy") or "order_first")
    if alignment_strategy == "hierarchical_llm":
        return await _compute_hierarchical_pairs(
            batch, src, tgt, ratio=ratio, config=config, settings=settings,
            progress_callback=progress_callback,
        )
    # 页眉、页脚和独立页码不是正文译文，必须在所有策略进入 DP/LLM 前隔离；
    # 完成后再按原始索引作为明确缺口插回，既保证完整性，也避免开头整体错位。
    src, source_running_matter = partition_running_matter(src)
    tgt, target_running_matter = partition_running_matter(tgt)
    full_review_enabled = bool(config.get(
        "full_review",
        getattr(settings, "alignment_llm_full_review_enabled", False),
    ))
    use_llm = bool(config.get(
        "use_llm_for_hard_blocks",
        settings.alignment_llm_refinement_enabled,
    )) and not full_review_enabled
    table_cell_boundary_enabled = bool(config.get(
        "table_cell_boundary_enabled",
        getattr(settings, "alignment_table_cell_boundary_enabled", True),
    ))
    semantic_scorer = None
    try:
        semantic_scorer = build_semantic_scorer(settings)
    except Exception as exc:  # 语义服务永远不能阻断确定性主路径。
        logger.warning("alignment embedding unavailable; fallback to deterministic DP: %s", exc)
        semantic_scorer = None
    blocks = (
        build_anchor_blocks(src, tgt)
        if alignment_strategy == "structure_aware"
        else build_order_blocks(src, tgt)
    )
    group_size = max(1, settings.alignment_embedding_window_blocks)
    if full_review_enabled:
        # 全量复核最终需要保留全部单元向量；扩大预取组可充分利用 embedding 批量接口，
        # 避免表格被拆成大量小锚点后产生数百次低利用率请求。
        group_size = max(group_size, 256)
    result: list[AlignPair] = []
    for group_start in range(0, len(blocks), group_size):
        group = blocks[group_start:group_start + group_size]
        semantic = None
        if semantic_scorer is not None:
            try:
                group_src = [unit for src_slice, _, _ in group for unit in src[src_slice]]
                group_tgt = [unit for _, tgt_slice, _ in group for unit in tgt[tgt_slice]]
                # 每组窗口独立向量化，避免整本年报同时驻留数万个高维向量。
                await asyncio.to_thread(semantic_scorer.prepare, group_src, group_tgt)
                semantic = semantic_scorer.similarity
            except Exception as exc:
                logger.warning(
                    "alignment embedding failed for one window group; keep deterministic result: %s", exc
                )
        for block_offset, (src_slice, tgt_slice, anchor_method) in enumerate(group):
            block_src, block_tgt = src[src_slice], tgt[tgt_slice]
            boundary_key, boundary_scope = _block_boundary_key(
                block_src, block_tgt, enabled=table_cell_boundary_enabled,
            )
            pairs = align_block(
                block_src, block_tgt, lang_ratio=ratio,
                semantic_similarity=semantic,
                boundary_key=boundary_key,
            )
            if boundary_scope:
                for pair in pairs:
                    pair.features["boundary_scope"] = boundary_scope
            if semantic is None:
                for pair in pairs:
                    pair.features["semantic_fallback"] = True
            if anchor_method.startswith("anchor_") and len(pairs) == 1:
                pairs[0].method = anchor_method
                pairs[0].confidence = max(pairs[0].confidence, 0.92)
            else:
                reverse_semantic = (
                    (lambda reverse_src, reverse_tgt: semantic(reverse_tgt, reverse_src))
                    if semantic is not None else None
                )
                reverse = align_block(
                    block_tgt, block_src, lang_ratio=1.0 / ratio,
                    semantic_similarity=reverse_semantic,
                    boundary_key=boundary_key,
                )
                reverse_signatures = {
                    (tuple(pair.tgt_indices), tuple(pair.src_indices)) for pair in reverse
                }
                for pair in pairs:
                    signature = (tuple(pair.src_indices), tuple(pair.tgt_indices))
                    consistent = signature in reverse_signatures
                    pair.features["bidirectional_consistent"] = consistent
                    if consistent:
                        pair.method = "dp_bidirectional"
                        pair.confidence = min(1.0, pair.confidence + 0.05)
                    else:
                        pair.confidence = min(pair.confidence, 0.44)
                        pair.features["confidence_reason"] = "正向与反向对齐结果不一致"
                if semantic is not None:
                    pairs = _repair_semantic_gaps(
                        pairs, block_src, block_tgt, semantic,
                        boundary_key=boundary_key,
                    )
                pairs = _repair_structural_table_gaps(
                    pairs, block_src, block_tgt, boundary_key=boundary_key,
                )
                if use_llm and needs_llm_refinement(pairs):
                    pairs = await refine_hard_block(
                        block_src, block_tgt, pairs,
                        semantic_similarity=semantic, lang_ratio=ratio,
                        boundary_key=boundary_key,
                    )
            for pair in pairs:
                _calibrate_pair_confidence(
                    pair,
                    block_src,
                    block_tgt,
                    ratio,
                    use_structure_evidence=alignment_strategy == "structure_aware",
                )
            result.extend(pairs)
            if progress_callback is not None:
                progress_callback(
                    min(group_start + block_offset + 1, len(blocks)), len(blocks),
                    (
                        "向量预对齐" if semantic is not None else "程序预对齐"
                    ) if full_review_enabled else (
                        "向量对齐" if semantic is not None else "程序降级对齐"
                    ),
                )
        if semantic_scorer is not None:
            if full_review_enabled:
                # 全量复核需要逐配对重新验收；保留单元向量，释放 DP 产生的大量组合缓存。
                semantic_scorer.clear_derived()
            else:
                semantic_scorer.clear()
    if full_review_enabled and result:
        try:
            result = await _review_all_alignment_pairs(
                result, src, tgt, ratio,
                model=str(config.get("full_review_model") or getattr(
                    settings, "alignment_llm_full_review_model",
                    "google/gemini-3.7-flash",
                )),
                max_pairs=max(1, int(getattr(settings, "alignment_llm_full_review_max_pairs", 28))),
                max_chars=max(1000, int(getattr(settings, "alignment_llm_full_review_max_chars", 18000))),
                table_max_pairs=max(1, int(getattr(
                settings, "alignment_llm_full_review_table_max_pairs", 24,
                ))),
                table_max_chars=max(1000, int(getattr(
                    settings, "alignment_llm_full_review_table_max_chars", 9000,
                ))),
                overlap_pairs=max(0, int(getattr(
                    settings, "alignment_llm_full_review_overlap_pairs", 2,
                ))),
                retry_min_pairs=max(2, int(getattr(
                    settings, "alignment_llm_full_review_retry_min_pairs", 4,
                ))),
                max_output_tokens=max(256, int(getattr(
                    settings, "alignment_llm_full_review_max_output_tokens", 4096,
                ))),
                concurrency=max(1, int(getattr(settings, "alignment_llm_full_review_concurrency", 4))),
                semantic_similarity=(
                    semantic_scorer.similarity if semantic_scorer is not None else None
                ),
                progress_callback=progress_callback,
                table_cell_boundary_enabled=table_cell_boundary_enabled,
            )
        finally:
            if semantic_scorer is not None:
                semantic_scorer.clear()
    elif full_review_enabled and semantic_scorer is not None:
        semantic_scorer.clear()
    return restore_running_matter_gaps(
        result, source_running_matter, target_running_matter,
    )


def _is_safe_review_boundary(pair: AlignPair) -> bool:
    """优先在稳定的一对一结果之后切块，降低跨块错位概率。"""
    return (
        len(pair.src_indices) == len(pair.tgt_indices) == 1
        and pair.confidence_level == "high"
        and not pair.features.get("gap")
    )


def _review_structure_profile(
    pair: AlignPair, src_map: dict[int, AlignUnit], tgt_map: dict[int, AlignUnit],
) -> tuple[bool, tuple[int, ...], tuple[int, ...]]:
    source_tables = tuple(sorted({
        src_map[index].block_index for index in pair.src_indices
        if src_map[index].block_type == "table_cell"
    }))
    target_tables = tuple(sorted({
        tgt_map[index].block_index for index in pair.tgt_indices
        if tgt_map[index].block_type == "table_cell"
    }))
    return bool(source_tables or target_tables), source_tables, target_tables


def _crosses_review_structure(
    left: tuple[bool, tuple[int, ...], tuple[int, ...]],
    right: tuple[bool, tuple[int, ...], tuple[int, ...]],
) -> bool:
    if left[0] != right[0]:
        return True
    if not left[0]:
        return False
    # 单侧 gap 没有另一侧表号，不因此单独切块；已知的表号发生变化才是硬边界。
    return bool(
        left[1] and right[1] and left[1] != right[1]
        or left[2] and right[2] and left[2] != right[2]
    )


def _build_full_review_chunks(
    pairs: list[AlignPair], src: list[AlignUnit], tgt: list[AlignUnit], *,
    max_pairs: int, max_chars: int,
    table_max_pairs: int = 24, table_max_chars: int = 9000,
) -> list[list[AlignPair]]:
    """按候选键值对分块，并尽量把切点移动到附近的稳定一对一锚点。"""
    src_map = {unit.index: unit for unit in src}
    tgt_map = {unit.index: unit for unit in tgt}

    def pair_chars(pair: AlignPair) -> int:
        return sum(src_map[index].char_len for index in pair.src_indices) + sum(
            tgt_map[index].char_len for index in pair.tgt_indices
        )

    chunks: list[list[AlignPair]] = []
    start = 0
    while start < len(pairs):
        end = start
        chars = 0
        start_profile = _review_structure_profile(pairs[start], src_map, tgt_map)
        pair_limit = min(max_pairs, table_max_pairs) if start_profile[0] else max_pairs
        char_limit = min(max_chars, table_max_chars) if start_profile[0] else max_chars
        structure_boundary = False
        while end < len(pairs):
            if end > start and _crosses_review_structure(
                start_profile, _review_structure_profile(pairs[end], src_map, tgt_map),
            ):
                structure_boundary = True
                break
            next_chars = pair_chars(pairs[end])
            if end > start and (end - start >= pair_limit or chars + next_chars > char_limit):
                break
            chars += next_chars
            end += 1
        if end >= len(pairs):
            chunks.append(pairs[start:])
            break

        if structure_boundary:
            chunks.append(pairs[start:end])
            start = end
            continue

        def is_gap(item: AlignPair) -> bool:
            return not item.src_indices or not item.tgt_indices

        while end < len(pairs) and (is_gap(pairs[end - 1]) or is_gap(pairs[end])):
            end += 1
        if end >= len(pairs):
            chunks.append(pairs[start:])
            break

        # 优先向前回退到最近的可靠锚点，绝不把连续 gap 区间从中间截断。
        # 如果前半段没有可靠切点，再向后寻找；允许窗口适度变大以换取完整上下文。
        minimum_end = min(end, start + max(1, max_pairs // 2))
        safe_end = next(
            (index + 1 for index in range(end - 1, minimum_end - 2, -1)
             if _is_safe_review_boundary(pairs[index])),
            None,
        )
        if safe_end is None:
            search_stop = min(len(pairs), end + max_pairs + 1)
            safe_end = next(
                (index + 1 for index in range(end, search_stop)
                 if _is_safe_review_boundary(pairs[index])),
                None,
            )
        if safe_end is not None:
            end = safe_end
        elif end == start:
            end += 1
        chunks.append(pairs[start:end])
        start = end
    return chunks


async def _review_all_alignment_pairs(
    pairs: list[AlignPair], src: list[AlignUnit], tgt: list[AlignUnit], lang_ratio: float, *,
    model: str, max_pairs: int, max_chars: int,
    table_max_pairs: int = 24, table_max_chars: int = 9000,
    overlap_pairs: int = 2, retry_min_pairs: int = 4,
    max_output_tokens: int = 4096,
    concurrency: int = 1,
    semantic_similarity: SemanticSimilarity | None = None,
    progress_callback: AlignmentProgress | None = None,
    accept_validated_candidate: bool = False,
    table_cell_boundary_enabled: bool = True,
) -> list[AlignPair]:
    """使用指定 Gemini 模型全量复核第一、二阶段产生的全部候选配对。"""
    src_map = {unit.index: unit for unit in src}
    tgt_map = {unit.index: unit for unit in tgt}
    chunks = _build_full_review_chunks(
        pairs, src, tgt, max_pairs=max_pairs, max_chars=max_chars,
        table_max_pairs=table_max_pairs, table_max_chars=table_max_chars,
    )
    reviewed_chunks: list[list[AlignPair] | None] = [None] * len(chunks)
    instruction = (
        "这是全量边界复核，不论程序候选置信度高低都要逐项检查。"
        "重点检查上一条末尾是否串入下一条、单侧空缺是否应与前后条目重组，以及编号引用是否错位。"
        "中文原子句是主侧边界；不得把下一中文原子句的译文吸收到当前条目。"
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))
    pair_positions = {id(pair): index for index, pair in enumerate(pairs)}
    retryable_outcomes = {
        "timeout", "request_error", "invalid_json", "invalid_response",
        "unexpected_error",
    }

    def review_context(fallback: list[AlignPair]) -> str:
        start = pair_positions.get(id(fallback[0]), 0)
        end = pair_positions.get(id(fallback[-1]), start)
        neighbors = (
            pairs[max(0, start - overlap_pairs):start]
            + pairs[end + 1:end + 1 + overlap_pairs]
        )
        source_indices = sorted({index for pair in neighbors for index in pair.src_indices})
        target_indices = sorted({index for pair in neighbors for index in pair.tgt_indices})

        def line(prefix: str, unit: AlignUnit) -> str:
            structure = (
                f"table={unit.block_index},row={unit.row_index},cell={unit.cell_index}"
                if unit.block_type == "table_cell"
                else f"type={unit.block_type},block={unit.block_index}"
            )
            return f"{prefix}{unit.index} [{structure}]: {unit.text}"

        if not source_indices and not target_indices:
            return ""
        source_lines = "\n".join(line("CTX-S", src_map[index]) for index in source_indices)
        target_lines = "\n".join(line("CTX-T", tgt_map[index]) for index in target_indices)
        return (
            "\n以下是窗口前后只读上下文，仅用于判断边界；不得在返回 JSON 中引用这些 CTX 下标：\n"
            f"源侧上下文：\n{source_lines or '(无)'}\n"
            f"译侧上下文：\n{target_lines or '(无)'}\n"
        )

    async def review_once(fallback: list[AlignPair]):
        source_indices = sorted({index for pair in fallback for index in pair.src_indices})
        target_indices = sorted({index for pair in fallback for index in pair.tgt_indices})
        block_src = [src_map[index] for index in source_indices]
        block_tgt = [tgt_map[index] for index in target_indices]
        boundary_key, _ = _block_boundary_key(
            block_src, block_tgt, enabled=table_cell_boundary_enabled,
        )
        refinement_outcome: dict[str, str] = {}
        async with semaphore:
            candidate = await refine_hard_block(
                block_src, block_tgt, fallback,
                provider="openrouter", model_override=model,
                semantic_similarity=semantic_similarity, lang_ratio=lang_ratio,
                method="llm_full_review", allow_provider_fallback=False,
                review_instruction=instruction,
                review_context=review_context(fallback),
                max_output_tokens=max_output_tokens,
                refinement_outcome=refinement_outcome,
                accept_validated_candidate=accept_validated_candidate,
                boundary_key=boundary_key,
            )
        if not refinement_outcome:
            refinement_outcome["status"] = (
                "accepted" if candidate is not fallback else "unchanged"
            )
        return candidate, refinement_outcome, block_src, block_tgt

    def mark_reviewed(
        candidate: list[AlignPair], refinement_outcome: dict[str, str],
        block_src: list[AlignUnit], block_tgt: list[AlignUnit], *,
        chunk_index: int, retry_part: str = "", parent_outcome: str = "",
    ) -> list[AlignPair]:
        outcome_status = refinement_outcome["status"]
        accepted = outcome_status == "accepted"
        if outcome_status not in {"accepted", "unchanged"}:
            logger.warning(
                "alignment full review fallback chunk=%s part=%s status=%s detail=%s",
                chunk_index, retry_part or "root", outcome_status,
                refinement_outcome.get("detail", ""),
            )
        for pair in candidate:
            pair.features["llm_full_review_attempted"] = True
            pair.features["llm_full_review_checked"] = outcome_status in {"accepted", "unchanged"}
            pair.features["llm_full_review_chunk"] = chunk_index
            pair.features["llm_full_review_changed"] = accepted
            pair.features["llm_full_review_outcome"] = outcome_status
            if retry_part:
                pair.features["llm_full_review_retry_part"] = retry_part
                pair.features["llm_full_review_retried"] = True
            if parent_outcome:
                pair.features["llm_full_review_parent_outcome"] = parent_outcome
            if refinement_outcome.get("detail"):
                pair.features["llm_full_review_detail"] = refinement_outcome["detail"]
            if refinement_outcome.get("provider"):
                pair.features["llm_full_review_provider"] = refinement_outcome["provider"]
            if refinement_outcome.get("model"):
                pair.features["llm_full_review_model"] = refinement_outcome["model"]
            _calibrate_pair_confidence(pair, block_src, block_tgt, lang_ratio)
        return candidate

    async def review_chunk(chunk_index: int, fallback: list[AlignPair]):
        candidate, outcome, block_src, block_tgt = await review_once(fallback)
        outcome_status = outcome["status"]
        if outcome_status in retryable_outcomes and len(fallback) >= retry_min_pairs:
            midpoint = len(fallback) // 2
            reviewed_parts: list[AlignPair] = []
            for retry_part, sub_fallback in (
                ("a", fallback[:midpoint]), ("b", fallback[midpoint:]),
            ):
                sub_candidate, sub_outcome, sub_src, sub_tgt = await review_once(sub_fallback)
                reviewed_parts.extend(mark_reviewed(
                    sub_candidate, sub_outcome, sub_src, sub_tgt,
                    chunk_index=chunk_index, retry_part=retry_part,
                    parent_outcome=outcome_status,
                ))
            return chunk_index, reviewed_parts
        return chunk_index, mark_reviewed(
            candidate, outcome, block_src, block_tgt, chunk_index=chunk_index,
        )

    tasks = [
        asyncio.create_task(review_chunk(chunk_index, fallback))
        for chunk_index, fallback in enumerate(chunks)
    ]
    completed = 0
    for task in asyncio.as_completed(tasks):
        chunk_index, candidate = await task
        reviewed_chunks[chunk_index] = candidate
        completed += 1
        if progress_callback is not None:
            progress_callback(completed, len(chunks), "LLM 全量复核")
    return [pair for chunk in reviewed_chunks if chunk is not None for pair in chunk]


def _repair_structural_table_gaps(
    pairs: list[AlignPair], src: list[AlignUnit], tgt: list[AlignUnit],
    boundary_key: BoundaryKey | None = None,
) -> list[AlignPair]:
    """吸收同一表格单元格内被 DP 单独留下的标题或标签。

    表格单元格是文档自身提供的强边界；只在 gap 与相邻已配对内容属于同一个
    单元格时合并，不把这条规则扩展到普通段落或跨单元格内容。
    """
    src_map = {unit.index: unit for unit in src}
    tgt_map = {unit.index: unit for unit in tgt}

    def table_parent(unit: AlignUnit) -> tuple[int, int | None, int | None] | None:
        if boundary_key is not None:
            value = boundary_key(unit)
            return value if value else None
        if unit.block_type != "table_cell":
            return None
        return unit.block_index, unit.row_index, unit.cell_index

    result = list(pairs)
    index = 0
    while index < len(result):
        gap = result[index]
        source_gap = bool(gap.src_indices) and not gap.tgt_indices
        target_gap = bool(gap.tgt_indices) and not gap.src_indices
        if not source_gap and not target_gap:
            index += 1
            continue
        gap_units = [src_map[item] for item in gap.src_indices] if source_gap else [tgt_map[item] for item in gap.tgt_indices]
        gap_parents = {table_parent(unit) for unit in gap_units}
        if None in gap_parents or len(gap_parents) != 1:
            index += 1
            continue
        candidates: list[int] = []
        for neighbor_index in (index - 1, index + 1):
            if not 0 <= neighbor_index < len(result):
                continue
            neighbor = result[neighbor_index]
            if not neighbor.src_indices or not neighbor.tgt_indices:
                continue
            same_side = [src_map[item] for item in neighbor.src_indices] if source_gap else [tgt_map[item] for item in neighbor.tgt_indices]
            if {table_parent(unit) for unit in same_side} == gap_parents:
                candidates.append(neighbor_index)
        if not candidates:
            index += 1
            continue
        # 优先吸收到后继配对：表格标签通常位于其值或正文之前。
        neighbor_index = index + 1 if index + 1 in candidates else candidates[0]
        neighbor = result[neighbor_index]
        if source_gap:
            neighbor.src_indices = sorted(gap.src_indices + neighbor.src_indices)
        else:
            neighbor.tgt_indices = sorted(gap.tgt_indices + neighbor.tgt_indices)
        neighbor.method = "structural_table_gap_repair"
        neighbor.features.update({
            "structural_table_gap_repair": True,
            "absorbed_src_indices": gap.src_indices,
            "absorbed_tgt_indices": gap.tgt_indices,
        })
        result.pop(index)
        index = max(0, index - 1)
    return result


def _repair_semantic_gaps(
    pairs: list[AlignPair], src: list[AlignUnit], tgt: list[AlignUnit], semantic_similarity,
    *, threshold: float = 0.76, boundary_key: BoundaryKey | None = None,
) -> list[AlignPair]:
    """只在相邻范围吸收高语义相似的孤立 gap，不允许跨过任何已有配对。"""
    src_map = {unit.index: unit for unit in src}
    tgt_map = {unit.index: unit for unit in tgt}
    result = list(pairs)

    # DP 可能把本应互译的一组内容表示成连续的“仅译文 gap + 仅原文 gap”。
    # 优先把这种互补缺口直接组成多对多配对，目标是先恢复内容覆盖和顺序。
    index = 0
    while index < len(result):
        if result[index].src_indices and result[index].tgt_indices:
            index += 1
            continue
        run_end = index
        run_src: list[int] = []
        run_tgt: list[int] = []
        while run_end < len(result) and not (
            result[run_end].src_indices and result[run_end].tgt_indices
        ):
            run_src.extend(result[run_end].src_indices)
            run_tgt.extend(result[run_end].tgt_indices)
            run_end += 1
        run_source_units = [src_map[item] for item in run_src]
        run_target_units = [tgt_map[item] for item in run_tgt]
        if (
            run_src and run_tgt and len(run_src) <= 3 and len(run_tgt) <= 3
            and _within_single_key(run_source_units, boundary_key)
            and _within_single_key(run_target_units, boundary_key)
            and not crosses_heading_boundary(run_source_units)
            and not crosses_heading_boundary(run_target_units)
        ):
            score = semantic_similarity(
                run_source_units,
                run_target_units,
            )
            if score is not None and score >= threshold:
                result[index:run_end] = [AlignPair(
                    sorted(run_src), sorted(run_tgt), min(0.9, score),
                    method="semantic_gap_pair",
                    features={
                        "semantic_gap_pair": True,
                        "semantic_similarity": round(score, 4),
                        "op": f"{len(run_src)}-{len(run_tgt)}",
                    },
                )]
                index += 1
                continue
        index = run_end

    index = 0
    while index < len(result):
        gap = result[index]
        is_source_gap = bool(gap.src_indices) and not gap.tgt_indices
        is_target_gap = bool(gap.tgt_indices) and not gap.src_indices
        if not is_source_gap and not is_target_gap:
            index += 1
            continue
        candidates: list[tuple[float, int]] = []
        for neighbor_index in (index - 1, index + 1):
            if neighbor_index < 0 or neighbor_index >= len(result):
                continue
            neighbor = result[neighbor_index]
            if not neighbor.src_indices or not neighbor.tgt_indices:
                continue
            if is_source_gap and len(neighbor.src_indices) >= 3:
                continue
            if is_target_gap and len(neighbor.tgt_indices) >= 3:
                continue
            combined_source = gap.src_indices + neighbor.src_indices
            combined_target = gap.tgt_indices + neighbor.tgt_indices
            if (
                not _within_single_key([src_map[item] for item in combined_source], boundary_key)
                or not _within_single_key([tgt_map[item] for item in combined_target], boundary_key)
                or crosses_heading_boundary([src_map[item] for item in combined_source])
                or crosses_heading_boundary([tgt_map[item] for item in combined_target])
            ):
                continue
            source_units = [src_map[item] for item in (gap.src_indices if is_source_gap else neighbor.src_indices)]
            target_units = [tgt_map[item] for item in (neighbor.tgt_indices if is_source_gap else gap.tgt_indices)]
            score = semantic_similarity(source_units, target_units)
            if score is not None:
                candidates.append((score, neighbor_index))
        if not candidates:
            index += 1
            continue
        score, neighbor_index = max(candidates)
        if score < threshold:
            index += 1
            continue
        neighbor = result[neighbor_index]
        if is_source_gap:
            neighbor.src_indices = sorted(gap.src_indices + neighbor.src_indices)
        else:
            neighbor.tgt_indices = sorted(gap.tgt_indices + neighbor.tgt_indices)
        neighbor.method = "semantic_gap_repair"
        neighbor.confidence = max(neighbor.confidence, min(0.9, score))
        neighbor.features.update({
            "semantic_gap_repair": True,
            "absorbed_gap_similarity": round(score, 4),
            "absorbed_src_indices": gap.src_indices,
            "absorbed_tgt_indices": gap.tgt_indices,
        })
        result.pop(index)
        if neighbor_index > index:
            neighbor_index -= 1
        index = max(0, neighbor_index - 1)
    return result


def _join_text(indices: Iterable[int], units: dict[int, AlignUnit]) -> str:
    return "\n".join(units[index].text for index in indices if index in units)


def _set_pair_cell_features(
    features: dict[str, Any], src_indices: Iterable[int], tgt_indices: Iterable[int],
    src: dict[int, AlignUnit], tgt: dict[int, AlignUnit],
) -> None:
    source_keys = list(dict.fromkeys(
        src[index].cell_key for index in src_indices if index in src and src[index].cell_key
    ))
    target_keys = list(dict.fromkeys(
        tgt[index].cell_key for index in tgt_indices if index in tgt and tgt[index].cell_key
    ))
    features["source_cell_keys"] = source_keys
    features["target_cell_keys"] = target_keys
    features["cross_cell"] = len(source_keys) > 1 or len(target_keys) > 1


def _llm_review_chunk_outcomes(pairs: list[AlignPair]) -> dict[str, int]:
    """按 chunk 去重统计全量复核结果，避免一个分块内多行被重复计数。"""
    chunk_outcomes: dict[tuple[int, str], str] = {}
    for pair in pairs:
        chunk = pair.features.get("llm_full_review_chunk")
        outcome = pair.features.get("llm_full_review_outcome")
        if isinstance(chunk, int) and isinstance(outcome, str) and outcome:
            retry_part = str(pair.features.get("llm_full_review_retry_part") or "")
            chunk_outcomes[(chunk, retry_part)] = outcome
    counts: dict[str, int] = {}
    for outcome in chunk_outcomes.values():
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


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
        _set_pair_cell_features(pair.features, pair.src_indices, pair.tgt_indices, src, tgt)
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
    semantic_pairs = sum(
        pair.features.get("semantic_similarity") is not None
        or pair.features.get("absorbed_gap_similarity") is not None
        for pair in pairs
    )
    llm_pairs = sum(pair.method in {"llm_boundary", "llm_full_review"} for pair in pairs)
    fallback_pairs = sum(bool(pair.features.get("semantic_fallback")) for pair in pairs)
    review_outcomes = _llm_review_chunk_outcomes(pairs)
    accepted_chunks = review_outcomes.get("accepted", 0)
    unchanged_chunks = review_outcomes.get("unchanged", 0)
    failed_chunks = sum(
        count for status, count in review_outcomes.items()
        if status not in {"accepted", "unchanged"}
    )
    review_message = ""
    if review_outcomes:
        review_message = (
            f" LLM 分块：接受 {accepted_chunks}，未调整 {unchanged_chunks}，"
            f"失败或拒绝 {failed_chunks}。"
        )
    config = json.loads(batch.config_json or "{}")
    hierarchical = config.get("alignment_strategy") == "hierarchical_llm"
    if hierarchical:
        batch.message = (
            f"分块对齐完成：隔离页眉页脚/页码 "
            f"{sum(pair.method == 'ignored_running_matter' for pair in pairs)} 组，"
            f"LLM 复核 {llm_pairs} 个配对。{review_message}"
        )
    elif fallback_pairs:
        batch.message = (
            f"对齐完成，但有 {fallback_pairs} 个配对因向量服务失败而使用程序降级；"
            f"向量配对 {semantic_pairs} 个，LLM 复核 {llm_pairs} 个。{review_message}"
        )
    else:
        batch.message = (
            f"向量对齐完成：语义配对 {semantic_pairs} 个，LLM 复核 {llm_pairs} 个。"
            f"{review_message}"
        )
    db.commit()


def _write_alignment_progress(batch_id: UUID, completed: int, total: int, phase: str) -> None:
    if phase == "LLM 全量复核":
        progress = min(95, 70 + round(completed * 25 / max(1, total)))
    elif phase in {"向量预对齐", "程序预对齐", "结构分块预对齐"}:
        progress = min(70, max(1, round(completed * 70 / max(1, total))))
    else:
        progress = min(95, max(1, round(completed * 95 / max(1, total))))
    with SessionLocal() as progress_db:
        current = progress_db.get(ProofreadingBatch, batch_id)
        if not current:
            raise AlignmentCanceled("双文档对齐批次已不存在。")
        if current.cancel_requested or current.status in {"canceling", "canceled"}:
            raise AlignmentCanceled("用户已终止双文档对齐任务。")
        if current.alignment_status != "aligning":
            return
        current.progress = progress
        current.message = f"{phase}：{completed}/{total} 个窗口（{progress}%）"
        progress_db.commit()


def _load_alignment_work(
    batch_id: UUID,
) -> tuple[
    AlignmentBatchSnapshot,
    list[AlignUnit],
    list[AlignUnit],
    list[tuple[list[int], list[int]]],
    set[tuple[tuple[int, ...], tuple[int, ...]]],
] | None:
    """用短事务读取对齐输入，返回后连接已归还连接池。"""

    with SessionLocal() as db:
        batch = db.get(ProofreadingBatch, batch_id)
        if not batch:
            return None
        src = _orm_units(db, batch.id, "source")
        tgt = _orm_units(db, batch.id, "target")
        locked_rows = (
            db.query(DocumentAlignmentPair)
            .filter_by(batch_id=batch.id, locked=True)
            .order_by(DocumentAlignmentPair.pair_order)
            .all()
        )
        locked_anchors = [
            (
                list(json.loads(row.src_indices or "[]")),
                list(json.loads(row.tgt_indices or "[]")),
            )
            for row in locked_rows
        ]
        locked_signatures = {
            (tuple(src_indices), tuple(tgt_indices))
            for src_indices, tgt_indices in locked_anchors
        }
        snapshot = AlignmentBatchSnapshot(
            id=batch.id,
            source_language=batch.source_language,
            target_language=batch.target_language,
            config_json=batch.config_json,
        )
    return snapshot, src, tgt, locked_anchors, locked_signatures


def _persist_alignment_result(
    batch_id: UUID,
    pairs: list[AlignPair],
    locked_signatures: set[tuple[tuple[int, ...], tuple[int, ...]]],
) -> None:
    """使用全新短事务检查取消状态并保存计算结果。"""

    with SessionLocal() as db:
        batch = db.get(ProofreadingBatch, batch_id)
        if not batch:
            return
        if batch.cancel_requested or batch.status in {"canceling", "canceled"}:
            raise AlignmentCanceled("用户已终止双文档对齐任务。")
        _store_pairs(db, batch, pairs, locked_signatures=locked_signatures)


def _mark_alignment_canceled(batch_id: UUID) -> None:
    with SessionLocal() as db:
        batch = db.get(ProofreadingBatch, batch_id)
        if not batch:
            return
        batch.cancel_requested = True
        has_previous_draft = (
            db.query(DocumentAlignmentPair.id).filter_by(batch_id=batch.id).first()
            is not None
        )
        batch.alignment_status = "draft" if has_previous_draft else "canceled"
        batch.status = "draft" if has_previous_draft else "canceled"
        batch.message = (
            "重跑已终止，已保留上一次对齐草稿。"
            if has_previous_draft else "双文档对齐已终止。"
        )
        batch.error_message = ""
        batch.finished_at = _utcnow_naive()
        db.commit()


def _mark_alignment_failed(batch_id: UUID, exc: Exception) -> None:
    with SessionLocal() as db:
        batch = db.get(ProofreadingBatch, batch_id)
        if not batch:
            return
        batch.alignment_status = "failed"
        batch.status = "failed"
        batch.error_message = str(exc)
        batch.finished_at = _utcnow_naive()
        db.commit()


def run_alignment_batch(batch_id: UUID) -> None:
    try:
        work = _load_alignment_work(batch_id)
        if work is None:
            return
        batch, src, tgt, locked_anchors, locked_signatures = work

        # 从这里到最终落库之间不持有数据库会话。全量 LLM 复核即使运行数小时，
        # 也不会触发 PostgreSQL idle-in-transaction 超时。
        if not locked_anchors:
            pairs = asyncio.run(_compute_pairs(
                batch, src, tgt,
                progress_callback=lambda completed, total, phase: _write_alignment_progress(
                    batch.id, completed, total, phase,
                ),
            ))
        else:
            pairs = []
            src_cursor = tgt_cursor = 0
            for src_indices, tgt_indices in locked_anchors:
                src_start = min(src_indices) if src_indices else src_cursor
                tgt_start = min(tgt_indices) if tgt_indices else tgt_cursor
                if src_start < src_cursor or tgt_start < tgt_cursor:
                    raise ValueError("锁定配对的顺序发生交叉，无法作为重跑锚点。")
                pairs.extend(asyncio.run(_compute_pairs(
                    batch, src[src_cursor:src_start], tgt[tgt_cursor:tgt_start],
                )))
                pairs.append(AlignPair(
                    src_indices, tgt_indices, 1.0,
                    method="manual", features={"locked_anchor": True},
                ))
                src_cursor = max(src_indices) + 1 if src_indices else src_start
                tgt_cursor = max(tgt_indices) + 1 if tgt_indices else tgt_start
            pairs.extend(asyncio.run(_compute_pairs(
                batch, src[src_cursor:], tgt[tgt_cursor:],
            )))
        _persist_alignment_result(batch_id, pairs, locked_signatures)
    except AlignmentCanceled:
        _mark_alignment_canceled(batch_id)
    except Exception as exc:
        logger.exception("document alignment batch failed batch_id=%s", batch_id)
        try:
            # 原计算/落库会话可能已经失效，失败状态始终由新会话写回。
            _mark_alignment_failed(batch_id, exc)
        except Exception:
            logger.exception("failed to persist alignment failure batch_id=%s", batch_id)


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
    for pair in pairs:
        src_seen.extend(json.loads(pair.src_indices or "[]"))
        tgt_seen.extend(json.loads(pair.tgt_indices or "[]"))
    src_all = [row.unit_index for row in db.query(DocumentAlignmentUnit).filter_by(batch_id=batch_id, side="source").all()]
    tgt_all = [row.unit_index for row in db.query(DocumentAlignmentUnit).filter_by(batch_id=batch_id, side="target").all()]
    if sorted(src_seen) != sorted(src_all) or len(src_seen) != len(set(src_seen)):
        raise ValueError("原文单元必须且只能属于一个配对。")
    if sorted(tgt_seen) != sorted(tgt_all) or len(tgt_seen) != len(set(tgt_seen)):
        raise ValueError("译文单元必须且只能属于一个配对。")

    # PostgreSQL 的唯一约束会逐行检查批量 UPDATE。直接把 228 改成 227 时，
    # 旧的 227 可能尚未更新，从而产生瞬时冲突。先整体迁移到负数空间，
    # 再写入最终连续序号，确保任意执行顺序下都不会撞唯一键。
    if any(pair.pair_order != expected for expected, pair in enumerate(pairs)):
        for temporary, pair in enumerate(pairs, start=1):
            pair.pair_order = -temporary
        db.flush()
        for expected, pair in enumerate(pairs):
            pair.pair_order = expected
        db.flush()


def refresh_pair_text(db: Session, pair: DocumentAlignmentPair) -> None:
    src = {unit.index: unit for unit in _orm_units(db, pair.batch_id, "source")}
    tgt = {unit.index: unit for unit in _orm_units(db, pair.batch_id, "target")}
    try:
        features = json.loads(pair.features or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        features = {}
    if not isinstance(features, dict):
        features = {}
    src_indices = json.loads(pair.src_indices or "[]")
    tgt_indices = json.loads(pair.tgt_indices or "[]")
    pair.source_text = (
        str(features["manual_source_text"])
        if "manual_source_text" in features
        else _join_text(src_indices, src)
    )
    pair.target_text = (
        str(features["manual_target_text"])
        if "manual_target_text" in features
        else _join_text(tgt_indices, tgt)
    )
    _set_pair_cell_features(features, src_indices, tgt_indices, src, tgt)
    pair.features = json.dumps(features, ensure_ascii=False)
    pair.method = "manual"
    pair.confidence = 1.0 if pair.locked else 0.8
    pair.confidence_level = "high"


def merge_alignment_pair_range(
    db: Session,
    batch_id: UUID,
    pair_ids: Iterable[UUID],
    *,
    lock_merged_pair: bool = True,
) -> DocumentAlignmentPair:
    """将同一批次中连续的多个配对原子合并，并压紧 pair_order。"""
    unique_ids = list(dict.fromkeys(pair_ids))
    if len(unique_ids) < 2:
        raise ValueError("请至少选择两个配对进行合并。")
    if len(unique_ids) > 100:
        raise ValueError("单次最多合并 100 个连续配对。")

    rows = db.query(DocumentAlignmentPair).filter(
        DocumentAlignmentPair.batch_id == batch_id,
        DocumentAlignmentPair.id.in_(unique_ids),
    ).order_by(DocumentAlignmentPair.pair_order).all()
    if len(rows) != len(unique_ids):
        raise ValueError("部分待合并配对不存在或不属于当前批次。")

    orders = [row.pair_order for row in rows]
    if orders != list(range(orders[0], orders[0] + len(rows))):
        raise ValueError("只能合并全文顺序中前后连续的配对。")

    first = rows[0]
    merged_src: list[int] = []
    merged_tgt: list[int] = []
    merged_pair_ids: list[str] = []
    for row in rows:
        merged_src.extend(json.loads(row.src_indices or "[]"))
        merged_tgt.extend(json.loads(row.tgt_indices or "[]"))
        try:
            row_features = json.loads(row.features or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            row_features = {}
        previous_ids = row_features.get("merged_pair_ids")
        if isinstance(previous_ids, list):
            merged_pair_ids.extend(str(value) for value in previous_ids)
        else:
            merged_pair_ids.append(str(row.id))

    first.src_indices = json.dumps(merged_src)
    first.tgt_indices = json.dumps(merged_tgt)
    first.locked = lock_merged_pair or all(row.locked for row in rows)
    first.features = json.dumps({
        "manual_merge": True,
        "merged_pair_ids": list(dict.fromkeys(merged_pair_ids)),
        "merged_pair_count": len(rows),
    }, ensure_ascii=False)

    # 合并后若存在原文，以首个原文单元作为版式锚点；纯增译则使用首个译文单元。
    anchor_side = "source" if merged_src else "target"
    anchor_index = merged_src[0] if merged_src else merged_tgt[0] if merged_tgt else None
    if anchor_index is not None:
        anchor = db.query(DocumentAlignmentUnit).filter_by(
            batch_id=batch_id,
            side=anchor_side,
            unit_index=anchor_index,
        ).first()
        if anchor is not None:
            first.block_type = anchor.block_type
            first.block_index = anchor.block_index
            first.row_index = anchor.row_index
            first.cell_index = anchor.cell_index

    for row in rows[1:]:
        db.delete(row)
    db.flush()
    refresh_pair_text(db, first)
    validate_pair_integrity(db, batch_id)
    db.flush()
    return first


def replace_alignment_pair_range(
    db: Session,
    batch_id: UUID,
    start_order: int,
    delete_count: int,
    replacements: list[dict[str, Any]],
) -> list[DocumentAlignmentPair]:
    """原子替换一段连续配对，供人工调整以及撤回/重做复用。"""
    rows = db.query(DocumentAlignmentPair).filter_by(batch_id=batch_id).order_by(
        DocumentAlignmentPair.pair_order,
    ).all()
    if start_order < 0 or start_order > len(rows):
        raise ValueError("调整起点超出当前配对范围。")
    if delete_count < 0 or start_order + delete_count > len(rows):
        raise ValueError("待替换的配对范围无效。")
    if delete_count > 100 or len(replacements) > 100:
        raise ValueError("单次最多调整 100 个连续配对。")

    normalized: list[dict[str, Any]] = []
    for item in replacements:
        src = item.get("src_indices", [])
        tgt = item.get("tgt_indices", [])
        if (
            not isinstance(src, list) or not isinstance(tgt, list)
            or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in [*src, *tgt])
            or src != sorted(set(src)) or tgt != sorted(set(tgt))
        ):
            raise ValueError("人工调整中的原文或译文单元序号无效。")
        normalized.append({
            "src_indices": src,
            "tgt_indices": tgt,
            "locked": bool(item.get("locked", True)),
        })

    before = rows[:start_order]
    removed = rows[start_order:start_order + delete_count]
    after = rows[start_order + delete_count:]

    # 先把保留行迁入负数序号空间，避免 PostgreSQL 唯一约束逐行检查时发生瞬时冲突。
    for temporary, row in enumerate([*before, *after], start=1):
        row.pair_order = -temporary
    for row in removed:
        db.delete(row)
    db.flush()

    created: list[DocumentAlignmentPair] = []
    for offset, item in enumerate(normalized):
        pair = DocumentAlignmentPair(
            batch_id=batch_id,
            pair_order=start_order + offset,
            src_indices=json.dumps(item["src_indices"]),
            tgt_indices=json.dumps(item["tgt_indices"]),
            source_text="",
            target_text="",
            confidence=1.0 if item["locked"] else 0.8,
            confidence_level="high",
            method="manual",
            features=json.dumps({"manual_adjustment": True}, ensure_ascii=False),
            locked=item["locked"],
        )
        db.add(pair)
        created.append(pair)
    db.flush()

    final_rows = [*before, *created, *after]
    for order, row in enumerate(final_rows):
        row.pair_order = order
    db.flush()
    for pair in created:
        src_indices = json.loads(pair.src_indices or "[]")
        tgt_indices = json.loads(pair.tgt_indices or "[]")
        anchor_side = "source" if src_indices else "target"
        anchor_index = src_indices[0] if src_indices else tgt_indices[0] if tgt_indices else None
        if anchor_index is not None:
            anchor = db.query(DocumentAlignmentUnit).filter_by(
                batch_id=batch_id,
                side=anchor_side,
                unit_index=anchor_index,
            ).first()
            if anchor is not None:
                pair.block_type = anchor.block_type
                pair.block_index = anchor.block_index
                pair.row_index = anchor.row_index
                pair.cell_index = anchor.cell_index
        refresh_pair_text(db, pair)
    validate_pair_integrity(db, batch_id)
    db.flush()
    return created


def delete_alignment_pair(
    db: Session,
    batch_id: UUID,
    pair_id: UUID,
) -> DocumentAlignmentPair | None:
    """删除人工空行或增译配对，同时保持两侧解析单元完整覆盖。

    增译配对仍持有目标文档单元，不能直接物理删除；将其单元并入相邻配对，
    并把相邻配对当前译文保存为人工文本，确保被删除的增译不会重新出现。
    """
    rows = db.query(DocumentAlignmentPair).filter_by(batch_id=batch_id).order_by(
        DocumentAlignmentPair.pair_order,
    ).all()
    current_index = next((index for index, row in enumerate(rows) if row.id == pair_id), -1)
    if current_index < 0:
        raise ValueError("待删除配对不存在或不属于当前批次。")
    current = rows[current_index]
    source_indices = json.loads(current.src_indices or "[]")
    target_indices = json.loads(current.tgt_indices or "[]")
    if source_indices:
        raise ValueError("只能删除空行或无对应原文的增译行。")

    neighbor: DocumentAlignmentPair | None = None
    if target_indices:
        if current_index > 0:
            neighbor = rows[current_index - 1]
            neighbor_target_indices = json.loads(neighbor.tgt_indices or "[]")
            neighbor.tgt_indices = json.dumps([*neighbor_target_indices, *target_indices])
        elif current_index + 1 < len(rows):
            neighbor = rows[current_index + 1]
            neighbor_target_indices = json.loads(neighbor.tgt_indices or "[]")
            neighbor.tgt_indices = json.dumps([*target_indices, *neighbor_target_indices])
        else:
            raise ValueError("文档仅剩这一条增译，无法在保持目标文档结构的同时删除。")

        try:
            neighbor_features = json.loads(neighbor.features or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            neighbor_features = {}
        if not isinstance(neighbor_features, dict):
            neighbor_features = {}
        neighbor_features["manual_target_text"] = neighbor.target_text or ""
        deleted_ids = neighbor_features.get("absorbed_deleted_pair_ids")
        neighbor_features["absorbed_deleted_pair_ids"] = [
            *(deleted_ids if isinstance(deleted_ids, list) else []),
            str(current.id),
        ]
        neighbor_features["manual_deleted_translation_only"] = True
        neighbor.features = json.dumps(neighbor_features, ensure_ascii=False)
        neighbor.locked = True

    db.delete(current)
    db.flush()
    if neighbor is not None:
        refresh_pair_text(db, neighbor)
    # 由统一完整性校验完成一次连续编号重排。这里若先手工迁移、随后校验再次迁移，
    # PostgreSQL 会在第二轮批量 UPDATE 中命中 (batch_id, pair_order) 唯一约束。
    validate_pair_integrity(db, batch_id)
    db.flush()
    return neighbor


def _ordered_cell_groups(
    indices: list[int], units: dict[int, AlignUnit],
) -> list[tuple[str, list[int], set[str]]]:
    groups: list[tuple[str, list[int], set[str]]] = []
    for index in indices:
        unit = units[index]
        key = unit.cell_key
        if groups and groups[-1][0] == key:
            groups[-1][1].append(index)
            groups[-1][2].update(unit.numbers)
        else:
            groups.append((key, [index], set(unit.numbers)))
    return groups


def _align_cell_groups(
    source: list[tuple[str, list[int], set[str]]],
    target: list[tuple[str, list[int], set[str]]],
) -> list[dict[str, Any]]:
    """用同键和数字强锚点对齐单元格组，其余内容按单调顺序产生 1:1 或 gap。"""
    n, m = len(source), len(target)
    inf = float("inf")
    scores = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, str] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    scores[0][0] = 0.0

    def match_cost(left: tuple[str, list[int], set[str]], right: tuple[str, list[int], set[str]]) -> float:
        if left[0] and left[0] == right[0]:
            return -20.0
        if left[2] and left[2] == right[2]:
            return -10.0
        return 1.0

    for i in range(n + 1):
        for j in range(m + 1):
            if scores[i][j] == inf:
                continue
            candidates: list[tuple[int, int, str, float]] = []
            if i < n and j < m:
                candidates.append((i + 1, j + 1, "pair", match_cost(source[i], target[j])))
            if i < n:
                candidates.append((i + 1, j, "source_gap", 2.0))
            if j < m:
                candidates.append((i, j + 1, "target_gap", 2.0))
            for ni, nj, operation, cost in candidates:
                candidate = scores[i][j] + cost
                if candidate < scores[ni][nj]:
                    scores[ni][nj] = candidate
                    back[ni][nj] = (i, j, operation)

    result: list[dict[str, Any]] = []
    i, j = n, m
    while i or j:
        previous = back[i][j]
        if previous is None:
            raise RuntimeError("单元格拆分回溯失败。")
        pi, pj, operation = previous
        if operation == "pair":
            result.append({"src_indices": source[pi][1], "tgt_indices": target[pj][1], "locked": True})
        elif operation == "source_gap":
            result.append({"src_indices": source[pi][1], "tgt_indices": [], "locked": True})
        else:
            result.append({"src_indices": [], "tgt_indices": target[pj][1], "locked": True})
        i, j = pi, pj
    result.reverse()
    return result


def split_alignment_pairs_by_cell(
    db: Session, batch_id: UUID, pair_ids: Iterable[UUID] | None = None,
) -> dict[str, int]:
    """把已落库的同侧跨单元格配对原地拆开，无需数据库迁移。"""
    source_units = {unit.index: unit for unit in _orm_units(db, batch_id, "source")}
    target_units = {unit.index: unit for unit in _orm_units(db, batch_id, "target")}
    requested = list(dict.fromkeys(pair_ids or []))
    rows = db.query(DocumentAlignmentPair).filter_by(batch_id=batch_id).order_by(
        DocumentAlignmentPair.pair_order,
    ).all()
    selected_ids = set(requested) if requested else {row.id for row in rows}
    if requested and len({row.id for row in rows}.intersection(selected_ids)) != len(requested):
        raise ValueError("部分待拆分配对不存在或不属于当前批次。")

    specs: list[dict[str, Any]] = []
    changed_pairs = created_pairs = 0
    for row in rows:
        source_indices = json.loads(row.src_indices or "[]")
        target_indices = json.loads(row.tgt_indices or "[]")
        source_groups = _ordered_cell_groups(source_indices, source_units)
        target_groups = _ordered_cell_groups(target_indices, target_units)
        source_keys = {key for key, _, _ in source_groups if key}
        target_keys = {key for key, _, _ in target_groups if key}
        if row.id not in selected_ids or (len(source_keys) <= 1 and len(target_keys) <= 1):
            specs.append({
                "row": row, "src_indices": source_indices, "tgt_indices": target_indices,
                "locked": row.locked, "changed": False,
            })
            continue
        replacements = _align_cell_groups(source_groups, target_groups)
        changed_pairs += 1
        created_pairs += len(replacements)
        for offset, replacement in enumerate(replacements):
            specs.append({
                **replacement,
                "row": row if offset == 0 else None,
                "changed": True,
            })

    # 相邻两个互补 gap 若来自同一单元格，重新组成 1:1；这能修复跨配对形成的规则性错位链。
    merged_gaps = 0
    index = 0
    while index + 1 < len(specs):
            left, right = specs[index], specs[index + 1]
            if not left["changed"] and not right["changed"]:
                index += 1
                continue
            left_src, left_tgt = left["src_indices"], left["tgt_indices"]
            right_src, right_tgt = right["src_indices"], right["tgt_indices"]
            if left_src and not left_tgt and right_tgt and not right_src:
                src_indices, tgt_indices = left_src, right_tgt
            elif left_tgt and not left_src and right_src and not right_tgt:
                src_indices, tgt_indices = right_src, left_tgt
            else:
                index += 1
                continue
            src_keys = {source_units[item].cell_key for item in src_indices if source_units[item].cell_key}
            tgt_keys = {target_units[item].cell_key for item in tgt_indices if target_units[item].cell_key}
            if len(src_keys) != 1 or src_keys != tgt_keys:
                index += 1
                continue
            left.update({
                "src_indices": sorted(src_indices),
                "tgt_indices": sorted(tgt_indices),
                "locked": True,
                "changed": True,
            })
            specs.pop(index + 1)
            merged_gaps += 1

    if not changed_pairs:
        return {"changed_pairs": 0, "created_pairs": 0, "merged_gaps": 0}

    # PostgreSQL 会逐行检查唯一序号：先整体搬到负数空间，再一次性写回最终顺序。
    for temporary, row in enumerate(rows, start=1):
        row.pair_order = -temporary
    db.flush()
    retained_rows = {id(spec["row"]) for spec in specs if spec["row"] is not None}
    for row in rows:
        if id(row) not in retained_rows:
            db.delete(row)
    db.flush()

    for order, spec in enumerate(specs):
        row = spec["row"]
        if row is None:
            row = DocumentAlignmentPair(
                batch_id=batch_id, pair_order=order,
                src_indices="[]", tgt_indices="[]", source_text="", target_text="",
                confidence=1.0, confidence_level="high", method="manual",
                features="{}", locked=True,
            )
            db.add(row)
            spec["row"] = row
        row.pair_order = order
        if not spec["changed"]:
            continue
        src_indices = spec["src_indices"]
        tgt_indices = spec["tgt_indices"]
        features: dict[str, Any] = {"manual_adjustment": True, "split_by_cell": True}
        _set_pair_cell_features(features, src_indices, tgt_indices, source_units, target_units)
        row.src_indices = json.dumps(src_indices)
        row.tgt_indices = json.dumps(tgt_indices)
        row.source_text = _join_text(src_indices, source_units)
        row.target_text = _join_text(tgt_indices, target_units)
        row.features = json.dumps(features, ensure_ascii=False)
        row.locked = bool(spec["locked"])
        row.method = "manual"
        row.confidence = 1.0 if row.locked else 0.8
        row.confidence_level = "high"
        anchor = (
            source_units.get(src_indices[0]) if src_indices
            else target_units.get(tgt_indices[0]) if tgt_indices else None
        )
        if anchor is not None:
            row.block_type = anchor.block_type
            row.block_index = anchor.block_index
            row.row_index = anchor.row_index
            row.cell_index = anchor.cell_index

    db.flush()
    validate_pair_integrity(db, batch_id)
    batch = db.get(ProofreadingBatch, batch_id)
    if batch is not None:
        batch.total_segments = len(specs)
        batch.skipped_segments = sum(
            bool(spec["src_indices"]) and not spec["tgt_indices"] for spec in specs
        )

    return {
        "changed_pairs": changed_pairs,
        "created_pairs": created_pairs,
        "merged_gaps": merged_gaps,
    }
