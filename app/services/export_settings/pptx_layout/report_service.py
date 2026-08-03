"""
report_service.py —— 把一次 PPTX 版式优化的 AI 判断与实际调整结果落库。

设计：
  - 使用独立 SessionLocal（导出任务运行在线程池），与主导出事务解耦。
  - 任何写库失败都只记日志、不抛出，绝不影响导出成功。
  - 表结构由 schema_setup 在启动时统一建好；此处只做写入。
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def persist_pptx_layout_report(
    report_context: dict[str, Any] | None,
    *,
    mode: str,
    provider: str,
    model: str,
    filename: str,
    total_candidates: int,
    vlm_used: bool,
    status: str,
    items: list[dict[str, Any]],
) -> None:
    """写入一条 PPTX 版式优化报告及其明细。失败仅记日志。"""
    if not report_context:
        return

    try:
        from app.database import SessionLocal
        from app.models import PptxLayoutReport, PptxLayoutReportItem

        with SessionLocal() as db:
            report = PptxLayoutReport(
                file_record_id=_coerce_uuid(report_context.get("file_record_id")),
                export_task_id=_coerce_uuid(report_context.get("export_task_id")),
                created_by_id=_coerce_uuid(report_context.get("created_by_id")),
                export_type=str(report_context.get("export_type") or ""),
                filename=filename or "",
                mode=mode or "",
                provider=provider or "",
                model=model or "",
                total_candidates=int(total_candidates or 0),
                adjusted_count=sum(1 for item in items if item.get("applied")),
                vlm_used=bool(vlm_used),
                status=status or "completed",
            )
            db.add(report)
            db.flush()

            for item in items:
                orig = item.get("orig") or (None, None, None, None)
                new = item.get("new") or (None, None, None, None)
                db.add(
                    PptxLayoutReportItem(
                        report_id=report.id,
                        slide_index=int(item.get("slide_index") or 0),
                        tag=str(item.get("tag") or ""),
                        uid=str(item.get("uid") or ""),
                        kind=str(item.get("kind") or ""),
                        source_text=str(item.get("source_text") or "")[:5000],
                        orig_left=_as_float(orig[0]),
                        orig_top=_as_float(orig[1]),
                        orig_width=_as_float(orig[2]),
                        orig_height=_as_float(orig[3]),
                        new_left=_as_float(new[0]),
                        new_top=_as_float(new[1]),
                        new_width=_as_float(new[2]),
                        new_height=_as_float(new[3]),
                        overflow_ratio=_as_float(item.get("overflow_ratio")),
                        font_scale=_as_float(item.get("font_scale")),
                        applied=bool(item.get("applied")),
                        reason=str(item.get("reason") or "")[:2000],
                    )
                )
            db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("PPTX 版式优化报告落库失败（不影响导出）。", exc_info=True)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
