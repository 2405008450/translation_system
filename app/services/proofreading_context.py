"""校对的只读相邻上下文；不参与结果回写和重复句传播。"""
from __future__ import annotations

import json

CONTEXT_CHAR_LIMIT = 6000


def _clip(value: str) -> str:
    from app.services.llm_service import _encode_line_break_placeholders
    value = _encode_line_break_placeholders(value)
    if len(value) <= 1000:
        return value
    prefix = value[:990]
    # 截断不能留下半个换行占位符。
    if prefix.rfind("⟦") > prefix.rfind("⟧"):
        prefix = prefix[:prefix.rfind("⟦")]
    return prefix + "…[已截断]"


def attach_neighbor_context(db, rows, groups, current_user) -> None:
    from app.auth import can_access_all_projects
    from app.routers.api import _apply_segment_assignment_visibility_filter
    from app.models import FileRecord, Segment

    readable = {segment.id for _, segment in rows}
    if not can_access_all_projects(current_user):
        readable = set()
        files = {segment.file_record_id for _, segment in rows}
        for file_id in files:
            file = db.get(FileRecord, file_id)
            query = db.query(Segment.id).filter(Segment.file_record_id == file_id)
            readable.update(row.id for row in _apply_segment_assignment_visibility_filter(
                db, query, file, current_user,
            ).all())
    by_binding = {}
    binding_by_segment = {}
    for baseline, segment in rows:
        by_binding.setdefault(baseline.binding_id, []).append(segment)
        binding_by_segment[segment.id] = str(baseline.binding_id)
    locations = {}
    for segments in by_binding.values():
        segments.sort(key=lambda s: (s.sequence_index if s.sequence_index is not None and s.sequence_index >= 0
                                     else s.display_index, str(s.id)))
        for index, segment in enumerate(segments):
            locations[str(segment.id)] = (segments, index)
    for group in groups:
        segments, index = locations[group["sid"]]
        context = []
        for offset in (-2, -1, 1, 2):
            other = index + offset
            if not 0 <= other < len(segments):
                continue
            segment = segments[other]
            if segment.id not in readable:
                continue
            context.append({
                "sid": str(segment.id), "offset": offset,
                "file_id": str(segment.file_record_id), "binding_id": binding_by_segment[segment.id],
                "position": segment.sequence_index,
                "source_text": _clip(segment.source_text or ""),
                "target_text": _clip(segment.target_text or ""),
                "protection_reason": segment.auto_write_protection_reason
                if segment.auto_write_protection == "protected" else None,
            })
        group["readonly_context"] = context


def pack_readonly_context(groups):
    """近邻优先；同请求去重，同时保留每个待校对句段的邻接关系。"""
    context = {}
    links = {group["sid"]: [] for group in groups}
    editable_ids = set(links)
    used = 0
    candidates = [(abs(item["offset"]), index, item) for index, group in enumerate(groups)
                  for item in group.get("readonly_context", [])]
    for _, index, item in sorted(candidates, key=lambda value: (value[0], value[1], value[2]["offset"])):
        sid = item["sid"]
        if sid in editable_ids:
            links[groups[index]["sid"]].append({"sid": sid, "offset": item["offset"]})
            continue
        if sid not in context:
            size = len(json.dumps(item, ensure_ascii=False))
            if used + size > CONTEXT_CHAR_LIMIT:
                continue
            context[sid] = {key: value for key, value in item.items() if key != "offset"}
            used += size
        links[groups[index]["sid"]].append({"sid": sid, "offset": item["offset"]})
    for entries in links.values():
        entries.sort(key=lambda item: item["offset"])
    ordered = sorted(context.values(), key=lambda item: (
        item.get("file_id", ""), item.get("binding_id", ""), item.get("position", -1), item["sid"],
    ))
    return {"readonly_context": ordered, "neighbors": links}
