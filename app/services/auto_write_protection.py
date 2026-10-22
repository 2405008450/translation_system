"""独立于工作流确认状态的自动写入保护。人工操作不受此限制。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def is_auto_write_protected(segment) -> bool:
    return getattr(segment, "auto_write_protection", None) == "protected"


def automatic_write_allowed(segment, *, entrypoint: str) -> bool:
    if not is_auto_write_protected(segment):
        return True
    logger.info("automatic write skipped entrypoint=%s segment=%s reason=%s", entrypoint,
                getattr(segment, "id", None), getattr(segment, "auto_write_protection_reason", None))
    return False


def set_auto_write_protection(segment, state: str, *, reason: str, actor_id=None) -> bool:
    if state not in {"none", "protected", "released"}:
        raise ValueError("无效的自动写入保护状态。")
    before = getattr(segment, "auto_write_protection", None) or "none"
    if before == state and getattr(segment, "auto_write_protection_reason", None) == reason:
        return False
    segment.auto_write_protection = state
    segment.auto_write_protection_reason = reason
    segment.auto_write_protection_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    segment.auto_write_protection_updated_by_id = actor_id
    segment.version = int(segment.version or 1) + 1
    logger.info("automatic write protection changed segment=%s before=%s after=%s reason=%s actor=%s",
                getattr(segment, "id", None), before, state, reason, actor_id)
    return True


def inherit_auto_write_protection(target, source) -> None:
    """拆分/合并保留人工解除状态；合并方自行优先选择受保护来源。"""
    for name in ("auto_write_protection", "auto_write_protection_reason",
                 "auto_write_protection_updated_at", "auto_write_protection_updated_by_id"):
        setattr(target, name, getattr(source, name, None))
