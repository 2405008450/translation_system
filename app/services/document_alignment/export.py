from __future__ import annotations

import csv
from copy import copy
from dataclasses import dataclass
from io import BytesIO, StringIO
import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy.orm import Session

from app.models import (
    DocumentAlignmentPair, FileRecord, ProofreadingBatch, ProofreadingColumnBinding,
    ProofreadingSegmentBaseline, Segment,
)
from app.services.normalizer import compact_match_core

from .segments import TRANSLATION_ONLY_SOURCE_LABEL, ensure_document_pair_segments_complete

MISSING_TRANSLATION_LABEL = "【译文缺失】"
TRANSLATION_ONLY_EXPORT_LABEL = "【增译】"


@dataclass(frozen=True)
class ProofreadingExportRow:
    order: int
    kind: str
    source_text: str
    original_target_text: str
    reviewed_target_text: str
    changed: bool
    confirmation_status: str
    llm_status: str
    confidence: float | None
    method: str
    pair_id: str = ""
    block_type: str = "paragraph"
    block_index: int = 0
    row_index: int | None = None
    cell_index: int | None = None


def _segment_metadata(segment: Segment) -> dict[str, Any]:
    try:
        value = json.loads(segment.segment_metadata or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_proofreading_export_rows(
    db: Session, batch: ProofreadingBatch,
) -> tuple[list[ProofreadingExportRow], FileRecord | None]:
    """建立导出的唯一顺序清单，不依赖 sentence_id 或数据库自然顺序。"""
    if batch.batch_kind == "document_pair":
        ensure_document_pair_segments_complete(db, batch)
        binding = db.query(ProofreadingColumnBinding).filter_by(batch_id=batch.id).first()
        file_record = db.get(FileRecord, binding.file_record_id) if binding else None
        baselines = db.query(ProofreadingSegmentBaseline).filter_by(batch_id=batch.id).all()
        baseline_by_segment = {item.segment_id: item for item in baselines}
        segment_by_pair: dict[str, Segment] = {}
        if file_record is not None:
            for segment in db.query(Segment).filter_by(file_record_id=file_record.id).all():
                pair_id = str(_segment_metadata(segment).get("alignment_pair_id") or "")
                if pair_id:
                    segment_by_pair[pair_id] = segment
        rows: list[ProofreadingExportRow] = []
        pairs = db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).order_by(
            DocumentAlignmentPair.pair_order,
        ).all()
        for pair in pairs:
            segment = segment_by_pair.get(str(pair.id))
            baseline = baseline_by_segment.get(segment.id) if segment is not None else None
            source_text = segment.source_text if segment is not None else pair.source_text
            reviewed_target = segment.target_text if segment is not None else pair.target_text
            original_target = baseline.original_target_text if baseline is not None else pair.target_text
            try:
                source_indices = json.loads(pair.src_indices or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                source_indices = []
            translation_only = not bool(source_indices if isinstance(source_indices, list) else [])
            kind = "增译" if translation_only else "缺译" if not reviewed_target else "对齐"
            llm_status = (
                "已校对" if segment is not None and bool(segment.llm_provider)
                else "人工确认" if segment is not None and segment.status == "confirmed"
                else "未校对"
            )
            rows.append(ProofreadingExportRow(
                order=pair.pair_order,
                kind=kind,
                source_text=TRANSLATION_ONLY_SOURCE_LABEL if translation_only else source_text,
                original_target_text=original_target or "",
                reviewed_target_text=reviewed_target or "",
                changed=(reviewed_target or "") != (original_target or ""),
                confirmation_status="已确认" if segment is not None and segment.status == "confirmed" else "未确认",
                llm_status=llm_status,
                confidence=pair.confidence,
                method=pair.method,
                pair_id=str(pair.id),
                block_type=pair.block_type,
                block_index=pair.block_index,
                row_index=pair.row_index,
                cell_index=pair.cell_index,
            ))
        return rows, file_record

    bindings = db.query(ProofreadingColumnBinding).filter_by(batch_id=batch.id).all()
    file_record = db.get(FileRecord, bindings[0].file_record_id) if bindings else None
    ordered_items: list[tuple[int, int, int, int, ProofreadingSegmentBaseline, Segment]] = []
    for binding in bindings:
        items = db.query(ProofreadingSegmentBaseline, Segment).join(
            Segment, Segment.id == ProofreadingSegmentBaseline.segment_id,
        ).filter(ProofreadingSegmentBaseline.binding_id == binding.id).all()
        for baseline, segment in items:
            ordered_items.append((
                baseline.sheet_index,
                baseline.row_index,
                binding.target_column,
                int(segment.sequence_index or 0),
                baseline,
                segment,
            ))
    rows = []
    for order, (_, _, _, _, baseline, segment) in enumerate(sorted(ordered_items, key=lambda item: item[:4])):
        rows.append(ProofreadingExportRow(
            order=order,
            kind="缺译" if not segment.target_text else "对齐",
            source_text=segment.source_text,
            original_target_text=baseline.original_target_text,
            reviewed_target_text=segment.target_text or "",
            changed=(segment.target_text or "") != (baseline.original_target_text or ""),
            confirmation_status="已确认" if segment.status == "confirmed" else "未确认",
            llm_status="已校对" if segment.llm_provider else "未校对",
            confidence=None,
            method="xlsx",
            block_type=segment.block_type,
            block_index=segment.block_index,
            row_index=baseline.row_index,
            cell_index=segment.cell_index,
        ))
    return rows, file_record


def build_export_readiness(db: Session, batch: ProofreadingBatch) -> dict[str, Any]:
    rows, file_record = build_proofreading_export_rows(db, batch)
    available_formats = (
        ["proofreading_docx_ordered", "proofreading_audit_xlsx"]
        if batch.batch_kind == "document_pair"
        else ["proofreading_xlsx_original"]
    )
    if batch.batch_kind == "document_pair" and file_record and Path(file_record.filename).suffix.lower() == ".docx":
        available_formats.insert(0, "proofreading_docx_layout")
    return {
        "batch_id": str(batch.id),
        "total": len(rows),
        "confirmed": sum(row.confirmation_status == "已确认" for row in rows),
        "unconfirmed": sum(row.confirmation_status != "已确认" for row in rows),
        "missing_translation": sum(row.kind == "缺译" for row in rows),
        "translation_only": sum(row.kind == "增译" for row in rows),
        "translation_only_unreviewed": sum(row.kind == "增译" and row.llm_status == "未校对" for row in rows),
        "llm_failed": int(batch.failed_segments or 0),
        "available_formats": available_formats,
        "has_warnings": any((
            any(row.confirmation_status != "已确认" for row in rows),
            any(row.kind == "缺译" for row in rows),
            any(row.kind == "增译" and row.llm_status == "未校对" for row in rows),
            bool(batch.failed_segments),
        )),
    }


def export_alignment_csv(pairs: list[DocumentAlignmentPair]) -> bytes:
    """导出对齐草稿；UTF-8 BOM 保证 Excel 直接打开中文不乱码。"""
    output = StringIO(newline="")
    fieldnames = [
        "pair_index", "source_indices", "target_indices", "source_text", "target_text",
        "confidence", "confidence_level", "method", "semantic_score", "status",
        "operation", "features_json",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for pair in pairs:
        features = json.loads(pair.features or "{}")
        if pair.source_text and not pair.target_text:
            status = "缺译"
        elif pair.target_text and not pair.source_text:
            status = "增译"
        elif pair.confidence_level == "low":
            status = "低置信"
        elif pair.confidence_level == "medium":
            status = "建议复核"
        else:
            status = "已对齐"
        writer.writerow({
            "pair_index": pair.pair_order,
            "source_indices": ",".join(map(str, json.loads(pair.src_indices or "[]"))),
            "target_indices": ",".join(map(str, json.loads(pair.tgt_indices or "[]"))),
            "source_text": pair.source_text,
            "target_text": pair.target_text,
            "confidence": pair.confidence,
            "confidence_level": pair.confidence_level,
            "method": pair.method,
            "semantic_score": features.get("semantic_similarity", features.get("absorbed_gap_similarity", "")),
            "status": status,
            "operation": features.get("op", ""),
            "features_json": json.dumps(features, ensure_ascii=False, separators=(",", ":")),
        })
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def export_document_pair_xlsx(db: Session, batch: ProofreadingBatch) -> tuple[bytes, str]:
    rows, _ = build_proofreading_export_rows(db, batch)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "双文档校对"
    sheet.append([
        "序号", "类型", "原文", "原译文", "校对后译文", "是否修改",
        "确认状态", "LLM 状态", "置信度", "对齐方法",
    ])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(fill_type="solid", fgColor="FFD9EAF7")
    for row in rows:
        sheet.append([
            row.order + 1, row.kind, row.source_text, row.original_target_text,
            row.reviewed_target_text or MISSING_TRANSLATION_LABEL,
            "是" if row.changed else "否", row.confirmation_status, row.llm_status,
            row.confidence if row.confidence is not None else "", row.method,
        ])
        if row.changed:
            cell = sheet.cell(row=sheet.max_row, column=5)
            cell.font = Font(color="FF0563C1", bold=True)
            cell.fill = PatternFill(fill_type="solid", fgColor="FFDDEBFF")
        if row.kind == "缺译":
            cell = sheet.cell(row=sheet.max_row, column=5)
            cell.font = Font(color="FF9C0006", bold=True)
            cell.fill = PatternFill(fill_type="solid", fgColor="FFFFC7CE")
        for cell in sheet[sheet.max_row]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:J{sheet.max_row}"
    sheet.sheet_view.showGridLines = False
    for column, width in zip("ABCDEFGHIJ", (9, 11, 45, 45, 45, 11, 12, 12, 12, 18)):
        sheet.column_dimensions[column].width = width
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue(), f"{Path(batch.filename).stem}_双文档校对版.xlsx"


def export_ordered_bilingual_docx(
    db: Session, batch: ProofreadingBatch,
) -> tuple[bytes, str]:
    """生成顺序优先的原文在前双语 Word，不依赖源文件版式映射。"""
    from docx import Document
    from docx.shared import Pt, RGBColor

    rows, _ = build_proofreading_export_rows(db, batch)
    document = Document()
    title = document.add_heading("双语校对文档", level=1)
    title.paragraph_format.keep_with_next = True
    for row in rows:
        source = document.add_paragraph()
        source.paragraph_format.keep_with_next = True
        source_run = source.add_run(row.source_text or TRANSLATION_ONLY_SOURCE_LABEL)
        source_run.bold = row.block_type == "heading"
        target = document.add_paragraph()
        target.paragraph_format.space_after = Pt(8)
        target_text = row.reviewed_target_text or MISSING_TRANSLATION_LABEL
        target_run = target.add_run(target_text)
        if row.kind == "增译":
            source_run.font.color.rgb = RGBColor(127, 96, 0)
        if row.kind == "缺译":
            target_run.bold = True
            target_run.font.color.rgb = RGBColor(156, 0, 6)
    output = BytesIO()
    document.save(output)
    return output.getvalue(), f"{Path(batch.filename).stem}_双语校对版_顺序优先.docx"


def _normalize_export_text(value: str) -> str:
    return "".join((value or "").split())


def _docx_package_is_well_formed(content: bytes) -> bool:
    """执行不依赖 Office 的基础 OOXML 包完整性检查。"""
    try:
        with ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            if archive.testzip() is not None:
                return False
            if not {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}.issubset(names):
                return False
            for name in names:
                if name.endswith((".xml", ".rels")):
                    ET.fromstring(archive.read(name))
    except (BadZipFile, KeyError, ET.ParseError, OSError):
        return False
    return True


def _layout_export_is_complete(
    content: bytes,
    rows: list[ProofreadingExportRow],
    *,
    source_content: bytes | None = None,
    document_parse_mode: str = "full",
    document_parse_options: dict[str, object] | str | None = None,
) -> bool:
    """用目标文本顺序门禁拦截内容缺失或错序的保留排版结果。"""
    if not _docx_package_is_well_formed(content):
        return False
    try:
        from app.services.document_workspace import parse_docx_workspace

        parsed = parse_docx_workspace(
            content,
            document_parse_mode=document_parse_mode,
            document_parse_options=document_parse_options,
        )
        output_text = _normalize_export_text("\n".join(
            str(item.get("display_text") or item.get("source_text") or "")
            for item in parsed.get("segments", [])
        ))
    except Exception:
        return False
    cursor = 0
    expected_counts: dict[str, int] = {}
    for row in rows:
        expected = _normalize_export_text(row.reviewed_target_text or MISSING_TRANSLATION_LABEL)
        if not expected:
            continue
        expected_counts[expected] = expected_counts.get(expected, 0) + 1
        position = output_text.find(expected, cursor)
        if position < 0:
            return False
        cursor = position + len(expected)
    if not all(output_text.count(text) >= count for text, count in expected_counts.items()):
        return False
    if source_content is not None:
        try:
            source_workspace = parse_docx_workspace(
                source_content,
                document_parse_mode=document_parse_mode,
                document_parse_options=document_parse_options,
            )
        except Exception:
            return False
        source_output_text = compact_match_core(output_text)
        source_cursor = 0
        for segment in source_workspace.get("segments", []):
            expected_source = compact_match_core(str(
                segment.get("display_text") or segment.get("source_text") or ""
            ))
            if not expected_source:
                continue
            position = source_output_text.find(expected_source, source_cursor)
            if position < 0:
                return False
            source_cursor = position + len(expected_source)
    return True


def _build_layout_export_segments(
    db: Session,
    file_record: FileRecord,
    rows: list[ProofreadingExportRow],
) -> list[dict[str, Any]]:
    """把增译临时锚定到前一有效源文块，避免改写持久化的对齐位置。"""
    segment_by_pair: dict[str, Segment] = {}
    for segment in db.query(Segment).filter_by(file_record_id=file_record.id).all():
        pair_id = str(_segment_metadata(segment).get("alignment_pair_id") or "")
        if pair_id:
            segment_by_pair[pair_id] = segment

    ordered_segments = [segment_by_pair.get(row.pair_id) for row in rows]
    export_segments: list[dict[str, Any]] = []
    for index, (row, segment) in enumerate(zip(rows, ordered_segments)):
        if segment is None:
            continue
        metadata = _segment_metadata(segment)
        anchor = segment
        if bool(metadata.get("translation_only")):
            anchor = next(
                (
                    candidate for candidate in reversed(ordered_segments[:index])
                    if candidate is not None and not bool(_segment_metadata(candidate).get("translation_only"))
                ),
                None,
            ) or next(
                (
                    candidate for candidate in ordered_segments[index + 1:]
                    if candidate is not None and not bool(_segment_metadata(candidate).get("translation_only"))
                ),
                segment,
            )
        target_text = segment.target_text or MISSING_TRANSLATION_LABEL
        if bool(metadata.get("translation_only")):
            target_text = f"{TRANSLATION_ONLY_EXPORT_LABEL}{target_text}"
        export_segments.append({
            "sentence_id": segment.sentence_id,
            "source_text": segment.source_text,
            "display_text": segment.display_text,
            "target_text": target_text,
            "target_html": None,
            "source_html": segment.source_html,
            "numbering_text": str(metadata.get("numbering_text") or ""),
            "matched_source_text": segment.matched_source_text,
            "sequence_index": row.order,
            "block_type": anchor.block_type,
            "block_index": anchor.block_index,
            "row_index": anchor.row_index,
            "cell_index": anchor.cell_index,
            "segment_metadata": metadata,
        })
    return export_segments


def export_layout_bilingual_docx(
    db: Session, batch: ProofreadingBatch,
) -> tuple[bytes, str, bool]:
    """优先保留源 DOCX 排版；完整性门禁失败时返回顺序优先版本。"""
    from app.services.file_record_service import load_file_record_source
    from app.services.task_file_service import export_bilingual_task_docx_with_layout

    rows, file_record = build_proofreading_export_rows(db, batch)
    if file_record is None or Path(file_record.filename).suffix.lower() != ".docx":
        content, filename = export_ordered_bilingual_docx(db, batch)
        return content, filename, True
    raw_bytes = load_file_record_source(file_record)
    if raw_bytes is None:
        content, filename = export_ordered_bilingual_docx(db, batch)
        return content, filename, True
    export_segments = _build_layout_export_segments(db, file_record, rows)
    try:
        exported = export_bilingual_task_docx_with_layout(
            raw_bytes=raw_bytes,
            filename=file_record.filename,
            segments=export_segments,
            order="source_first",
            document_parse_mode=file_record.document_parse_mode,
            document_parse_options=file_record.document_parse_options,
            target_language=file_record.target_language,
        )
        if _layout_export_is_complete(
            exported.content,
            rows,
            source_content=raw_bytes,
            document_parse_mode=file_record.document_parse_mode,
            document_parse_options=file_record.document_parse_options,
        ):
            return (
                exported.content,
                f"{Path(batch.filename).stem}_双语校对版_保留排版.docx",
                False,
            )
    except Exception:
        pass
    content, filename = export_ordered_bilingual_docx(db, batch)
    return content, filename, True
