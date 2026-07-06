from __future__ import annotations

import logging
import math
import re
from copy import deepcopy
from threading import Lock
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import String, case, cast, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models import (
    FileRecord,
    Project,
    Segment,
    TranslationMetricEvent,
    User,
    UserActivityDaily,
)


logger = logging.getLogger(__name__)
_BACKFILL_LOCK = Lock()
_BACKFILL_COMPLETED = False
_DASHBOARD_CACHE_LOCK = Lock()
_DASHBOARD_CACHE_TTL = timedelta(seconds=15)
_DASHBOARD_CACHE: dict[str, tuple[datetime, dict]] = {}
_USER_ACTIVITY_LOCK = Lock()
_USER_ACTIVITY_WRITE_INTERVAL = timedelta(seconds=60)
_USER_ACTIVITY_LAST_WRITTEN: dict[tuple[UUID, date], datetime] = {}

SOURCE_WORD_PATTERN = re.compile(
    r"[A-Za-z0-9]+|"
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\uac00-\ud7af\U00020000-\U0002a6df\U0002a700-\U0002ebef]"
)

SOURCE_LABELS = {
    "manual": "人工",
    "tm": "记忆库",
    "llm": "LLM",
    "none": "未匹配",
}

POSTGRES_SOURCE_WORD_PATTERN = (
    r"[A-Za-z0-9]+|"
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\uac00-\ud7af]"
)


def count_source_words(text: str | None) -> int:
    """按看板口径统计源文工作量。"""
    if not text:
        return 0
    return len(SOURCE_WORD_PATTERN.findall(text))


def record_user_activity_safely(user_id: UUID) -> None:
    today = date.today()
    now = datetime.now()
    cache_key = (user_id, today)
    with _USER_ACTIVITY_LOCK:
        last_written = _USER_ACTIVITY_LAST_WRITTEN.get(cache_key)
        if last_written is not None and now - last_written < _USER_ACTIVITY_WRITE_INTERVAL:
            return
        _USER_ACTIVITY_LAST_WRITTEN[cache_key] = now

    try:
        with SessionLocal() as db:
            record_user_activity(db, user_id, activity_date=today)
            db.commit()
    except Exception:  # noqa: BLE001
        with _USER_ACTIVITY_LOCK:
            _USER_ACTIVITY_LAST_WRITTEN.pop(cache_key, None)
        logger.debug("failed to record user activity user_id=%s", user_id, exc_info=True)


def record_user_activity(db: Session, user_id: UUID, activity_date: date | None = None) -> UserActivityDaily:
    current_date = activity_date or date.today()
    now = datetime.now()
    existing = (
        db.query(UserActivityDaily)
        .filter(
            UserActivityDaily.user_id == user_id,
            UserActivityDaily.activity_date == current_date,
        )
        .first()
    )
    if existing is not None:
        existing.request_count += 1
        existing.last_seen_at = now
        db.add(existing)
        _clear_dashboard_cache()
        return existing

    activity = UserActivityDaily(
        user_id=user_id,
        activity_date=current_date,
        request_count=1,
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(activity)
    try:
        db.flush()
        _clear_dashboard_cache()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(UserActivityDaily)
            .filter(
                UserActivityDaily.user_id == user_id,
                UserActivityDaily.activity_date == current_date,
            )
            .first()
        )
        if existing is None:
            raise
        existing.request_count += 1
        existing.last_seen_at = now
        db.add(existing)
        return existing
    return activity


def record_translation_metric_event(
    db: Session,
    *,
    segment: Segment,
    before_text: str | None,
    after_text: str | None,
    source: str,
    current_user: User | None = None,
    event_key: str | None = None,
    created_at: datetime | None = None,
) -> TranslationMetricEvent | None:
    normalized_source = (source or "manual").strip().lower() or "manual"
    if normalized_source != "llm" and (before_text or "") == (after_text or ""):
        return None
    if not (after_text or "").strip():
        return None
    if event_key and _event_key_exists(db, event_key):
        return None

    file_record = segment.file_record or db.query(FileRecord).filter(FileRecord.id == segment.file_record_id).first()
    project = file_record.project if file_record and file_record.project else None
    word_count = segment.source_word_count or count_source_words(segment.source_text)
    if segment.source_word_count != word_count:
        segment.source_word_count = word_count

    event = TranslationMetricEvent(
        event_key=event_key,
        project_id=file_record.project_id if file_record else None,
        file_record_id=file_record.id if file_record else segment.file_record_id,
        segment_id=segment.id,
        user_id=current_user.id if current_user else None,
        source=normalized_source,
        source_language=(file_record.source_language if file_record else None)
        or (project.source_language if project else None),
        target_language=(file_record.target_language if file_record else None)
        or (project.target_language if project else None),
        source_word_count=max(int(word_count or 0), 0),
        target_was_empty=not (before_text or "").strip(),
    )
    if created_at is not None:
        event.created_at = created_at
    db.add(event)
    _clear_dashboard_cache()
    return event


def run_analytics_backfill_once() -> None:
    global _BACKFILL_COMPLETED
    if _BACKFILL_COMPLETED:
        return
    if not _BACKFILL_LOCK.acquire(blocking=False):
        return
    try:
        with SessionLocal() as db:
            ensure_analytics_backfill(db)
        _BACKFILL_COMPLETED = True
    except Exception:  # noqa: BLE001
        logger.exception("analytics backfill failed")
    finally:
        _BACKFILL_LOCK.release()


def ensure_analytics_backfill(db: Session, batch_size: int = 2000) -> None:
    changed = False
    while True:
        word_count_updates = _backfill_segment_word_counts(db, batch_size=batch_size)
        event_inserts = _backfill_translation_events(db, batch_size=batch_size)
        if word_count_updates > 0 or event_inserts > 0:
            changed = True
        db.commit()
        if word_count_updates == 0 and event_inserts == 0:
            break
    if changed:
        _clear_dashboard_cache()


def get_dashboard_payload(db: Session, granularity: str = "day") -> dict:
    normalized_granularity = "month" if granularity == "month" else "day"
    cache_key = f"{id(db.get_bind())}:{normalized_granularity}"
    cached_payload = _get_cached_dashboard_payload(cache_key)
    if cached_payload is not None:
        return cached_payload

    labels, start_date = _build_bucket_labels(normalized_granularity)
    start_at = datetime.combine(start_date, time.min)

    language_pairs = _build_language_pairs(db)
    source_breakdown = _build_source_breakdown(db)
    translated_source_words = sum(
        item["translated_source_word_count"]
        for item in language_pairs
    )
    llm_processed_source_words = sum(
        item["source_word_count"]
        for item in source_breakdown
        if item["source"] == "llm"
    )
    summary = _build_summary(
        db,
        translated_source_words=translated_source_words,
        llm_processed_source_words=llm_processed_source_words,
    )
    series = _build_series(db, labels, start_date, start_at, normalized_granularity)
    user_stats = _build_user_stats(db, start_date=start_date, start_at=start_at)

    payload = {
        "granularity": normalized_granularity,
        "summary": summary,
        "series": series,
        "language_pairs": language_pairs,
        "source_breakdown": source_breakdown,
        "user_stats": user_stats,
    }
    _set_cached_dashboard_payload(cache_key, payload)
    return deepcopy(payload)


def _get_cached_dashboard_payload(granularity: str) -> dict | None:
    now = datetime.now()
    with _DASHBOARD_CACHE_LOCK:
        cached = _DASHBOARD_CACHE.get(granularity)
        if cached is None:
            return None
        cached_at, payload = cached
        if now - cached_at > _DASHBOARD_CACHE_TTL:
            _DASHBOARD_CACHE.pop(granularity, None)
            return None
        return deepcopy(payload)


def _set_cached_dashboard_payload(granularity: str, payload: dict) -> None:
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE[granularity] = (datetime.now(), deepcopy(payload))


def _clear_dashboard_cache() -> None:
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE.clear()


def _event_key_exists(db: Session, event_key: str) -> bool:
    return (
        db.query(TranslationMetricEvent.id)
        .filter(TranslationMetricEvent.event_key == event_key)
        .first()
        is not None
    )


def _backfill_segment_word_counts(db: Session, batch_size: int) -> int:
    if db.get_bind().dialect.name == "postgresql":
        rows = db.execute(
            text(
                """
                WITH batch AS (
                    SELECT id, source_text
                    FROM segments
                    WHERE source_word_count = 0
                      AND source_text <> ''
                      AND source_text ~ :pattern
                    ORDER BY id
                    LIMIT :batch_size
                ),
                counts AS (
                    SELECT batch.id, COUNT(*)::INTEGER AS word_count
                    FROM batch
                    JOIN LATERAL regexp_matches(batch.source_text, :pattern, 'g') AS matches(match) ON TRUE
                    GROUP BY batch.id
                )
                UPDATE segments AS segment
                SET source_word_count = counts.word_count
                FROM counts
                WHERE segment.id = counts.id
                RETURNING segment.id
                """
            ),
            {"batch_size": batch_size, "pattern": POSTGRES_SOURCE_WORD_PATTERN},
        ).all()
        return len(rows)

    segments = (
        db.query(Segment)
        .filter(
            Segment.source_word_count == 0,
            Segment.source_text != "",
        )
        .order_by(Segment.id.asc())
        .limit(batch_size)
        .all()
    )
    updated = 0
    for segment in segments:
        word_count = count_source_words(segment.source_text)
        if word_count > 0 and segment.source_word_count != word_count:
            segment.source_word_count = word_count
            db.add(segment)
            updated += 1
    db.flush()
    return updated


def _backfill_translation_events(db: Session, batch_size: int) -> int:
    if db.get_bind().dialect.name == "postgresql":
        rows = db.execute(
            text(
                """
                WITH batch AS (
                    SELECT
                        s.id AS segment_id,
                        s.file_record_id,
                        fr.project_id,
                        s.source,
                        COALESCE(fr.source_language, p.source_language) AS source_language,
                        COALESCE(fr.target_language, p.target_language) AS target_language,
                        s.source_word_count,
                        COALESCE(s.updated_at, s.created_at, NOW()) AS created_at
                    FROM segments AS s
                    JOIN file_records AS fr ON fr.id = s.file_record_id
                    LEFT JOIN projects AS p ON p.id = fr.project_id
                    LEFT JOIN translation_metric_events AS e
                        ON e.event_key = 'backfill:' || s.id::text
                    WHERE s.target_text <> ''
                      AND s.source_word_count > 0
                      AND e.id IS NULL
                    ORDER BY s.updated_at NULLS LAST, s.id
                    LIMIT :batch_size
                )
                INSERT INTO translation_metric_events (
                    event_key,
                    project_id,
                    file_record_id,
                    segment_id,
                    source,
                    source_language,
                    target_language,
                    source_word_count,
                    target_was_empty,
                    created_at
                )
                SELECT
                    'backfill:' || batch.segment_id::text,
                    batch.project_id,
                    batch.file_record_id,
                    batch.segment_id,
                    COALESCE(NULLIF(batch.source, ''), 'tm'),
                    batch.source_language,
                    batch.target_language,
                    batch.source_word_count,
                    TRUE,
                    batch.created_at
                FROM batch
                ON CONFLICT DO NOTHING
                RETURNING id
                """
            ),
            {"batch_size": batch_size},
        ).all()
        return len(rows)

    translated_segments = (
        db.query(Segment)
        .options(joinedload(Segment.file_record).joinedload(FileRecord.project))
        .outerjoin(
            TranslationMetricEvent,
            TranslationMetricEvent.event_key == ("backfill:" + cast(Segment.id, String)),
        )
        .filter(
            Segment.target_text != "",
            Segment.source_word_count > 0,
            TranslationMetricEvent.id.is_(None),
        )
        .order_by(Segment.updated_at.asc(), Segment.id.asc())
        .limit(batch_size)
        .all()
    )
    inserted = 0
    for segment in translated_segments:
        event = record_translation_metric_event(
            db,
            segment=segment,
            before_text="",
            after_text=segment.target_text,
            source=segment.source or "tm",
            event_key=f"backfill:{segment.id}",
            created_at=segment.updated_at or segment.created_at,
        )
        if event is not None:
            inserted += 1
    db.flush()
    return inserted


def _build_summary(
    db: Session,
    *,
    translated_source_words: int | None = None,
    llm_processed_source_words: int | None = None,
) -> dict:
    if db.get_bind().dialect.name == "postgresql":
        row = db.execute(
            text(
                """
                SELECT
                    (SELECT COUNT(*) FROM projects) AS total_projects,
                    (SELECT COUNT(*) FROM file_records) AS total_files,
                    (SELECT COALESCE(SUM(source_word_count), 0) FROM segments) AS total_source_words,
                    (
                        SELECT COUNT(*)
                        FROM user_activity_daily
                        WHERE activity_date = :today
                    ) AS active_users_today
                """
            ),
            {"today": date.today()},
        ).mappings().one()
        total_source_words = int(row["total_source_words"] or 0)
        if translated_source_words is None:
            translated_source_words = int(
                db.query(func.coalesce(func.sum(Segment.source_word_count), 0))
                .filter(Segment.target_text != "", Segment.source_word_count > 0)
                .scalar()
                or 0
            )
        if llm_processed_source_words is None:
            llm_processed_source_words = int(
                db.query(func.coalesce(func.sum(TranslationMetricEvent.source_word_count), 0))
                .filter(TranslationMetricEvent.source == "llm")
                .scalar()
                or 0
            )
        translated_source_words = int(translated_source_words or 0)
        translation_progress = (
            round(translated_source_words / total_source_words * 100, 1)
            if total_source_words > 0
            else 0
        )
        return {
            "total_projects": int(row["total_projects"] or 0),
            "total_files": int(row["total_files"] or 0),
            "total_source_word_count": total_source_words,
            "translated_source_word_count": translated_source_words,
            "llm_processed_source_word_count": int(llm_processed_source_words or 0),
            "active_users_today": int(row["active_users_today"] or 0),
            "translation_progress": translation_progress,
        }

    total_projects = int(db.query(func.count(Project.id)).scalar() or 0)
    total_files = int(db.query(func.count(FileRecord.id)).scalar() or 0)
    total_source_words = int(db.query(func.coalesce(func.sum(Segment.source_word_count), 0)).scalar() or 0)
    if translated_source_words is None:
        translated_source_words = int(
            db.query(func.coalesce(func.sum(Segment.source_word_count), 0))
            .filter(Segment.target_text != "", Segment.source_word_count > 0)
            .scalar()
            or 0
        )
    if llm_processed_source_words is None:
        llm_processed_source_words = int(
            db.query(func.coalesce(func.sum(TranslationMetricEvent.source_word_count), 0))
            .filter(TranslationMetricEvent.source == "llm")
            .scalar()
            or 0
        )
    translated_source_words = int(translated_source_words or 0)
    llm_processed_source_words = int(llm_processed_source_words or 0)
    active_users_today = int(
        db.query(func.count(UserActivityDaily.id))
        .filter(UserActivityDaily.activity_date == date.today())
        .scalar()
        or 0
    )
    translation_progress = (
        round(translated_source_words / total_source_words * 100, 1)
        if total_source_words > 0
        else 0
    )
    return {
        "total_projects": total_projects,
        "total_files": total_files,
        "total_source_word_count": total_source_words,
        "translated_source_word_count": translated_source_words,
        "llm_processed_source_word_count": llm_processed_source_words,
        "active_users_today": active_users_today,
        "translation_progress": translation_progress,
    }


def _build_series(
    db: Session,
    labels: list[str],
    start_date: date,
    start_at: datetime,
    granularity: str,
) -> list[dict]:
    if db.get_bind().dialect.name == "postgresql":
        return _build_series_postgres(db, labels, start_date, start_at, granularity)

    project_counts: defaultdict[str, int] = defaultdict(int)
    for created_at, in db.query(Project.created_at).filter(Project.created_at >= start_at).all():
        project_counts[_bucket_key(created_at.date(), granularity)] += 1

    translated_words: defaultdict[str, int] = defaultdict(int)
    llm_words: defaultdict[str, int] = defaultdict(int)
    events = (
        db.query(
            TranslationMetricEvent.created_at,
            TranslationMetricEvent.source,
            TranslationMetricEvent.source_word_count,
            TranslationMetricEvent.target_was_empty,
        )
        .filter(TranslationMetricEvent.created_at >= start_at)
        .all()
    )
    for event_created_at, source, source_word_count, target_was_empty in events:
        key = _bucket_key(event_created_at.date(), granularity)
        if target_was_empty:
            translated_words[key] += int(source_word_count or 0)
        if source == "llm":
            llm_words[key] += int(source_word_count or 0)

    active_users: defaultdict[str, set[UUID]] = defaultdict(set)
    activities = (
        db.query(UserActivityDaily.activity_date, UserActivityDaily.user_id)
        .filter(UserActivityDaily.activity_date >= start_date)
        .all()
    )
    for activity_date, user_id in activities:
        active_users[_bucket_key(activity_date, granularity)].add(user_id)

    return [
        {
            "bucket": label,
            "project_created_count": project_counts[label],
            "translated_source_word_count": translated_words[label],
            "llm_processed_source_word_count": llm_words[label],
            "active_user_count": len(active_users[label]),
        }
        for label in labels
    ]


def _build_series_postgres(
    db: Session,
    labels: list[str],
    start_date: date,
    start_at: datetime,
    granularity: str,
) -> list[dict]:
    if granularity == "month":
        project_bucket_sql = "to_char(date_trunc('month', created_at), 'YYYY-MM')"
        event_bucket_sql = "to_char(date_trunc('month', created_at), 'YYYY-MM')"
        activity_bucket_sql = "to_char(date_trunc('month', activity_date::timestamp), 'YYYY-MM')"
    else:
        project_bucket_sql = "created_at::date::text"
        event_bucket_sql = "created_at::date::text"
        activity_bucket_sql = "activity_date::text"

    project_counts = {
        bucket: int(count or 0)
        for bucket, count in db.execute(
            text(
                f"""
                SELECT {project_bucket_sql} AS bucket, COUNT(*) AS count
                FROM projects
                WHERE created_at >= :start_at
                GROUP BY bucket
                """
            ),
            {"start_at": start_at},
        ).all()
    }

    event_rows = db.execute(
        text(
            f"""
            SELECT
                {event_bucket_sql} AS bucket,
                COALESCE(SUM(CASE WHEN target_was_empty THEN source_word_count ELSE 0 END), 0)
                    AS translated_source_word_count,
                COALESCE(SUM(CASE WHEN source = 'llm' THEN source_word_count ELSE 0 END), 0)
                    AS llm_processed_source_word_count
            FROM translation_metric_events
            WHERE created_at >= :start_at
            GROUP BY bucket
            """
        ),
        {"start_at": start_at},
    ).all()
    translated_words = {
        bucket: int(translated_source_word_count or 0)
        for bucket, translated_source_word_count, _ in event_rows
    }
    llm_words = {
        bucket: int(llm_processed_source_word_count or 0)
        for bucket, _, llm_processed_source_word_count in event_rows
    }

    active_users = {
        bucket: int(count or 0)
        for bucket, count in db.execute(
            text(
                f"""
                SELECT {activity_bucket_sql} AS bucket, COUNT(DISTINCT user_id) AS count
                FROM user_activity_daily
                WHERE activity_date >= :start_date
                GROUP BY bucket
                """
            ),
            {"start_date": start_date},
        ).all()
    }

    return [
        {
            "bucket": label,
            "project_created_count": project_counts.get(label, 0),
            "translated_source_word_count": translated_words.get(label, 0),
            "llm_processed_source_word_count": llm_words.get(label, 0),
            "active_user_count": active_users.get(label, 0),
        }
        for label in labels
    ]


def _build_user_stats(db: Session, *, start_date: date, start_at: datetime) -> list[dict]:
    stats_by_key: dict[str | None, dict] = {}
    user_id_values: dict[str, UUID | str] = {}

    def ensure_user_stat(user_id: UUID | str | None) -> dict:
        key = str(user_id) if user_id is not None else None
        if key not in stats_by_key:
            stats_by_key[key] = {
                "user_id": key,
                "username": "unassigned" if key is None else None,
                "nickname": "未归属/历史数据" if key is None else None,
                "role": None,
                "translator_type": None,
                "is_active": False,
                "display_name": "未归属/历史数据" if key is None else "未知用户",
                "active_day_count": 0,
                "request_count": 0,
                "estimated_active_minutes": 0,
                "first_seen_at": None,
                "last_seen_at": None,
                "new_source_word_count": 0,
                "modified_source_word_count": 0,
                "total_source_word_count": 0,
                "event_count": 0,
                "_activity_dates": set(),
                "_first_seen_at": None,
                "_last_seen_at": None,
            }
        if key is not None and user_id is not None:
            user_id_values[key] = user_id
        return stats_by_key[key]

    activities = (
        db.query(
            UserActivityDaily.user_id,
            UserActivityDaily.activity_date,
            UserActivityDaily.request_count,
            UserActivityDaily.first_seen_at,
            UserActivityDaily.last_seen_at,
        )
        .filter(UserActivityDaily.activity_date >= start_date)
        .all()
    )
    for user_id, activity_date, request_count, first_seen_at, last_seen_at in activities:
        stat = ensure_user_stat(user_id)
        stat["_activity_dates"].add(str(activity_date))
        stat["request_count"] += int(request_count or 0)
        stat["estimated_active_minutes"] += _estimate_active_minutes(
            first_seen_at,
            last_seen_at,
            request_count,
        )
        stat["_first_seen_at"] = _min_datetime(stat["_first_seen_at"], first_seen_at)
        stat["_last_seen_at"] = _max_datetime(stat["_last_seen_at"], last_seen_at)

    event_rows = (
        db.query(
            TranslationMetricEvent.user_id,
            func.count(TranslationMetricEvent.id),
            func.coalesce(
                func.sum(
                    case(
                        (
                            TranslationMetricEvent.target_was_empty.is_(True),
                            TranslationMetricEvent.source_word_count,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            TranslationMetricEvent.target_was_empty.is_(False),
                            TranslationMetricEvent.source_word_count,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .filter(TranslationMetricEvent.created_at >= start_at)
        .group_by(TranslationMetricEvent.user_id)
        .all()
    )
    for user_id, event_count, new_source_words, modified_source_words in event_rows:
        stat = ensure_user_stat(user_id)
        stat["event_count"] += int(event_count or 0)
        stat["new_source_word_count"] += int(new_source_words or 0)
        stat["modified_source_word_count"] += int(modified_source_words or 0)
        stat["total_source_word_count"] = (
            stat["new_source_word_count"] + stat["modified_source_word_count"]
        )

    if user_id_values:
        users = db.query(User).filter(User.id.in_(list(user_id_values.values()))).all()
        for user in users:
            key = str(user.id)
            stat = stats_by_key.get(key)
            if stat is None:
                continue
            display_name = (getattr(user, "nickname", None) or "").strip() or user.username
            stat.update(
                {
                    "username": user.username,
                    "nickname": getattr(user, "nickname", None),
                    "role": user.role,
                    "translator_type": getattr(user, "translator_type", None),
                    "is_active": bool(user.is_active),
                    "display_name": display_name,
                }
            )

    items: list[dict] = []
    for stat in stats_by_key.values():
        stat["active_day_count"] = len(stat.pop("_activity_dates"))
        first_seen_at = stat.pop("_first_seen_at")
        last_seen_at = stat.pop("_last_seen_at")
        stat["first_seen_at"] = first_seen_at.isoformat() if first_seen_at else None
        stat["last_seen_at"] = last_seen_at.isoformat() if last_seen_at else None
        items.append(stat)

    return sorted(
        items,
        key=lambda item: (
            -int(item["total_source_word_count"] or 0),
            -int(item["estimated_active_minutes"] or 0),
            -int(item["request_count"] or 0),
            item["display_name"] or "",
        ),
    )


def _estimate_active_minutes(
    first_seen_at: datetime | None,
    last_seen_at: datetime | None,
    request_count: int | None,
) -> int:
    if int(request_count or 0) <= 0:
        return 0
    if first_seen_at is None or last_seen_at is None:
        return 1
    elapsed_seconds = max((last_seen_at - first_seen_at).total_seconds(), 0)
    return max(1, math.ceil(elapsed_seconds / 60))


def _min_datetime(current_value: datetime | None, next_value: datetime | None) -> datetime | None:
    if next_value is None:
        return current_value
    if current_value is None or next_value < current_value:
        return next_value
    return current_value


def _max_datetime(current_value: datetime | None, next_value: datetime | None) -> datetime | None:
    if next_value is None:
        return current_value
    if current_value is None or next_value > current_value:
        return next_value
    return current_value


def _build_language_pairs(db: Session) -> list[dict]:
    pair_stats: dict[tuple[str | None, str | None], dict] = {}

    def ensure_pair(source_language: str | None, target_language: str | None) -> dict:
        key = (source_language, target_language)
        if key not in pair_stats:
            pair_stats[key] = {
                "source_language": source_language,
                "target_language": target_language,
                "project_ids": set(),
                "file_ids": set(),
                "translated_source_word_count": 0,
                "llm_processed_source_word_count": 0,
            }
        return pair_stats[key]

    projects = db.query(Project.id, Project.source_language, Project.target_language).all()
    for project_id, source_language, target_language in projects:
        ensure_pair(source_language, target_language)["project_ids"].add(project_id)

    files = (
        db.query(
            FileRecord.id,
            FileRecord.project_id,
            func.coalesce(FileRecord.source_language, Project.source_language),
            func.coalesce(FileRecord.target_language, Project.target_language),
        )
        .outerjoin(Project, Project.id == FileRecord.project_id)
        .all()
    )
    file_pair_by_id: dict[UUID, tuple[str | None, str | None]] = {}
    for file_record_id, project_id, source_language, target_language in files:
        file_pair_by_id[file_record_id] = (source_language, target_language)
        pair = ensure_pair(source_language, target_language)
        if project_id is not None:
            pair["project_ids"].add(project_id)
        pair["file_ids"].add(file_record_id)

    translated_rows = (
        db.query(Segment.file_record_id, func.coalesce(func.sum(Segment.source_word_count), 0))
        .filter(Segment.target_text != "", Segment.source_word_count > 0)
        .group_by(Segment.file_record_id)
        .all()
    )
    for file_record_id, word_count in translated_rows:
        source_language, target_language = file_pair_by_id.get(file_record_id, (None, None))
        ensure_pair(source_language, target_language)["translated_source_word_count"] += int(word_count or 0)

    llm_rows = (
        db.query(
            TranslationMetricEvent.source_language,
            TranslationMetricEvent.target_language,
            func.coalesce(func.sum(TranslationMetricEvent.source_word_count), 0),
        )
        .filter(TranslationMetricEvent.source == "llm")
        .group_by(TranslationMetricEvent.source_language, TranslationMetricEvent.target_language)
        .all()
    )
    for source_language, target_language, word_count in llm_rows:
        ensure_pair(source_language, target_language)["llm_processed_source_word_count"] += int(word_count or 0)

    items = []
    for pair in pair_stats.values():
        items.append(
            {
                "source_language": pair["source_language"],
                "target_language": pair["target_language"],
                "project_count": len(pair["project_ids"]),
                "file_count": len(pair["file_ids"]),
                "translated_source_word_count": pair["translated_source_word_count"],
                "llm_processed_source_word_count": pair["llm_processed_source_word_count"],
            }
        )
    return sorted(
        items,
        key=lambda item: (
            item["translated_source_word_count"],
            item["llm_processed_source_word_count"],
            item["project_count"],
        ),
        reverse=True,
    )


def _build_source_breakdown(db: Session) -> list[dict]:
    rows = (
        db.query(
            TranslationMetricEvent.source,
            func.count(TranslationMetricEvent.id),
            func.coalesce(func.sum(TranslationMetricEvent.source_word_count), 0),
        )
        .group_by(TranslationMetricEvent.source)
        .all()
    )
    return [
        {
            "source": source,
            "label": SOURCE_LABELS.get(source, source or "其他"),
            "event_count": int(event_count or 0),
            "source_word_count": int(source_word_count or 0),
        }
        for source, event_count, source_word_count in rows
    ]


def _build_bucket_labels(granularity: str) -> tuple[list[str], date]:
    today = date.today()
    if granularity == "month":
        first_of_month = today.replace(day=1)
        start_month = _add_months(first_of_month, -11)
        labels = [_month_key(_add_months(start_month, offset)) for offset in range(12)]
        return labels, start_month

    start_date = today - timedelta(days=29)
    labels = [(start_date + timedelta(days=offset)).isoformat() for offset in range(30)]
    return labels, start_date


def _bucket_key(value: date, granularity: str) -> str:
    if granularity == "month":
        return _month_key(value)
    return value.isoformat()


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _add_months(value: date, months: int) -> date:
    next_month = value.month + months
    year = value.year + (next_month - 1) // 12
    month = (next_month - 1) % 12 + 1
    return date(year, month, 1)
