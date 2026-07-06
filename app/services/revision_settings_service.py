from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.auth import serialize_user
from app.models import RevisionDisplaySetting, User


DEFAULT_INSERT_COLOR = "#2563eb"
DEFAULT_DELETE_COLOR = "#dc2626"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")


def get_default_revision_display_settings(file_record_id: UUID | str) -> dict[str, Any]:
    return {
        "id": None,
        "file_record_id": str(file_record_id),
        "show_author_time": True,
        "show_others_revisions": True,
        "default_insert_color": DEFAULT_INSERT_COLOR,
        "default_delete_color": DEFAULT_DELETE_COLOR,
        "author_colors": {},
        "updated_by": None,
        "updated_at": None,
    }


def get_revision_display_settings(db: Session, file_record_id: UUID) -> dict[str, Any]:
    setting = (
        db.query(RevisionDisplaySetting)
        .options(joinedload(RevisionDisplaySetting.updated_by))
        .filter(RevisionDisplaySetting.file_record_id == file_record_id)
        .first()
    )
    if setting is None:
        return get_default_revision_display_settings(file_record_id)
    return serialize_revision_display_settings(setting)


def upsert_revision_display_settings(
    db: Session,
    *,
    file_record_id: UUID,
    payload: dict[str, Any],
    updated_by: User,
) -> dict[str, Any]:
    setting = (
        db.query(RevisionDisplaySetting)
        .filter(RevisionDisplaySetting.file_record_id == file_record_id)
        .first()
    )
    if setting is None:
        setting = RevisionDisplaySetting(file_record_id=file_record_id)
        db.add(setting)

    default_insert_color = _normalize_color(
        payload.get("default_insert_color"),
        DEFAULT_INSERT_COLOR,
    )
    default_delete_color = _normalize_color(
        payload.get("default_delete_color"),
        DEFAULT_DELETE_COLOR,
    )

    setting.show_author_time = _normalize_bool(payload.get("show_author_time"), True)
    setting.show_others_revisions = _normalize_bool(payload.get("show_others_revisions"), True)
    setting.default_insert_color = default_insert_color
    setting.default_delete_color = default_delete_color
    setting.author_colors = _normalize_author_colors(
        payload.get("author_colors"),
        default_insert_color=default_insert_color,
        default_delete_color=default_delete_color,
    )
    setting.updated_by_id = updated_by.id
    setting.updated_at = datetime.now()

    db.commit()
    return get_revision_display_settings(db, file_record_id)


def serialize_revision_display_settings(setting: RevisionDisplaySetting) -> dict[str, Any]:
    return {
        "id": str(setting.id),
        "file_record_id": str(setting.file_record_id),
        "show_author_time": bool(setting.show_author_time),
        "show_others_revisions": bool(setting.show_others_revisions),
        "default_insert_color": setting.default_insert_color or DEFAULT_INSERT_COLOR,
        "default_delete_color": setting.default_delete_color or DEFAULT_DELETE_COLOR,
        "author_colors": _normalize_author_colors(
            setting.author_colors,
            default_insert_color=setting.default_insert_color or DEFAULT_INSERT_COLOR,
            default_delete_color=setting.default_delete_color or DEFAULT_DELETE_COLOR,
        ),
        "updated_by": serialize_user(setting.updated_by) if setting.updated_by else None,
        "updated_at": setting.updated_at.isoformat() if setting.updated_at else None,
    }


def _normalize_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    return bool(value)


def _normalize_color(value: Any, fallback: str) -> str:
    color = str(value or "").strip()
    if HEX_COLOR_RE.match(color):
        return color.lower()
    return fallback


def _normalize_author_colors(
    value: Any,
    *,
    default_insert_color: str,
    default_delete_color: str,
) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, dict[str, str]] = {}
    for raw_user_id, raw_colors in value.items():
        user_id = str(raw_user_id or "").strip()
        if not user_id or not isinstance(raw_colors, dict):
            continue
        normalized[user_id] = {
            "insert": _normalize_color(raw_colors.get("insert"), default_insert_color),
            "delete": _normalize_color(raw_colors.get("delete"), default_delete_color),
        }
    return normalized
