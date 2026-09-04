from __future__ import annotations

import json
from typing import Any

from app.services.llm_service import LLMRequestError, request_chat_completion
from app.services.translation_review.llm_gate import llm_gate

from .dp import AlignPair, BoundaryKey, SemanticSimilarity, _within_single_key
from .features import crosses_heading_boundary, has_structure_conflict
from .parser import AlignUnit


def _pair_mapping_signature(pairs: list[AlignPair]) -> tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]:
    return tuple((tuple(pair.src_indices), tuple(pair.tgt_indices)) for pair in pairs)


def _set_refinement_outcome(
    outcome: dict[str, str] | None, status: str, *,
    detail: str = "", provider: str = "", model: str = "",
) -> None:
    if outcome is None:
        return
    outcome.clear()
    outcome["status"] = status
    if detail:
        outcome["detail"] = detail[:300]
    if provider:
        outcome["provider"] = provider
    if model:
        outcome["model"] = model


def _classify_request_failure(exc: Exception) -> str:
    message = str(exc).lower()
    timeout_markers = ("请求超过", "timeout", "timed out", "超时", "未返回")
    return "timeout" if any(marker in message for marker in timeout_markers) else "request_error"


def _validate_index_response(
    payload: Any, src_count: int, tgt_count: int, *,
    src_keys: list[str] | None = None, tgt_keys: list[str] | None = None,
) -> list[tuple[list[int], list[int]]] | None:
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
        if source and source != list(range(min(source), max(source) + 1)):
            return None
        if target and target != list(range(min(target), max(target) + 1)):
            return None
        if src_keys is not None and len({src_keys[index] for index in source if src_keys[index]}) > 1:
            return None
        if tgt_keys is not None and len({tgt_keys[index] for index in target if tgt_keys[index]}) > 1:
            return None
        if used_src.intersection(source) or used_tgt.intersection(target):
            return None
        used_src.update(source)
        used_tgt.update(target)
        result.append((source, target))
    if used_src != set(range(src_count)) or used_tgt != set(range(tgt_count)):
        return None
    last_src = last_tgt = -1
    for source, target in result:
        if source:
            if source[0] <= last_src:
                return None
            last_src = source[-1]
        if target:
            if target[0] <= last_tgt:
                return None
            last_tgt = target[-1]
    return result


def _candidate_quality(
    pairs: list[AlignPair], src: list[AlignUnit], tgt: list[AlignUnit],
    semantic_similarity: SemanticSimilarity | None, lang_ratio: float,
) -> tuple[int, int, float | None, float | None, float]:
    src_map, tgt_map = {unit.index: unit for unit in src}, {unit.index: unit for unit in tgt}
    number_conflicts = structure_conflicts = 0
    semantic_scores: list[float] = []
    length_penalty = 0.0
    compared = 0
    for pair in pairs:
        if not pair.src_indices or not pair.tgt_indices:
            continue
        source = [src_map[index] for index in pair.src_indices]
        target = [tgt_map[index] for index in pair.tgt_indices]
        source_numbers = {number for unit in source for number in unit.numbers}
        target_numbers = {number for unit in target for number in unit.numbers}
        if source_numbers and target_numbers and not source_numbers.intersection(target_numbers):
            number_conflicts += 1
        if has_structure_conflict(source, target):
            structure_conflicts += 1
        if semantic_similarity is not None:
            score = semantic_similarity(source, target)
            if score is not None:
                semantic_scores.append(score)
                pair.features["semantic_similarity"] = round(score, 4)
        source_length = sum(unit.char_len for unit in source)
        target_length = sum(unit.char_len for unit in target)
        actual_ratio = target_length / max(1, source_length)
        if actual_ratio < lang_ratio * 0.25 or actual_ratio > lang_ratio * 2.5:
            length_penalty += 1.0
        compared += 1
    mean_semantic = sum(semantic_scores) / len(semantic_scores) if semantic_scores else None
    min_semantic = min(semantic_scores) if semantic_scores else None
    return (
        number_conflicts, structure_conflicts, mean_semantic, min_semantic,
        length_penalty / max(1, compared),
    )


def _has_semantic_adjacent_absorption(
    pairs: list[AlignPair], src: list[AlignUnit], tgt: list[AlignUnit],
    semantic_similarity: SemanticSimilarity | None,
) -> bool:
    """识别“宽配对吞入相邻条目译文，随后形成互补缺口”的典型错位。"""
    if semantic_similarity is None:
        return False
    src_map = {unit.index: unit for unit in src}
    tgt_map = {unit.index: unit for unit in tgt}

    def score(source_indices: list[int], target_indices: list[int]) -> float | None:
        if not source_indices or not target_indices:
            return None
        return semantic_similarity(
            [src_map[index] for index in source_indices],
            [tgt_map[index] for index in target_indices],
        )

    def split_is_better(wide: AlignPair, gap: AlignPair, *, gap_before: bool) -> bool:
        current = score(wide.src_indices, wide.tgt_indices)
        if current is None:
            return False

        # 源侧缺口紧邻宽目标配对：尝试把目标前缀或后缀归还给缺口源文。
        if gap.src_indices and not gap.tgt_indices and len(wide.tgt_indices) > 1:
            for split_at in range(1, len(wide.tgt_indices)):
                moved = wide.tgt_indices[:split_at] if gap_before else wide.tgt_indices[split_at:]
                kept = wide.tgt_indices[split_at:] if gap_before else wide.tgt_indices[:split_at]
                kept_score = score(wide.src_indices, kept)
                gap_score = score(gap.src_indices, moved)
                if _is_clear_semantic_resplit(current, kept_score, gap_score):
                    return True

        # 目标侧缺口紧邻宽源配对：对称地尝试归还源文前缀或后缀。
        if gap.tgt_indices and not gap.src_indices and len(wide.src_indices) > 1:
            for split_at in range(1, len(wide.src_indices)):
                moved = wide.src_indices[:split_at] if gap_before else wide.src_indices[split_at:]
                kept = wide.src_indices[split_at:] if gap_before else wide.src_indices[:split_at]
                kept_score = score(kept, wide.tgt_indices)
                gap_score = score(moved, gap.tgt_indices)
                if _is_clear_semantic_resplit(current, kept_score, gap_score):
                    return True
        return False

    for index, wide in enumerate(pairs):
        if not wide.src_indices or not wide.tgt_indices:
            continue
        if index > 0 and split_is_better(wide, pairs[index - 1], gap_before=True):
            return True
        if index + 1 < len(pairs) and split_is_better(wide, pairs[index + 1], gap_before=False):
            return True
    return False


def _is_clear_semantic_resplit(
    current: float, kept: float | None, gap: float | None,
) -> bool:
    if kept is None or gap is None or kept < 0.62 or gap < 0.62:
        return False
    return (kept + gap) / 2 >= max(0.70, current + 0.08)


def _accept_llm_candidate(
    candidate: list[AlignPair], fallback: list[AlignPair],
    src: list[AlignUnit], tgt: list[AlignUnit],
    semantic_similarity: SemanticSimilarity | None, lang_ratio: float,
    boundary_key: BoundaryKey | None = None,
) -> bool:
    src_map = {unit.index: unit for unit in src}
    tgt_map = {unit.index: unit for unit in tgt}
    if any(
        not _within_single_key([src_map[index] for index in pair.src_indices], boundary_key)
        or not _within_single_key([tgt_map[index] for index in pair.tgt_indices], boundary_key)
        or crosses_heading_boundary([src_map[index] for index in pair.src_indices])
        or crosses_heading_boundary([tgt_map[index] for index in pair.tgt_indices])
        for pair in candidate
    ):
        return False
    if _has_semantic_adjacent_absorption(candidate, src, tgt, semantic_similarity):
        return False
    candidate_quality = _candidate_quality(candidate, src, tgt, semantic_similarity, lang_ratio)
    fallback_quality = _candidate_quality(fallback, src, tgt, semantic_similarity, lang_ratio)
    candidate_gap_units = sum(
        len(pair.src_indices) + len(pair.tgt_indices)
        for pair in candidate if not pair.src_indices or not pair.tgt_indices
    )
    fallback_gap_units = sum(
        len(pair.src_indices) + len(pair.tgt_indices)
        for pair in fallback if not pair.src_indices or not pair.tgt_indices
    )
    if candidate_gap_units > fallback_gap_units + 1:
        return False
    (
        candidate_numbers, candidate_structure, candidate_semantic,
        candidate_min_semantic, candidate_length,
    ) = candidate_quality
    (
        fallback_numbers, fallback_structure, fallback_semantic,
        fallback_min_semantic, fallback_length,
    ) = fallback_quality
    if candidate_numbers > fallback_numbers or candidate_structure > fallback_structure:
        return False
    if candidate_length > max(0.15, fallback_length + 0.05):
        return False
    if semantic_similarity is not None:
        if candidate_semantic is None or candidate_semantic < 0.58:
            return False
        if fallback_semantic is not None and candidate_semantic + 0.03 < fallback_semantic:
            return False
        if (
            candidate_min_semantic is not None
            and fallback_min_semantic is not None
            and candidate_min_semantic < 0.58
            and candidate_min_semantic + 0.08 < fallback_min_semantic
        ):
            return False
    return True


async def refine_hard_block(
    src: list[AlignUnit], tgt: list[AlignUnit], fallback: list[AlignPair], *,
    provider: str = "auto", model_override: str | None = None,
    semantic_similarity: SemanticSimilarity | None = None, lang_ratio: float = 1.0,
    method: str = "llm_boundary", allow_provider_fallback: bool = True,
    review_instruction: str = "",
    review_context: str = "",
    max_output_tokens: int | None = None,
    refinement_outcome: dict[str, str] | None = None,
    accept_validated_candidate: bool = False,
    boundary_key: BoundaryKey | None = None,
) -> list[AlignPair]:
    system = (
        "你是文本对齐工具。只输出 JSON 对象，格式为 {\"pairs\":[{\"s\":[0],\"t\":[0]}]}。"
        "绝对不要输出、复述或改写任何原文或译文；对象中不得出现 s、t、pairs 之外的字段。"
        "必须根据完整互译关系决定一对多、多对一或多对多，禁止因为序号相同就机械地一一配对。"
        "每一侧所有下标必须恰好出现一次，配对顺序必须同时保持源侧和目标侧单调递增。"
        "表格按键对齐：同一配对的源侧下标必须全部来自同一个非空单元格键，目标侧同理。"
        "两侧键值本身不要求相等；遇到跨键内容宁可保留为漏译或增译，也禁止在同一侧跨键合并。"
    )

    def structure(unit: AlignUnit) -> str:
        if unit.block_type == "table_cell":
            return (
                f"key={boundary_key(unit) if boundary_key else unit.cell_key or '-'},"
                f"table={unit.block_index},row={unit.row_index},cell={unit.cell_index},"
                f"parent={unit.parent_segment_id or '-'}"
            )
        return f"type={unit.block_type},block={unit.block_index},parent={unit.parent_segment_id or '-'}"

    source_lines = "\n".join(
        f"S{i} [{structure(unit)}]: {unit.text}" for i, unit in enumerate(src)
    )
    target_lines = "\n".join(
        f"T{i} [{structure(unit)}]: {unit.text}" for i, unit in enumerate(tgt)
    )
    source_positions = {unit.index: index for index, unit in enumerate(src)}
    target_positions = {unit.index: index for index, unit in enumerate(tgt)}
    fallback_payload = [
        {
            "s": [source_positions[index] for index in pair.src_indices],
            "t": [target_positions[index] for index in pair.tgt_indices],
        }
        for pair in fallback
    ]
    user = (
        f"源侧（共 {len(src)} 条）：\n{source_lines}\n\n译侧（共 {len(tgt)} 条）：\n{target_lines}\n\n"
        f"程序候选：{json.dumps({'pairs': fallback_payload}, ensure_ascii=False)}\n\n"
        "程序候选仅供参考；只有确实完整互译时才能放在同一配对中。"
        "不得把相邻段落的额外内容并入当前配对。漏译的 t 为空数组，增译的 s 为空数组。"
        "正文段落中，译文的一个条目可能合并原文多个段落，原文也可能合并多个译文条目；"
        "这种 N:1 或 1:N 只适用于非表格正文。表格不同单元格之间不得用断句差异解释为合并。"
        "只有对应内容确实不存在时才能使用空数组。"
        f"{review_instruction}"
        f"{review_context}"
        "只返回 JSON。"
    )
    # 边界复核是兜底路径。单次失败后保留已经通过确定性与向量门禁的候选，
    # 避免一个窗口连续占用几十秒并阻塞整批进度。
    for _ in range(1):
        try:
            async with llm_gate():
                completion = await request_chat_completion(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    provider=provider, model_override=model_override,
                    response_format={"type": "json_object"}, temperature=0,
                    allow_fallback=allow_provider_fallback,
                    extra_body=(
                        {"max_tokens": max_output_tokens} if max_output_tokens else None
                    ),
                )
            try:
                payload = json.loads(completion.content)
            except json.JSONDecodeError as exc:
                _set_refinement_outcome(
                    refinement_outcome, "invalid_json", detail=str(exc),
                    provider=completion.provider, model=completion.model,
                )
                continue
            parsed = _validate_index_response(
                payload, len(src), len(tgt),
                src_keys=[boundary_key(unit) if boundary_key else "" for unit in src],
                tgt_keys=[boundary_key(unit) if boundary_key else "" for unit in tgt],
            )
            if parsed is None:
                _set_refinement_outcome(
                    refinement_outcome, "invalid_json",
                    detail="JSON 索引未完整覆盖、重复、越界或不满足单调顺序。",
                    provider=completion.provider, model=completion.model,
                )
                continue
            candidate = [AlignPair(
                [src[index].index for index in source], [tgt[index].index for index in target],
                0.6 if source and target else 0.3, method=method,
                features={
                    "op": f"{len(source)}-{len(target)}", "llm_indices_only": True,
                    "boundary_granularity": "coarse" if len(source) != 1 or len(target) != 1 else "sentence",
                    "llm_provider": completion.provider,
                    "llm_model": completion.model,
                },
            ) for source, target in parsed]
            if accept_validated_candidate or _accept_llm_candidate(
                candidate, fallback, src, tgt, semantic_similarity, lang_ratio,
                boundary_key=boundary_key,
            ):
                if _pair_mapping_signature(candidate) == _pair_mapping_signature(fallback):
                    _set_refinement_outcome(
                        refinement_outcome, "unchanged",
                        provider=completion.provider, model=completion.model,
                    )
                    return fallback
                _set_refinement_outcome(
                    refinement_outcome, "accepted",
                    provider=completion.provider, model=completion.model,
                )
                return candidate
            _set_refinement_outcome(
                refinement_outcome, "quality_rejected",
                detail="LLM 候选未通过语义、数字、结构、长度或相邻缺口质量门禁。",
                provider=completion.provider, model=completion.model,
            )
        except LLMRequestError as exc:
            _set_refinement_outcome(
                refinement_outcome, _classify_request_failure(exc), detail=str(exc),
            )
            continue
        except Exception as exc:  # LLM 永远只是可选优化，任何失败都回落 DP。
            _set_refinement_outcome(
                refinement_outcome, "unexpected_error", detail=str(exc),
            )
            continue
    if refinement_outcome is not None and "status" not in refinement_outcome:
        _set_refinement_outcome(refinement_outcome, "invalid_response")
    return fallback


def needs_llm_refinement(pairs: list[AlignPair]) -> bool:
    if not pairs:
        return False
    # 多对多本身不是错误：不同语言的断句天然可能不同。只有存在缺口、
    # 低置信度或明确的质量冲突时，才把这个小窗口交给 LLM 复核。
    if any(not pair.src_indices or not pair.tgt_indices for pair in pairs):
        return True
    if any(pair.confidence_level == "low" for pair in pairs):
        return True
    conflict_markers = {"number_conflict", "structure_conflict", "extreme_length_ratio"}
    return any(conflict_markers.intersection(pair.features) for pair in pairs)
