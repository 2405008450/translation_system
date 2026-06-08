from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import FileRecord, Segment
from app.services.analytics_service import count_source_words, record_translation_metric_event
from app.services.normalizer import build_source_hash, normalize_text


PROJECT_SYNC_SOURCE = "project_sync"
PROJECT_SYNC_STATUS = "exact"

_SOURCE_PRIORITY = {
    "manual": 30,
    "llm": 20,
    "tm": 10,
    PROJECT_SYNC_SOURCE: 0,
}


@dataclass
class ProjectSegmentSyncSummary:
    filled_count: int = 0
    conflict_count: int = 0
    affected_file_ids: set[UUID] = field(default_factory=set)
    current_file_segments: list[Segment] = field(default_factory=list)

    @property
    def affected_file_count(self) -> int:
        return len(self.affected_file_ids)

    def extend(self, other: "ProjectSegmentSyncSummary") -> None:
        self.filled_count += other.filled_count
        self.conflict_count += other.conflict_count
        self.affected_file_ids.update(other.affected_file_ids)
        self.current_file_segments.extend(other.current_file_segments)

    def to_dict(self) -> dict[str, int]:
        return {
            "filled_count": self.filled_count,
            "conflict_count": self.conflict_count,
            "affected_file_count": self.affected_file_count,
        }


@dataclass(frozen=True)
class _SyncCandidate:
    segment: Segment
    source_hash: str
    target_text: str
    rank: tuple[int, int, datetime]


def empty_project_segment_sync_summary() -> ProjectSegmentSyncSummary:
    return ProjectSegmentSyncSummary()


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
            _segment_has_empty_target(),
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
                _segment_has_empty_target(),
                _segment_is_not_confirmed(),
                _segment_project_sync_enabled(),
            )
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

    if summary.filled_count:
        db.flush()
    return summary


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

    if summary.filled_count:
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
        if target.project_sync_disabled or target.status == "confirmed" or normalize_text(target.target_text):
            continue

        before_text = target.target_text or ""
        target.target_text = candidate.target_text
        target.target_html = None
        target.status = PROJECT_SYNC_STATUS
        target.source = PROJECT_SYNC_SOURCE
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
        summary.filled_count += 1
        summary.affected_file_ids.add(target.file_record_id)
        if target.file_record_id == current_file_id:
            summary.current_file_segments.append(target)


def _build_candidate(segment: Segment) -> _SyncCandidate | None:
    if not normalize_text(segment.target_text):
        return None
    if segment.project_sync_disabled and segment.source == PROJECT_SYNC_SOURCE:
        return None
    source_hash = segment.source_hash or build_source_hash(segment.source_text)
    if not source_hash:
        return None
    confirmed_priority = 1 if segment.status == "confirmed" else 0
    source_priority = _SOURCE_PRIORITY.get(segment.source or "", -1)
    updated_at = segment.updated_at or datetime.min
    return _SyncCandidate(
        segment=segment,
        source_hash=source_hash,
        target_text=segment.target_text or "",
        rank=(confirmed_priority, source_priority, updated_at),
    )


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


def _segment_has_empty_target():
    return func.trim(func.coalesce(Segment.target_text, "")) == ""


def _segment_is_not_confirmed():
    return or_(Segment.status.is_(None), Segment.status != "confirmed")


def _segment_project_sync_enabled():
    return or_(Segment.project_sync_disabled.is_(False), Segment.project_sync_disabled.is_(None))
