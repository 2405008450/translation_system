from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import FileRecord, User


PRE_TRANSLATE_OPERATION = "pre_translate"
FILE_OPERATION_LOCK_TIMEOUT_SECONDS = 10 * 60
FILE_OPERATION_TOKEN_HEADER = "X-File-Operation-Token"


def local_now() -> datetime:
    return datetime.now()


def get_file_operation_message(operation: str | None) -> str:
    if operation == PRE_TRANSLATE_OPERATION:
        return "文件正在预翻译中，请完成后再进入工作台编辑。"
    if operation:
        return "文件正在处理中，请稍后再编辑。"
    return ""


def is_file_operation_stale(file_record: FileRecord, now: datetime | None = None) -> bool:
    if not file_record.active_operation:
        return False
    if file_record.active_operation_updated_at is None:
        return True

    current_time = now or local_now()
    return current_time - file_record.active_operation_updated_at > timedelta(
        seconds=FILE_OPERATION_LOCK_TIMEOUT_SECONDS,
    )


def clear_file_operation_lock(file_record: FileRecord) -> None:
    file_record.active_operation = None
    file_record.active_operation_token = None
    file_record.active_operation_updated_at = None
    file_record.active_operation_user_id = None


def clear_stale_file_operation_lock(
    db: Session,
    file_record: FileRecord,
    now: datetime | None = None,
) -> bool:
    if not is_file_operation_stale(file_record, now=now):
        return False
    clear_file_operation_lock(file_record)
    db.flush()
    return True


def serialize_file_operation_state(file_record: FileRecord) -> dict[str, str | bool | None]:
    active_operation = file_record.active_operation
    is_locked = bool(active_operation)
    return {
        "active_operation": active_operation,
        "active_operation_message": get_file_operation_message(active_operation) if is_locked else "",
        "is_edit_locked": is_locked,
    }


def acquire_file_operation_lock(
    db: Session,
    file_record_id,
    *,
    operation: str,
    current_user: User | None,
) -> tuple[FileRecord, str]:
    file_record = (
        db.query(FileRecord)
        .filter(FileRecord.id == file_record_id)
        .with_for_update()
        .first()
    )
    if file_record is None:
        raise HTTPException(status_code=404, detail="文件不存在。")

    clear_stale_file_operation_lock(db, file_record)
    if file_record.active_operation:
        raise HTTPException(
            status_code=409,
            detail=get_file_operation_message(file_record.active_operation),
        )

    token = uuid4().hex
    file_record.active_operation = operation
    file_record.active_operation_token = token
    file_record.active_operation_updated_at = local_now()
    file_record.active_operation_user_id = current_user.id if current_user else None
    db.commit()
    db.refresh(file_record)
    return file_record, token


def ensure_file_record_write_allowed(
    db: Session,
    file_record: FileRecord,
    *,
    operation_token: str | None = None,
    touch_token: bool = True,
) -> None:
    if clear_stale_file_operation_lock(db, file_record):
        db.commit()
        db.refresh(file_record)

    if not file_record.active_operation:
        return

    if operation_token and operation_token == file_record.active_operation_token:
        if touch_token:
            file_record.active_operation_updated_at = local_now()
            db.flush()
        return

    raise HTTPException(
        status_code=409,
        detail=get_file_operation_message(file_record.active_operation),
    )


def heartbeat_file_operation_lock(
    db: Session,
    file_record: FileRecord,
    *,
    operation_token: str | None,
) -> None:
    ensure_file_record_write_allowed(
        db,
        file_record,
        operation_token=operation_token,
        touch_token=True,
    )
    db.commit()


def release_file_operation_lock(
    db: Session,
    file_record: FileRecord,
    *,
    operation_token: str | None,
) -> None:
    if clear_stale_file_operation_lock(db, file_record):
        db.commit()
        return

    if not file_record.active_operation:
        return

    if not operation_token or operation_token != file_record.active_operation_token:
        raise HTTPException(
            status_code=409,
            detail=get_file_operation_message(file_record.active_operation),
        )

    clear_file_operation_lock(file_record)
    db.commit()
