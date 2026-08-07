from __future__ import annotations

from copy import copy
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy.orm import Session

from app.models import DocumentAlignmentPair, ProofreadingBatch, ProofreadingSegmentBaseline, Segment


def export_document_pair_xlsx(db: Session, batch: ProofreadingBatch) -> tuple[bytes, str]:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "双文档校对"
    sheet.append(["原文", "校对前译文", "校对后译文", "是否变更", "置信度", "对齐方法"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(fill_type="solid", fgColor="FFD9EAF7")
    rows = db.query(ProofreadingSegmentBaseline, Segment).join(
        Segment, Segment.id == ProofreadingSegmentBaseline.segment_id,
    ).filter(ProofreadingSegmentBaseline.batch_id == batch.id).order_by(ProofreadingSegmentBaseline.row_index).all()
    pairs = {pair.pair_order: pair for pair in db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).all()}
    for baseline, segment in rows:
        pair = pairs.get(baseline.row_index)
        changed = (segment.target_text or "") != (baseline.original_target_text or "")
        sheet.append([
            segment.source_text, baseline.original_target_text, segment.target_text or "",
            "是" if changed else "否", pair.confidence if pair else "", pair.method if pair else "",
        ])
        if changed:
            cell = sheet.cell(row=sheet.max_row, column=3)
            cell.font = Font(color="FF0563C1", bold=True)
            cell.fill = PatternFill(fill_type="solid", fgColor="FFDDEBFF")
    # 增译无法物化为 Segment，但导出必须明确保留。
    for pair in db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).order_by(DocumentAlignmentPair.pair_order).all():
        if not pair.source_text:
            sheet.append(["（增译）", pair.target_text, pair.target_text, "否", pair.confidence, pair.method])
    sheet.freeze_panes = "A2"
    for column, width in zip("ABCDEF", (45, 45, 45, 12, 12, 18)):
        sheet.column_dimensions[column].width = width
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue(), f"{Path(batch.filename).stem}_双文档校对版.xlsx"
