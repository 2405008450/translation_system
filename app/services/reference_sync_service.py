"""把参考文件分析出的术语和翻译记忆同步到项目级的词汇表/记忆库。

设计要点：
- 词汇表（GlossaryBase + GlossaryEntry）走的是翻译时智能注入。
- 翻译记忆（TMCollection + TranslationMemory）走的是 TM 匹配 → fuzzy 改写。
- 每个 ReferenceProfile 独占一个 GlossaryBase 和 TMCollection，标记 origin='reference'。
- 重新分析时整体覆盖（先清空 entries 再写），保证幂等。
- 写完后把这两个 base 自动追加到项目下所有文件的绑定列表里。
- 删除参考资料时连带删除对应 base，cascade 自然清掉 entries 和文件层绑定。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    FileRecord,
    GlossaryBase,
    GlossaryEntry,
    Project,
    ReferenceProfile,
    TMCollection,
    TranslationMemory,
)
from app.services.normalizer import (
    build_source_hash,
    normalize_match_text,
    normalize_text,
)


logger = logging.getLogger(__name__)


REFERENCE_ORIGIN = "reference"


def sync_reference_profile_resources(
    db: Session,
    profile: ReferenceProfile,
    *,
    project: Project | None,
    source_language: str | None,
    target_language: str | None,
    creator_id: uuid.UUID | None,
    terminology: list[dict] | None,
    translation_memory: list[dict] | None,
) -> None:
    """把分析结果落到 glossary_bases / memory_bases，并自动绑定到项目下所有文件。

    没有项目就跳过（参考分析必须挂在某个文件上，文件如果不属于任何项目就没法做项目级共享）。
    """
    if project is None:
        logger.info(
            "reference profile %s 未关联项目，跳过同步到词汇表/记忆库", profile.id
        )
        return

    if not source_language or not target_language:
        logger.info(
            "reference profile %s 缺少语言对，跳过同步到词汇表/记忆库", profile.id
        )
        return

    profile.project_id = project.id

    glossary_base = _ensure_glossary_base(
        db,
        profile=profile,
        project=project,
        source_language=source_language,
        target_language=target_language,
    )
    _replace_glossary_entries(
        db,
        glossary_base=glossary_base,
        source_language=source_language,
        target_language=target_language,
        creator_id=creator_id,
        terminology=terminology or [],
    )

    memory_base = _ensure_memory_base(
        db,
        profile=profile,
        project=project,
        source_language=source_language,
        target_language=target_language,
    )
    _replace_memory_entries(
        db,
        memory_base=memory_base,
        source_language=source_language,
        target_language=target_language,
        creator_id=creator_id,
        translation_memory=translation_memory or [],
    )

    profile.glossary_base_id = glossary_base.id
    profile.memory_base_id = memory_base.id

    _bind_to_project_files(
        db,
        project_id=project.id,
        source_language=source_language,
        target_language=target_language,
        glossary_base_id=glossary_base.id,
        memory_base_id=memory_base.id,
    )


def cleanup_reference_profile_resources(
    db: Session,
    profile: ReferenceProfile,
) -> None:
    """删除参考资料同步出来的 glossary_base 和 memory_base，并清掉文件层绑定。

    cascade 会自然带掉 entries；文件层的 base id 数组需要手动剔除。
    """
    project_id = profile.project_id
    glossary_base_id = profile.glossary_base_id
    memory_base_id = profile.memory_base_id

    if project_id and (glossary_base_id or memory_base_id):
        _unbind_from_project_files(
            db,
            project_id=project_id,
            glossary_base_id=glossary_base_id,
            memory_base_id=memory_base_id,
        )

    if glossary_base_id:
        glossary_base = db.get(GlossaryBase, glossary_base_id)
        if glossary_base is not None and glossary_base.origin == REFERENCE_ORIGIN:
            db.delete(glossary_base)

    if memory_base_id:
        memory_base = db.get(TMCollection, memory_base_id)
        if memory_base is not None and memory_base.origin == REFERENCE_ORIGIN:
            db.delete(memory_base)

    profile.glossary_base_id = None
    profile.memory_base_id = None


def attach_project_reference_bases_to_file(
    db: Session,
    file_record: FileRecord,
) -> None:
    """新建文件时调用，自动带上项目级参考 base。"""
    if file_record.project_id is None:
        return
    if not file_record.source_language or not file_record.target_language:
        return

    profiles = (
        db.execute(
            select(ReferenceProfile).where(
                ReferenceProfile.project_id == file_record.project_id
            )
        )
        .scalars()
        .all()
    )
    if not profiles:
        return

    glossary_ids = _load_uuid_list(file_record.glossary_base_ids)
    memory_ids = _load_uuid_list(file_record.collection_ids_json)
    glossary_changed = False
    memory_changed = False

    for profile in profiles:
        if profile.glossary_base_id:
            base = db.get(GlossaryBase, profile.glossary_base_id)
            if (
                base is not None
                and base.source_language == file_record.source_language
                and base.target_language == file_record.target_language
                and profile.glossary_base_id not in glossary_ids
            ):
                glossary_ids.append(profile.glossary_base_id)
                glossary_changed = True
        if profile.memory_base_id:
            base = db.get(TMCollection, profile.memory_base_id)
            if (
                base is not None
                and (base.source_language or file_record.source_language)
                == file_record.source_language
                and (base.target_language or file_record.target_language)
                == file_record.target_language
                and profile.memory_base_id not in memory_ids
            ):
                memory_ids.append(profile.memory_base_id)
                memory_changed = True

    if glossary_changed:
        file_record.glossary_base_ids = json.dumps([str(value) for value in glossary_ids])
    if memory_changed:
        file_record.collection_ids_json = json.dumps([str(value) for value in memory_ids])
        if file_record.collection_id is None and memory_ids:
            file_record.collection_id = memory_ids[0]


# ------------------------- 内部实现 -------------------------


def _ensure_glossary_base(
    db: Session,
    *,
    profile: ReferenceProfile,
    project: Project,
    source_language: str,
    target_language: str,
) -> GlossaryBase:
    if profile.glossary_base_id:
        existing = db.get(GlossaryBase, profile.glossary_base_id)
        if existing is not None:
            existing.name = _build_glossary_base_name(project, profile)
            existing.description = _build_base_description(project)
            existing.source_language = source_language
            existing.target_language = target_language
            existing.project_id = project.id
            existing.origin = REFERENCE_ORIGIN
            return existing

    base = GlossaryBase(
        name=_build_glossary_base_name(project, profile),
        description=_build_base_description(project),
        source_language=source_language,
        target_language=target_language,
        project_id=project.id,
        origin=REFERENCE_ORIGIN,
    )
    db.add(base)
    db.flush()
    return base


def _ensure_memory_base(
    db: Session,
    *,
    profile: ReferenceProfile,
    project: Project,
    source_language: str,
    target_language: str,
) -> TMCollection:
    if profile.memory_base_id:
        existing = db.get(TMCollection, profile.memory_base_id)
        if existing is not None:
            existing.name = _build_memory_base_name(project, profile)
            existing.description = _build_base_description(project)
            existing.source_language = source_language
            existing.target_language = target_language
            existing.project_id = project.id
            existing.origin = REFERENCE_ORIGIN
            return existing

    base = TMCollection(
        name=_build_memory_base_name(project, profile),
        description=_build_base_description(project),
        source_language=source_language,
        target_language=target_language,
        project_id=project.id,
        origin=REFERENCE_ORIGIN,
    )
    db.add(base)
    db.flush()
    return base


def _replace_glossary_entries(
    db: Session,
    *,
    glossary_base: GlossaryBase,
    source_language: str,
    target_language: str,
    creator_id: uuid.UUID | None,
    terminology: list[dict],
) -> None:
    db.execute(
        delete(GlossaryEntry).where(GlossaryEntry.glossary_base_id == glossary_base.id)
    )

    seen_keys: set[str] = set()
    for item in terminology:
        source_text = normalize_text(item.get("source") or item.get("source_text") or "")
        target_text = normalize_text(item.get("target") or item.get("target_text") or "")
        if not source_text or not target_text:
            continue

        source_normalized = normalize_match_text(source_text) or source_text
        if not source_normalized:
            continue
        dedupe_key = source_normalized.casefold()
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        note_parts = []
        context_value = normalize_text(item.get("context") or "")
        if context_value:
            note_parts.append(f"上下文：{context_value}")
        category_value = normalize_text(item.get("category") or "")
        if category_value:
            note_parts.append(f"类别：{category_value}")

        db.add(
            GlossaryEntry(
                glossary_base_id=glossary_base.id,
                source_text=source_text,
                target_text=target_text,
                note="；".join(note_parts) or None,
                source_normalized=source_normalized,
                source_language=source_language,
                target_language=target_language,
                creator_id=creator_id,
            )
        )

    db.flush()


def _replace_memory_entries(
    db: Session,
    *,
    memory_base: TMCollection,
    source_language: str,
    target_language: str,
    creator_id: uuid.UUID | None,
    translation_memory: list[dict],
) -> None:
    db.execute(
        delete(TranslationMemory).where(TranslationMemory.collection_id == memory_base.id)
    )

    seen_hashes: set[str] = set()
    for item in translation_memory:
        source_text = normalize_text(item.get("source") or item.get("source_text") or "")
        target_text = normalize_text(item.get("target") or item.get("target_text") or "")
        if not source_text or not target_text:
            continue

        source_hash = build_source_hash(source_text)
        if source_hash in seen_hashes:
            continue
        seen_hashes.add(source_hash)

        db.add(
            TranslationMemory(
                collection_id=memory_base.id,
                source_text=source_text,
                target_text=target_text,
                source_hash=source_hash,
                source_normalized=normalize_match_text(source_text) or source_text,
                source_language=source_language,
                target_language=target_language,
                creator_id=creator_id,
            )
        )

    db.flush()


def _bind_to_project_files(
    db: Session,
    *,
    project_id: uuid.UUID,
    source_language: str,
    target_language: str,
    glossary_base_id: uuid.UUID,
    memory_base_id: uuid.UUID,
) -> None:
    file_records = (
        db.execute(
            select(FileRecord).where(
                FileRecord.project_id == project_id,
                FileRecord.source_language == source_language,
                FileRecord.target_language == target_language,
            )
        )
        .scalars()
        .all()
    )

    for file_record in file_records:
        glossary_ids = _load_uuid_list(file_record.glossary_base_ids)
        if glossary_base_id not in glossary_ids:
            glossary_ids.append(glossary_base_id)
            file_record.glossary_base_ids = json.dumps(
                [str(value) for value in glossary_ids]
            )

        memory_ids = _load_uuid_list(file_record.collection_ids_json)
        if memory_base_id not in memory_ids:
            memory_ids.append(memory_base_id)
            file_record.collection_ids_json = json.dumps(
                [str(value) for value in memory_ids]
            )
            if file_record.collection_id is None:
                file_record.collection_id = memory_base_id


def _unbind_from_project_files(
    db: Session,
    *,
    project_id: uuid.UUID,
    glossary_base_id: uuid.UUID | None,
    memory_base_id: uuid.UUID | None,
) -> None:
    file_records = (
        db.execute(select(FileRecord).where(FileRecord.project_id == project_id))
        .scalars()
        .all()
    )
    for file_record in file_records:
        if glossary_base_id is not None:
            glossary_ids = [
                value
                for value in _load_uuid_list(file_record.glossary_base_ids)
                if value != glossary_base_id
            ]
            file_record.glossary_base_ids = json.dumps(
                [str(value) for value in glossary_ids]
            )
        if memory_base_id is not None:
            memory_ids = [
                value
                for value in _load_uuid_list(file_record.collection_ids_json)
                if value != memory_base_id
            ]
            file_record.collection_ids_json = json.dumps(
                [str(value) for value in memory_ids]
            )
            if file_record.collection_id == memory_base_id:
                file_record.collection_id = memory_ids[0] if memory_ids else None


def _load_uuid_list(raw_value: str | None) -> list[uuid.UUID]:
    if not raw_value:
        return []
    try:
        decoded = json.loads(raw_value)
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []

    result: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for item in decoded:
        try:
            value = uuid.UUID(str(item))
        except (TypeError, ValueError):
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _build_glossary_base_name(project: Project, profile: ReferenceProfile) -> str:
    short_id = str(profile.id)[:8]
    return f"[参考] {project.name} #{short_id} 词汇"


def _build_memory_base_name(project: Project, profile: ReferenceProfile) -> str:
    short_id = str(profile.id)[:8]
    return f"[参考] {project.name} #{short_id} 记忆"


def _build_base_description(project: Project) -> str:
    return f"由参考文件分析自动生成（项目：{project.name}）"


__all__ = [
    "REFERENCE_ORIGIN",
    "sync_reference_profile_resources",
    "cleanup_reference_profile_resources",
    "attach_project_reference_bases_to_file",
]
