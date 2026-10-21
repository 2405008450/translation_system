"""各类校对 Word 导出共用的修改建议筛选规则。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    ProofreadingBatch, ProofreadingSegmentBaseline, Segment,
    TranslationReviewReport, TranslationReviewReportItem,
)


def collect_proofreading_comments(
    db: Session, batch: ProofreadingBatch,
    items: list[tuple[Segment, ProofreadingSegmentBaseline | None]],
) -> dict[str, str]:
    if not items or (batch.batch_kind == "document_pair" and batch.workflow_stage != "proofreading"):
        return {}
    file_ids = {segment.file_record_id for segment, _ in items}
    reports = db.query(TranslationReviewReportItem).join(
        TranslationReviewReport, TranslationReviewReport.id == TranslationReviewReportItem.report_id,
    ).filter(
        TranslationReviewReport.proofreading_batch_id == batch.id,
        TranslationReviewReportItem.file_record_id.in_(file_ids),
        TranslationReviewReportItem.applied.is_(True),
        TranslationReviewReportItem.status == "applied",
        TranslationReviewReportItem.category_key != "generation_error",
    ).order_by(TranslationReviewReport.created_at.desc(), TranslationReviewReportItem.created_at.desc(),
               TranslationReviewReportItem.id.desc()).all()
    latest: dict = {}
    for item in reports:
        latest.setdefault(item.segment_id, item)
    comments: dict[str, str] = {}
    for segment, baseline in items:
        suggestion = latest.get(segment.id)
        after = segment.target_text or ""
        if baseline is None or (baseline.original_target_text or "") == after or suggestion is None:
            continue
        category = (suggestion.category_key or "").strip()
        if (suggestion.created_at >= baseline.created_at and suggestion.suggested_target_text == after
                and (suggestion.reason or "").strip()
                and category.lower() not in {"unchanged", "no_change", "无需修改"}):
            comments[str(segment.id)] = f"{category or '校对建议'}：{suggestion.reason.strip()}"
    return comments
