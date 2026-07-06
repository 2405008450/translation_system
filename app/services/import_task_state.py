from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from app.services.cache import get_json as cache_get_json
from app.services.cache import set_json as cache_set_json


IMPORT_TASK_TTL_SECONDS = 24 * 60 * 60
ImportTaskStatus = Literal["queued", "running", "completed", "failed", "canceling", "canceled"]
IMPORT_TASK_ACTIVE_STATUSES = {"queued", "running", "canceling"}
IMPORT_TASK_TERMINAL_STATUSES = {"completed", "failed", "canceled"}


class ImportTaskCanceled(Exception):
    """导入任务已被用户取消。"""


def import_task_cache_key(task_id: str) -> str:
    return f"import-task:{task_id}"


def set_import_task_status(
    task_id: str,
    status: ImportTaskStatus,
    *,
    progress: int = 0,
    message: str = "",
    result: dict[str, Any] | None = None,
    error: str | None = None,
    cancel_requested: bool | None = None,
) -> dict[str, Any]:
    existing = get_import_task_status(task_id) or {}
    requested = (
        bool(cancel_requested)
        if cancel_requested is not None
        else bool(existing.get("cancel_requested")) or status in {"canceling", "canceled"}
    )
    payload: dict[str, Any] = {
        "task_id": task_id,
        "status": status,
        "progress": max(0, min(100, int(progress))),
        "message": message,
        "result": result,
        "error": error,
        "cancel_requested": requested,
        "updated_at": datetime.now().isoformat(),
    }
    cache_set_json(import_task_cache_key(task_id), payload, ttl_seconds=IMPORT_TASK_TTL_SECONDS)
    return payload


def get_import_task_status(task_id: str) -> dict[str, Any] | None:
    payload = cache_get_json(import_task_cache_key(task_id))
    return payload if isinstance(payload, dict) else None


def import_task_cancel_requested(task_id: str) -> bool:
    status = get_import_task_status(task_id)
    if not status:
        return False
    return bool(status.get("cancel_requested")) or status.get("status") in {"canceling", "canceled"}


def raise_if_import_task_canceled(task_id: str) -> None:
    if import_task_cancel_requested(task_id):
        raise ImportTaskCanceled("导入已取消。")


def request_import_task_cancel(task_id: str) -> dict[str, Any] | None:
    status = get_import_task_status(task_id)
    if not status:
        return None

    current_status = str(status.get("status") or "")
    if current_status in IMPORT_TASK_TERMINAL_STATUSES:
        return status

    if current_status == "queued":
        return set_import_task_status(
            task_id,
            "canceled",
            progress=100,
            message="导入已取消。",
            error=None,
            cancel_requested=True,
        )

    return set_import_task_status(
        task_id,
        "canceling",
        progress=int(status.get("progress") or 0),
        message="正在取消导入，请稍候。",
        error=None,
        cancel_requested=True,
    )
