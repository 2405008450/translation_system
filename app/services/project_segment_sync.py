from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import FileRecord, Segment, SegmentRevision, User
from app.services.analytics_service import count_source_words, record_translation_metric_event
from app.services.automatic_numbering import (
    is_word_document_filename,
    strip_segment_automatic_numbering_prefix,
)
from app.services.normalizer import build_source_hash, normalize_text
from app.services.segment_status import apply_segment_status, resolve_unconfirmed_segment_status


PROJECT_SYNC_SOURCE = "project_sync"
PROJECT_SYNC_STATUS = "exact"
logger = logging.getLogger(__name__)

_SOURCE_PRIORITY = {
    "manual": 30,
    "llm": 20,
    "tm": 10,
    PROJECT_SYNC_SOURCE: 0,
}


@dataclass
class ProjectSegmentSyncSummary:
    filled_count: int = 0
    updated_count: int = 0
    conflict_count: int = 0
    affected_file_ids: set[UUID] = field(default_factory=set)
    current_file_segments: list[Segment] = field(default_factory=list)

    @property
    def affected_file_count(self) -> int:
        return len(self.affected_file_ids)

    def extend(self, other: "ProjectSegmentSyncSummary") -> None:
        self.filled_count += other.filled_count
        self.updated_count += other.updated_count
        self.conflict_count += other.conflict_count
        self.affected_file_ids.update(other.affected_file_ids)
        self.current_file_segments.extend(other.current_file_segments)

    def to_dict(self) -> dict[str, int]:
        return {
            "filled_count": self.filled_count,
            "updated_count": self.updated_count,
            "conflict_count": self.conflict_count,
            "affected_file_count": self.affected_file_count,
        }


@dataclass
class PretranslationLLMConvergenceSummary:
    duplicate_group_count: int = 0
    updated_count: int = 0
    affected_file_ids: set[UUID] = field(default_factory=set)

    @property
    def affected_file_count(self) -> int:
        return len(self.affected_file_ids)

    def to_dict(self) -> dict[str, int]:
        return {
            "duplicate_group_count": self.duplicate_group_count,
            "updated_count": self.updated_count,
            "affected_file_count": self.affected_file_count,
        }


@dataclass
class ProjectSyncDisableSummary:
    updated_count: int = 0
    disabled_count: int = 0
    cleared_count: int = 0
    updated_segments: list[Segment] = field(default_factory=list)

    def to_dict(self) -> dict[str, int]:
        return {
            "updated_count": self.updated_count,
            "disabled_count": self.disabled_count,
            "cleared_count": self.cleared_count,
        }


@dataclass(frozen=True)
class _SyncCandidate:
    segment: Segment
    source_hash: str
    target_text: str
    rank: tuple[int, int, datetime, str]


def empty_project_segment_sync_summary() -> ProjectSegmentSyncSummary:
    return ProjectSegmentSyncSummary()


def disable_project_sync_for_segments(
    segments: list[Segment],
    *,
    current_user=None,
) -> ProjectSyncDisableSummary:
    """关闭句段项目同步；对已由项目同步填充的译文同步清空。"""
    summary = ProjectSyncDisableSummary()

    for segment in segments:
        changed = False

        if not segment.project_sync_disabled:
            segment.project_sync_disabled = True
            summary.disabled_count += 1
            changed = True

        if (segment.source or "") == PROJECT_SYNC_SOURCE:
            if normalize_text(segment.target_text):
                summary.cleared_count += 1
            if _clear_project_synced_segment_fields(segment):
                changed = True

        if changed:
            if current_user is not None:
                segment.last_modified_by_id = current_user.id
            segment.version = int(segment.version or 1) + 1
            summary.updated_count += 1
            summary.updated_segments.append(segment)

    return summary


def enable_project_sync_for_segments(
    segments: list[Segment],
    *,
    current_user=None,
) -> ProjectSyncDisableSummary:
    """恢复句段项目同步，不改动现有译文。"""
    summary = ProjectSyncDisableSummary()

    for segment in segments:
        if not segment.project_sync_disabled:
            continue

        segment.project_sync_disabled = False
        if current_user is not None:
            segment.last_modified_by_id = current_user.id
        segment.version = int(segment.version or 1) + 1
        summary.updated_count += 1
        summary.updated_segments.append(segment)

    return summary


def sync_project_repeated_segments_from_file(
    db: Session,
    *,
    file_record: FileRecord,
    current_user=None,
) -> ProjectSegmentSyncSummary:
    if file_record.project_id is None:
        return ProjectSegmentSyncSummary()
    _backfill_project_segment_hashes(db, file_record=file_record)

    targets = (
        db.query(Segment)
        .filter(
            Segment.file_record_id == file_record.id,
            _segment_is_not_confirmed(),
            _segment_project_sync_enabled(),
        )
        .all()
    )
    if not targets:
        return ProjectSegmentSyncSummary()

    return _fill_targets_from_project_candidates(
        db,
        file_record=file_record,
        targets=targets,
        current_file_id=file_record.id,
        current_user=current_user,
    )


def sync_project_repeated_segments_from_segments(
    db: Session,
    *,
    file_record: FileRecord,
    source_segments: list[Segment],
    current_user=None,
) -> ProjectSegmentSyncSummary:
    if file_record.project_id is None:
        return ProjectSegmentSyncSummary()
    _backfill_project_segment_hashes(db, file_record=file_record)

    candidates = [
        _build_candidate(segment)
        for segment in source_segments
        if segment.file_record_id == file_record.id
    ]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return ProjectSegmentSyncSummary()

    summary = ProjectSegmentSyncSummary()
    for source_hash, grouped_candidates in _group_candidates(candidates).items():
        resolved = _resolve_candidate(grouped_candidates)
        if resolved is None:
            summary.conflict_count += 1
            continue

        targets = (
            db.query(Segment)
            .join(FileRecord, Segment.file_record_id == FileRecord.id)
            .filter(
                FileRecord.project_id == file_record.project_id,
                FileRecord.source_language == file_record.source_language,
                FileRecord.target_language == file_record.target_language,
                Segment.source_hash == source_hash,
                Segment.id != resolved.segment.id,
                _segment_project_sync_enabled(),
            )
            .order_by(Segment.id.asc())
            .with_for_update(skip_locked=True)
            .all()
        )
        _apply_candidate_to_targets(
            db,
            candidate=resolved,
            targets=targets,
            summary=summary,
            current_file_id=file_record.id,
            current_user=current_user,
        )

    if summary.filled_count or summary.updated_count:
        db.flush()
    return summary


def sync_project_segments_for_hash(
    db: Session,
    *,
    project_id: UUID,
    source_language: str,
    target_language: str,
    source_hash: str,
    current_user=None,
) -> ProjectSegmentSyncSummary:
    """对项目内同一 source_hash 的全部句段做一次收敛同步（outbox worker 使用）。

    候选与目标取同一批已锁定行：全局最高优先级（最新确认 > 人工 > LLM > TM）
    的译文胜出，天然不会用低优先级结果覆盖人工确认。
    """
    summary = ProjectSegmentSyncSummary()
    if not source_hash:
        return summary

    rows = (
        db.query(Segment)
        .join(FileRecord, Segment.file_record_id == FileRecord.id)
        .filter(
            FileRecord.project_id == project_id,
            func.coalesce(FileRecord.source_language, "") == (source_language or ""),
            func.coalesce(FileRecord.target_language, "") == (target_language or ""),
            Segment.source_hash == source_hash,
            _segment_project_sync_enabled(),
        )
        .order_by(Segment.id.asc())
        .with_for_update(of=Segment)
        .all()
    )
    if len(rows) < 2:
        return summary

    candidates = [
        candidate
        for candidate in (_build_candidate(row) for row in rows)
        if candidate is not None
    ]
    resolved = _resolve_candidate(candidates)
    if resolved is None:
        if candidates:
            summary.conflict_count += 1
        return summary

    targets = [row for row in rows if row.id != resolved.segment.id]
    _apply_candidate_to_targets(
        db,
        candidate=resolved,
        targets=targets,
        summary=summary,
        current_file_id=resolved.segment.file_record_id,
        current_user=current_user,
    )
    if summary.filled_count or summary.updated_count:
        db.flush()
    return summary


def run_project_sync_for_segment_ids(
    file_record_id: UUID,
    segment_ids: list[UUID],
    current_user_id: UUID | None = None,
) -> ProjectSegmentSyncSummary:
    """在独立 DB session 中执行项目重复句段同步，供后台 worker 调用。"""
    unique_segment_ids = list(dict.fromkeys(segment_ids))
    if not unique_segment_ids:
        return ProjectSegmentSyncSummary()

    with SessionLocal() as db:
        file_record = db.query(FileRecord).filter(FileRecord.id == file_record_id).first()
        if not file_record:
            return ProjectSegmentSyncSummary()
        current_user = (
            db.query(User).filter(User.id == current_user_id).first()
            if current_user_id is not None
            else None
        )
        segments = (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record_id,
                Segment.id.in_(unique_segment_ids),
            )
            .all()
        )
        if not segments:
            return ProjectSegmentSyncSummary()

        try:
            summary = sync_project_repeated_segments_from_segments(
                db,
                file_record=file_record,
                source_segments=segments,
                current_user=current_user,
            )
            db.commit()
            if summary.filled_count or summary.updated_count or summary.conflict_count:
                logger.info(
                    "project sync completed file_record_id=%s segments=%s filled=%s updated=%s conflicts=%s affected_files=%s",
                    file_record_id,
                    len(unique_segment_ids),
                    summary.filled_count,
                    summary.updated_count,
                    summary.conflict_count,
                    summary.affected_file_count,
                )
            return summary
        except Exception:
            db.rollback()
            logger.exception(
                "project sync failed file_record_id=%s segments=%s",
                file_record_id,
                len(unique_segment_ids),
            )
            raise


def backfill_segment_source_hashes(db: Session, *, batch_size: int = 500) -> int:
    rows = (
        db.query(Segment)
        .filter(or_(Segment.source_hash.is_(None), Segment.source_hash == ""))
        .limit(batch_size)
        .all()
    )
    for segment in rows:
        segment.source_hash = build_source_hash(segment.source_text)
    if rows:
        db.flush()
    return len(rows)


def converge_pretranslation_llm_segments(
    db: Session,
    *,
    project_id: UUID,
    ordered_file_record_ids: list[UUID],
    current_user: User | None = None,
    updated_since: datetime | None = None,
) -> PretranslationLLMConvergenceSummary:
    """统一一次多文件预翻译批次中的跨文件重复原文译文。

    只覆盖本批次新写入的、未确认且来源为 LLM 的译文。标准译文依次取：
    已确认译文、精确匹配上下文、用户选择顺序中最靠前的 LLM 结果。
    """
    summary = PretranslationLLMConvergenceSummary()
    ordered_file_record_ids = list(dict.fromkeys(ordered_file_record_ids))
    if len(ordered_file_record_ids) < 2:
        return summary

    file_records = (
        db.query(FileRecord)
        .filter(
            FileRecord.project_id == project_id,
            FileRecord.id.in_(ordered_file_record_ids),
        )
        .all()
    )
    file_records_by_id = {file_record.id: file_record for file_record in file_records}
    valid_file_ids = [file_id for file_id in ordered_file_record_ids if file_id in file_records_by_id]
    if len(valid_file_ids) < 2:
        return summary

    file_order = {file_id: index for index, file_id in enumerate(valid_file_ids)}
    batch_segments = (
        db.query(Segment)
        .filter(
            Segment.file_record_id.in_(valid_file_ids),
            _segment_project_sync_enabled(),
            func.trim(func.coalesce(Segment.target_text, "")) != "",
        )
        .with_for_update(of=Segment)
        .all()
    )
    if not batch_segments:
        return summary

    hashes_changed = False
    grouped_segments: dict[tuple[str, str, str], list[Segment]] = {}
    for segment in batch_segments:
        if not segment.source_hash:
            segment.source_hash = build_source_hash(segment.source_text)
            hashes_changed = True
        if not segment.source_hash:
            continue
        file_record = file_records_by_id.get(segment.file_record_id)
        if file_record is None:
            continue
        key = (
            file_record.source_language or "",
            file_record.target_language or "",
            segment.source_hash,
        )
        grouped_segments.setdefault(key, []).append(segment)

    if hashes_changed:
        db.flush()

    candidate_keys = {
        key
        for key, segments in grouped_segments.items()
        if len({segment.file_record_id for segment in segments}) >= 2
        and any(_is_batch_llm_convergence_target(segment, updated_since) for segment in segments)
    }
    if not candidate_keys:
        return summary

    backfilled_pairs: set[tuple[str, str]] = set()
    for file_record in file_records:
        language_pair = (file_record.source_language or "", file_record.target_language or "")
        if language_pair in backfilled_pairs:
            continue
        _backfill_project_segment_hashes(db, file_record=file_record)
        backfilled_pairs.add(language_pair)

    confirmed_by_key: dict[tuple[str, str, str], list[Segment]] = {}
    confirmed_rows = (
        db.query(Segment)
        .join(FileRecord, Segment.file_record_id == FileRecord.id)
        .filter(
            FileRecord.project_id == project_id,
            Segment.source_hash.in_({key[2] for key in candidate_keys}),
            Segment.status == "confirmed",
            _segment_project_sync_enabled(),
            func.trim(func.coalesce(Segment.target_text, "")) != "",
        )
        .all()
    )
    for segment in confirmed_rows:
        file_record = segment.file_record
        key = (
            file_record.source_language or "",
            file_record.target_language or "",
            segment.source_hash or "",
        )
        if key in candidate_keys:
            confirmed_by_key.setdefault(key, []).append(segment)

    for key in sorted(candidate_keys):
        segments = grouped_segments[key]
        targets = [
            segment
            for segment in segments
            if _is_batch_llm_convergence_target(segment, updated_since)
        ]
        canonical = _resolve_pretranslation_convergence_candidate(
            segments=segments,
            confirmed_segments=confirmed_by_key.get(key, []),
            targets=targets,
            file_order=file_order,
        )
        if canonical is None:
            continue

        canonical_candidate = _build_candidate(canonical)
        canonical_text = canonical_candidate.target_text if canonical_candidate else (canonical.target_text or "")
        if not normalize_text(canonical_text):
            continue

        summary.duplicate_group_count += 1
        for target in targets:
            if target.id == canonical.id or (target.target_text or "") == canonical_text:
                continue
            _apply_pretranslation_convergence_translation(
                db,
                target=target,
                canonical=canonical,
                canonical_text=canonical_text,
                current_user=current_user,
            )
            summary.updated_count += 1
            summary.affected_file_ids.add(target.file_record_id)

    if summary.updated_count:
        db.flush()
    return summary


def _is_batch_llm_convergence_target(segment: Segment, updated_since: datetime | None) -> bool:
    if (segment.source or "") != "llm" or segment.status == "confirmed":
        return False
    if segment.project_sync_disabled or not normalize_text(segment.target_text):
        return False
    if updated_since is None:
        return True
    return segment.updated_at is not None and segment.updated_at >= updated_since


def _resolve_pretranslation_convergence_candidate(
    *,
    segments: list[Segment],
    confirmed_segments: list[Segment],
    targets: list[Segment],
    file_order: dict[UUID, int],
) -> Segment | None:
    if confirmed_segments:
        return max(confirmed_segments, key=_confirmed_convergence_rank)

    exact_segments = [segment for segment in segments if segment.status == "exact"]
    if exact_segments:
        return min(exact_segments, key=lambda segment: _batch_segment_order(segment, file_order))

    if targets:
        return min(targets, key=lambda segment: _batch_segment_order(segment, file_order))
    return None


def _confirmed_convergence_rank(segment: Segment) -> tuple[int, datetime, str]:
    return (
        1 if (segment.source or "") == "manual" else 0,
        segment.confirmed_at or segment.updated_at or datetime.min,
        str(segment.id),
    )


def _batch_segment_order(segment: Segment, file_order: dict[UUID, int]) -> tuple[int, int, int, int, int, str]:
    fallback_position = 2**31 - 1

    def position(value: int | None) -> int:
        return int(value) if value is not None and int(value) >= 0 else fallback_position

    return (
        file_order.get(segment.file_record_id, fallback_position),
        position(segment.display_index),
        position(segment.sequence_index),
        position(segment.block_index),
        position(segment.row_index),
        str(segment.sentence_id or segment.id),
    )


def _apply_pretranslation_convergence_translation(
    db: Session,
    *,
    target: Segment,
    canonical: Segment,
    canonical_text: str,
    current_user: User | None,
) -> None:
    before_text = target.target_text or ""
    target.target_text = canonical_text
    target.target_html = None
    target.version = int(target.version or 1) + 1
    target.source_word_count = target.source_word_count or count_source_words(target.source_text)
    if current_user is not None:
        target.last_modified_by_id = current_user.id

    if (canonical.source or "") == "llm":
        target.source = "llm"
        target.llm_provider = canonical.llm_provider
        target.llm_model = canonical.llm_model
        target.project_sync_source_segment_id = None
        target.project_sync_source_file_record_id = None
        apply_segment_status(target, resolve_unconfirmed_segment_status(target))
    else:
        target.source = PROJECT_SYNC_SOURCE
        target.llm_provider = None
        target.llm_model = None
        target.project_sync_source_segment_id = canonical.id
        target.project_sync_source_file_record_id = canonical.file_record_id
        target.score = 1.0
        target.matched_source_text = canonical.source_text
        apply_segment_status(target, PROJECT_SYNC_STATUS)

    db.add(SegmentRevision(
        file_record_id=target.file_record_id,
        segment_id=target.id,
        sentence_id=target.sentence_id,
        before_text=before_text,
        after_text=canonical_text,
        source=PROJECT_SYNC_SOURCE,
        status="pending",
        author_id=current_user.id if current_user is not None else None,
    ))
    record_translation_metric_event(
        db,
        segment=target,
        before_text=before_text,
        after_text=canonical_text,
        source=PROJECT_SYNC_SOURCE,
        current_user=current_user,
    )


def _fill_targets_from_project_candidates(
    db: Session,
    *,
    file_record: FileRecord,
    targets: list[Segment],
    current_file_id: UUID,
    current_user,
) -> ProjectSegmentSyncSummary:
    target_hashes = _ensure_segment_hashes(targets)
    if not target_hashes:
        return ProjectSegmentSyncSummary()

    candidate_rows = (
        db.query(Segment)
        .join(FileRecord, Segment.file_record_id == FileRecord.id)
        .filter(
            FileRecord.project_id == file_record.project_id,
            FileRecord.source_language == file_record.source_language,
            FileRecord.target_language == file_record.target_language,
            Segment.file_record_id != file_record.id,
            Segment.source_hash.in_(list(target_hashes)),
            _segment_project_sync_enabled(),
            func.trim(func.coalesce(Segment.target_text, "")) != "",
        )
        .order_by(Segment.id.asc())
        .with_for_update(skip_locked=True)
        .all()
    )
    candidates = [candidate for candidate in (_build_candidate(row) for row in candidate_rows) if candidate is not None]
    candidates_by_hash = _group_candidates(candidates)

    targets_by_hash: dict[str, list[Segment]] = {}
    for target in targets:
        if target.source_hash:
            targets_by_hash.setdefault(target.source_hash, []).append(target)

    summary = ProjectSegmentSyncSummary()
    for source_hash, grouped_targets in targets_by_hash.items():
        resolved = _resolve_candidate(candidates_by_hash.get(source_hash, []))
        if resolved is None:
            if candidates_by_hash.get(source_hash):
                summary.conflict_count += 1
            continue
        _apply_candidate_to_targets(
            db,
            candidate=resolved,
            targets=grouped_targets,
            summary=summary,
            current_file_id=current_file_id,
            current_user=current_user,
        )

    if summary.filled_count or summary.updated_count:
        db.flush()
    return summary


def _apply_candidate_to_targets(
    db: Session,
    *,
    candidate: _SyncCandidate,
    targets: list[Segment],
    summary: ProjectSegmentSyncSummary,
    current_file_id: UUID,
    current_user,
) -> None:
    for target in targets:
        if target.id == candidate.segment.id:
            continue
        if target.project_sync_disabled:
            continue

        before_text = target.target_text or ""
        before_text_is_empty = not normalize_text(before_text)
        if before_text == candidate.target_text:
            if _set_project_sync_origin(target, candidate):
                target.version = int(target.version or 1) + 1
                summary.updated_count += 1
                summary.affected_file_ids.add(target.file_record_id)
                if target.file_record_id == current_file_id:
                    summary.current_file_segments.append(target)
            continue

        target.target_text = candidate.target_text
        target.target_html = None
        apply_segment_status(
            target,
            "confirmed" if target.status == "confirmed" else PROJECT_SYNC_STATUS,
        )
        target.source = PROJECT_SYNC_SOURCE
        target.project_sync_source_segment_id = candidate.segment.id
        target.project_sync_source_file_record_id = candidate.segment.file_record_id
        target.last_modified_by_id = current_user.id if current_user is not None else None
        target.score = 1.0
        target.matched_source_text = candidate.segment.source_text
        target.version = int(target.version or 1) + 1
        target.source_word_count = target.source_word_count or count_source_words(target.source_text)
        record_translation_metric_event(
            db,
            segment=target,
            before_text=before_text,
            after_text=candidate.target_text,
            source=PROJECT_SYNC_SOURCE,
            current_user=current_user,
        )
        if before_text_is_empty:
            summary.filled_count += 1
        else:
            summary.updated_count += 1
        summary.affected_file_ids.add(target.file_record_id)
        if target.file_record_id == current_file_id:
            summary.current_file_segments.append(target)


def _build_candidate(segment: Segment) -> _SyncCandidate | None:
    if not normalize_text(segment.target_text):
        return None
    if segment.project_sync_disabled:
        return None
    source_hash = segment.source_hash or build_source_hash(segment.source_text)
    if not source_hash:
        return None
    confirmed_priority = 1 if segment.status == "confirmed" else 0
    source_priority = _SOURCE_PRIORITY.get(segment.source or "", -1)
    # 已确认句段以确认时间决胜（最新确认胜出）；未确认沿用更新时间。
    if confirmed_priority:
        updated_at = getattr(segment, "confirmed_at", None) or segment.updated_at or datetime.min
    else:
        updated_at = segment.updated_at or datetime.min
    filename = getattr(getattr(segment, "file_record", None), "filename", None)
    target_text = (
        strip_segment_automatic_numbering_prefix(
            segment,
            segment.target_text,
            reference_texts=[segment.matched_source_text],
        )
        if is_word_document_filename(filename)
        else segment.target_text or ""
    )
    if not normalize_text(target_text):
        return None
    return _SyncCandidate(
        segment=segment,
        source_hash=source_hash,
        target_text=target_text,
        rank=(confirmed_priority, source_priority, updated_at, str(segment.id)),
    )


def _set_project_sync_origin(segment: Segment, candidate: _SyncCandidate) -> bool:
    if (segment.source or "") != PROJECT_SYNC_SOURCE:
        return False
    changed = False
    if segment.project_sync_source_segment_id != candidate.segment.id:
        segment.project_sync_source_segment_id = candidate.segment.id
        changed = True
    if segment.project_sync_source_file_record_id != candidate.segment.file_record_id:
        segment.project_sync_source_file_record_id = candidate.segment.file_record_id
        changed = True
    return changed


def _clear_project_synced_segment_fields(segment: Segment) -> bool:
    changed = False
    fields = {
        "target_text": "",
        "target_html": None,
        "status": "none",
        "confirmed_at": None,
        "source": "none",
        "score": 0.0,
        "matched_source_text": None,
        "matched_collection_name": None,
        "matched_creator_name": None,
        "matched_created_at": None,
        "matched_updated_at": None,
        "llm_provider": None,
        "llm_model": None,
        "project_sync_source_segment_id": None,
        "project_sync_source_file_record_id": None,
    }
    for field_name, next_value in fields.items():
        if getattr(segment, field_name) != next_value:
            setattr(segment, field_name, next_value)
            changed = True
    return changed


def _resolve_candidate(candidates: list[_SyncCandidate]) -> _SyncCandidate | None:
    if not candidates:
        return None
    best_rank = max(candidate.rank for candidate in candidates)
    best_candidates = [candidate for candidate in candidates if candidate.rank == best_rank]
    translations = {candidate.target_text for candidate in best_candidates}
    if len(translations) > 1:
        return None
    return best_candidates[0]


def _group_candidates(candidates: list[_SyncCandidate]) -> dict[str, list[_SyncCandidate]]:
    grouped: dict[str, list[_SyncCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.source_hash, []).append(candidate)
    return grouped


def _ensure_segment_hashes(segments: list[Segment]) -> set[str]:
    hashes: set[str] = set()
    for segment in segments:
        if not segment.source_hash:
            segment.source_hash = build_source_hash(segment.source_text)
        if segment.source_hash:
            hashes.add(segment.source_hash)
    return hashes


def _backfill_project_segment_hashes(db: Session, *, file_record: FileRecord) -> None:
    rows = (
        db.query(Segment)
        .join(FileRecord, Segment.file_record_id == FileRecord.id)
        .filter(
            FileRecord.project_id == file_record.project_id,
            FileRecord.source_language == file_record.source_language,
            FileRecord.target_language == file_record.target_language,
            or_(Segment.source_hash.is_(None), Segment.source_hash == ""),
        )
        .all()
    )
    for segment in rows:
        segment.source_hash = build_source_hash(segment.source_text)
    if rows:
        db.flush()


def _segment_is_not_confirmed():
    return or_(Segment.status.is_(None), Segment.status != "confirmed")


def _segment_project_sync_enabled():
    return or_(Segment.project_sync_disabled.is_(False), Segment.project_sync_disabled.is_(None))
