"""保守核验历史 TM 精确采用证据，不根据当前分数单独推断保护。"""
from __future__ import annotations

import json

from app.models import TranslationMemory
from app.services.normalizer import normalize_text


def inspect_backfill_candidate(db, segment, file):
    evidence = {"segment_id": str(segment.id), "version": int(segment.version or 1),
                "file_id": str(file.id), "eligible": False}
    if segment.auto_write_protection != "none":
        return {**evidence, "reason": "protection_already_decided"}
    if segment.source != "tm" or not segment.target_text.strip():
        return {**evidence, "reason": "not_current_tm_translation"}
    if float(segment.score or 0) != 1.0 or not segment.matched_source_text:
        return {**evidence, "reason": "missing_exact_evidence"}
    if normalize_text(segment.source_text) != normalize_text(segment.matched_source_text):
        return {**evidence, "reason": "source_evidence_mismatch"}
    try:
        collection_ids = json.loads(file.collection_ids_json or "[]")
        if not isinstance(collection_ids, list):
            raise ValueError()
        from uuid import UUID
        collection_ids = [UUID(str(value)) for value in collection_ids]
    except (ValueError, TypeError):
        return {**evidence, "reason": "invalid_collection_scope"}
    if not collection_ids and file.collection_id:
        collection_ids = [file.collection_id]
    if not collection_ids or not file.source_language or not file.target_language:
        return {**evidence, "reason": "missing_collection_or_language_scope"}
    # 原文精确相等是保守条件；历史大小写/标点归一化命中交由人工处理。
    matches = db.query(TranslationMemory).filter(
        TranslationMemory.collection_id.in_(collection_ids),
        TranslationMemory.source_language == file.source_language,
        TranslationMemory.target_language == file.target_language,
        TranslationMemory.source_text == segment.matched_source_text,
    ).limit(2).all()
    if len(matches) != 1:
        return {**evidence, "reason": "missing_or_ambiguous_tm_entry"}
    entry = matches[0]
    if entry.target_text != segment.target_text:
        return {**evidence, "reason": "tm_translation_changed"}
    if not segment.matched_updated_at or entry.updated_at != segment.matched_updated_at:
        return {**evidence, "reason": "tm_revision_unverifiable"}
    return {**evidence, "eligible": True, "reason": "verified_tm_exact",
            "tm_entry_id": str(entry.id), "tm_updated_at": entry.updated_at.isoformat()}
