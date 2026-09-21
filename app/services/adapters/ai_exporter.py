"""将 PDF-compatible AI 的译文导出为 PDF 或 SVG。"""
from __future__ import annotations

import base64
import inspect
import json
import logging
import math
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from lxml import etree

from app.config import get_settings
from app.services.adapters.ai_adapter import (
    AiTextLine,
    extract_ai_text_lines,
    extract_ai_text_lines_from_path,
)
from app.services.adapters.exceptions import ExportError


logger = logging.getLogger(__name__)


def _snippet(text: str, limit: int = 80) -> str:
    """把长文本截断为单行片段，便于打印到日志。"""
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "…"


PDF_MEDIA_TYPE = "application/pdf"
SVG_MEDIA_TYPE = "image/svg+xml"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"

# PyMuPDF span flags 位掩码（见 TextPage 文档）。
_SPAN_FLAG_ITALIC = 2
_SPAN_FLAG_SERIF = 4
_SPAN_FLAG_BOLD = 16


@dataclass(frozen=True)
class _TranslationEntry:
    segment_id: str
    source_text: str
    target_text: str
    ai_text_id: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _TextReplacement:
    segment_id: str
    target_text: str
    text_line: AiTextLine


@dataclass(frozen=True)
class AiSvgExportReport:
    """一次 SVG 导出的降级情况，用于告知使用者拿到的是混合结果。"""

    total_pages: int
    rasterized_pages: tuple[int, ...] = ()

    @property
    def has_rasterized_pages(self) -> bool:
        return bool(self.rasterized_pages)

    def build_notice(self) -> str:
        """生成面向使用者的中文提示；无降级时返回空串。"""
        if not self.rasterized_pages:
            return ""
        preview_limit = 10
        preview = "、".join(str(index) for index in self.rasterized_pages[:preview_limit])
        location = (
            f"第 {preview} 等画板"
            if len(self.rasterized_pages) > preview_limit
            else f"第 {preview} 个画板"
        )
        return (
            f"其中 {len(self.rasterized_pages)} / {self.total_pages} 个画板的矢量数据过大，"
            f"图形已转为内嵌位图（{location}）；"
            "这些画板的文字仍然是可选中、可编辑的矢量文字，其余画板保持完整矢量。"
        )


class AiExporter:
    """在 AI 的 PDF 兼容层中回写译文，并转换为 Linux 友好的输出格式。"""

    def export_pdf(self, original_bytes: bytes, segments: list[Any]) -> bytes:
        text_lines, _ = extract_ai_text_lines(original_bytes, include_ocr=False)
        replacements = self._collect_replacements(text_lines, segments)

        try:
            document = fitz.open(stream=original_bytes, filetype="pdf")
        except Exception as exc:
            raise ExportError(format="PDF", reason=f"无法打开 AI 的 PDF 兼容内容：{exc}") from exc

        try:
            self._apply_replacements(document, replacements, subset_fonts=True)
            return document.tobytes(garbage=4, deflate=True, clean=True)
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError(format="PDF", reason=f"AI 译文写回失败：{exc}") from exc
        finally:
            document.close()

    def export_pdf_to_path(
        self,
        original_path: str | Path,
        output_path: str | Path,
        segments: list[Any],
    ) -> Path:
        """复制源 PDF 后增量写回译文，避免重写超大型 AI 的全部对象。"""

        source = Path(original_path)
        destination = Path(output_path)
        self._ensure_output_disk_space(destination, additional_bytes=source.stat().st_size)
        text_lines, _ = extract_ai_text_lines_from_path(source, include_ocr=False)
        replacements = self._collect_replacements(text_lines, segments)

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.part")
        temporary.unlink(missing_ok=True)
        try:
            with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)

            if replacements:
                try:
                    document = fitz.open(str(temporary))
                except Exception as exc:
                    raise ExportError(format="PDF", reason=f"无法打开 AI 的 PDF 兼容内容：{exc}") from exc
                try:
                    if not document.can_save_incrementally():
                        raise ExportError(
                            format="PDF",
                            reason="AI 的 PDF 兼容内容不支持安全增量保存，请重新另存为启用 PDF 兼容的 AI。",
                        )
                    # 大型 AI 不执行全文件字体子集化；该优化会扫描所有对象并可能耗时数十分钟。
                    self._apply_replacements(document, replacements, subset_fonts=False)
                    document.saveIncr()
                finally:
                    document.close()

            os.replace(temporary, destination)
            return destination
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError(format="PDF", reason=f"AI 译文写回失败：{exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def export_svg_to_path(
        self,
        original_path: str | Path,
        output_path: str | Path,
        segments: list[Any],
    ) -> tuple[Path, AiSvgExportReport]:
        """经磁盘临时 PDF 逐页写出 SVG，避免多画板结果在内存累计。

        返回输出路径与降级报告，便于上层把“部分画板已转位图”告知使用者。
        """

        destination = Path(output_path)
        self._ensure_output_disk_space(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        translated_pdf = destination.with_name(f".{destination.stem}.translated.pdf")
        try:
            self.export_pdf_to_path(original_path, translated_pdf, segments)
            try:
                document = fitz.open(str(translated_pdf))
            except Exception as exc:
                raise ExportError(format="SVG", reason=f"无法读取翻译后的 PDF：{exc}") from exc
            try:
                if len(document) <= 0:
                    raise ExportError(format="SVG", reason="AI 文件中没有可导出的画板。")
                report = self._serialize_svg_document_to_path(document, destination)
                return destination, report
            finally:
                document.close()
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError(format="SVG", reason=f"AI 转 SVG 失败：{exc}") from exc
        finally:
            translated_pdf.unlink(missing_ok=True)

    def export_svg(self, original_bytes: bytes, segments: list[Any]) -> bytes:
        pdf_bytes = self.export_pdf(original_bytes, segments)
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise ExportError(format="SVG", reason=f"无法读取翻译后的 PDF：{exc}") from exc

        try:
            if len(document) <= 0:
                raise ExportError(format="SVG", reason="AI 文件中没有可导出的画板。")
            return self._serialize_svg_document(document)
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError(format="SVG", reason=f"AI 转 SVG 失败：{exc}") from exc
        finally:
            document.close()

    def _collect_replacements(
        self,
        native_text_lines: list[AiTextLine],
        segments: list[Any],
    ) -> list[_TextReplacement]:
        entries_by_segment_id, entries_by_ai_text_id = self._build_translation_entries(segments)
        replacements: list[_TextReplacement] = []
        matched_ai_text_ids: set[str] = set()

        for index, text_line in enumerate(native_text_lines):
            segment_id = f"seg-{index + 1:06d}"
            ai_text_id = str(text_line.metadata.get("ai_text_id") or "")
            entry = entries_by_ai_text_id.get(ai_text_id) or entries_by_segment_id.get(segment_id)
            if entry is None or not entry.target_text.strip():
                continue
            if entry.source_text and self._normalize_source(entry.source_text) != self._normalize_source(text_line.text):
                raise ExportError(
                    format="PDF",
                    reason=(
                        f"句段 {entry.segment_id} 的源文字或版面定位已发生变化，"
                        "为避免把译文写入错误对象，本次导出已停止。请重新上传文件后再试。"
                    ),
                )
            replacements.append(
                _TextReplacement(
                    segment_id=entry.segment_id,
                    target_text=entry.target_text,
                    text_line=text_line,
                )
            )
            if ai_text_id:
                matched_ai_text_ids.add(ai_text_id)

        # OCR 转曲文字没有 PDF text operator，导出时直接使用导入阶段持久化的坐标，
        # 避免再次加载 OCR 模型，也避免 OCR 顺序波动导致译文写入错误位置。
        for ai_text_id, entry in entries_by_ai_text_id.items():
            extraction_method = str(entry.metadata.get("extraction_method") or "")
            if (
                ai_text_id in matched_ai_text_ids
                or extraction_method != "ocr_outlined_text"
                or not entry.target_text.strip()
            ):
                continue
            bbox = entry.metadata.get("bbox") or ()
            if len(bbox) != 4:
                raise ExportError(
                    format="PDF",
                    reason=f"OCR 句段 {entry.segment_id} 缺少有效的页面坐标。",
                )
            replacements.append(
                _TextReplacement(
                    segment_id=entry.segment_id,
                    target_text=entry.target_text,
                    text_line=AiTextLine(text=entry.source_text, metadata=dict(entry.metadata)),
                )
            )
        return replacements

    def _apply_replacements(
        self,
        document: fitz.Document,
        replacements: list[_TextReplacement],
        *,
        subset_fonts: bool = True,
    ) -> None:
        replacements_by_page: dict[int, list[_TextReplacement]] = {}
        for replacement in replacements:
            page_index = int(replacement.text_line.metadata.get("page_index", 0))
            if page_index < 0 or page_index >= len(document):
                raise ExportError(
                    format="PDF",
                    reason=f"句段 {replacement.segment_id} 的页面定位无效。",
                )
            replacements_by_page.setdefault(page_index, []).append(replacement)

        for page_index, page_replacements in replacements_by_page.items():
            page = document[page_index]
            native_replacements = [
                replacement
                for replacement in page_replacements
                if replacement.text_line.metadata.get("extraction_method") != "ocr_outlined_text"
            ]
            outlined_replacements = [
                replacement
                for replacement in page_replacements
                if replacement.text_line.metadata.get("extraction_method") == "ocr_outlined_text"
            ]

            for replacement in native_replacements:
                redact_rect = self._build_redaction_rect(page, replacement.text_line.metadata)
                page.add_redact_annot(redact_rect, fill=None, cross_out=False)
            if native_replacements:
                self._apply_text_only_redactions(page)

            for replacement in outlined_replacements:
                redact_rect = self._build_redaction_rect(
                    page,
                    replacement.text_line.metadata,
                    padding=0.5,
                )
                page.add_redact_annot(redact_rect, fill=None, cross_out=False)
            if outlined_replacements:
                self._apply_graphics_redactions(page)

            for replacement in page_replacements:
                self._insert_translation(page, replacement)

        if subset_fonts and hasattr(document, "subset_fonts"):
            try:
                document.subset_fonts(fallback=True)
            except Exception:
                pass

    def _ensure_output_disk_space(self, output_path: Path, *, additional_bytes: int = 0) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(output_path.parent).free
        reserve_bytes = max(int(get_settings().ai_min_free_disk_mb), 1) * 1024 * 1024
        required_bytes = max(int(additional_bytes), 0) + reserve_bytes
        if free_bytes < required_bytes:
            required_gib = round(required_bytes / (1024 ** 3), 2)
            raise ExportError(
                format=output_path.suffix.lstrip(".").upper() or "AI",
                reason=f"导出磁盘空间不足，至少需要 {required_gib} GiB 可用空间。",
            )

    def _build_translation_entries(
        self,
        segments: list[Any],
    ) -> tuple[dict[str, _TranslationEntry], dict[str, _TranslationEntry]]:
        by_segment_id: dict[str, _TranslationEntry] = {}
        by_ai_text_id: dict[str, _TranslationEntry] = {}
        duplicate_ai_text_ids: set[str] = set()
        for segment in segments:
            if isinstance(segment, dict):
                segment_id = segment.get("segment_id") or segment.get("sentence_id")
                source_text = segment.get("source_text")
                target_text = segment.get("target_text")
                raw_metadata = segment.get("segment_metadata") or segment.get("metadata")
            else:
                segment_id = getattr(segment, "segment_id", None) or getattr(segment, "sentence_id", None)
                source_text = getattr(segment, "source_text", None)
                target_text = getattr(segment, "target_text", None)
                raw_metadata = getattr(segment, "segment_metadata", None) or getattr(segment, "metadata", None)
            if not segment_id or not str(target_text or "").strip():
                continue

            metadata = self._parse_segment_metadata(raw_metadata)
            entry = _TranslationEntry(
                segment_id=str(segment_id),
                source_text=str(source_text or ""),
                target_text=str(target_text).strip(),
                ai_text_id=str(metadata.get("ai_text_id") or ""),
                metadata=metadata,
            )
            by_segment_id[entry.segment_id] = entry
            if entry.ai_text_id:
                existing = by_ai_text_id.get(entry.ai_text_id)
                if existing is not None and existing.segment_id != entry.segment_id:
                    same_source = self._normalize_source(existing.source_text) == self._normalize_source(entry.source_text)
                    same_target = self._normalize_source(existing.target_text) == self._normalize_source(entry.target_text)
                    if same_source and same_target:
                        # AI 源文件存在完全重复的文本对象（如阴影/复制图层），
                        # 数据库因此产生同定位、同原文、同译文的多条句段。
                        # 写回时目标位置只有一个，安全地保留首条并跳过后续。
                        duplicate_ai_text_ids.add(entry.ai_text_id)
                        continue
                    # 剩余情况：同一 AI 文本定位却有不同原文或不同译文，属于数据歧义，
                    # 记录冲突详情后拒绝导出，交由使用者在编辑器中合并处理。
                    conflict_detail = (
                        f"ai_text_id={entry.ai_text_id} "
                        f"existing_segment_id={existing.segment_id} "
                        f"existing_source={_snippet(existing.source_text)!r} "
                        f"existing_target={_snippet(existing.target_text)!r} "
                        f"duplicate_segment_id={entry.segment_id} "
                        f"duplicate_source={_snippet(entry.source_text)!r} "
                        f"duplicate_target={_snippet(entry.target_text)!r}"
                    )
                    logger.error("AI export duplicate ai_text_id detected: %s", conflict_detail)
                    raise ExportError(
                        format="PDF",
                        reason=(
                            f"AI 文本定位 {entry.ai_text_id} 对应了多个翻译句段"
                            f"（segment {existing.segment_id} 与 {entry.segment_id}）；"
                            f"原文A={_snippet(existing.source_text, 40)!r} 译文A={_snippet(existing.target_text, 40)!r}；"
                            f"原文B={_snippet(entry.source_text, 40)!r} 译文B={_snippet(entry.target_text, 40)!r}。"
                        ),
                    )
                by_ai_text_id[entry.ai_text_id] = entry
        if duplicate_ai_text_ids:
            logger.warning(
                "AI export merged %d duplicate ai_text_id groups (identical source & target); "
                "sample ids=%s",
                len(duplicate_ai_text_ids),
                sorted(duplicate_ai_text_ids)[:5],
            )
        return by_segment_id, by_ai_text_id

    def _parse_segment_metadata(self, raw_metadata: Any) -> dict[str, Any]:
        if isinstance(raw_metadata, dict):
            return raw_metadata
        if isinstance(raw_metadata, str) and raw_metadata.strip():
            try:
                parsed = json.loads(raw_metadata)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def _normalize_source(self, text: str) -> str:
        return " ".join(str(text or "").split())

    def _build_redaction_rect(
        self,
        page: fitz.Page,
        metadata: dict[str, Any],
        *,
        padding: float = 0.0,
    ) -> fitz.Rect:
        bbox = metadata.get("bbox") or ()
        if len(bbox) != 4:
            raise ExportError(format="PDF", reason="AI 文本缺少有效的页面坐标。")
        rect = fitz.Rect(*(float(value) for value in bbox))
        if padding > 0:
            rect = fitz.Rect(
                rect.x0 - padding,
                rect.y0 - padding,
                rect.x1 + padding,
                rect.y1 + padding,
            )
        rect = rect & page.rect
        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            raise ExportError(format="PDF", reason="AI 文本页面坐标位于画板范围之外。")
        return rect

    def _apply_text_only_redactions(self, page: fitz.Page) -> None:
        parameters = inspect.signature(page.apply_redactions).parameters
        kwargs: dict[str, int] = {"images": 0}
        if "graphics" in parameters:
            kwargs["graphics"] = 0
        if "text" in parameters:
            kwargs["text"] = 0
        page.apply_redactions(**kwargs)

    def _apply_graphics_redactions(self, page: fitz.Page) -> None:
        parameters = inspect.signature(page.apply_redactions).parameters
        if "graphics" not in parameters:
            raise ExportError(
                format="PDF",
                reason="当前 PyMuPDF 版本不支持擦除 AI 转曲文字，请升级到 1.24 或更高版本。",
            )
        kwargs: dict[str, int] = {"images": 0, "graphics": 2}
        if "text" in parameters:
            kwargs["text"] = 0
        page.apply_redactions(**kwargs)

    def _insert_translation(self, page: fitz.Page, replacement: _TextReplacement) -> None:
        metadata = replacement.text_line.metadata
        bbox = metadata.get("bbox") or ()
        rect = fitz.Rect(*(float(value) for value in bbox)) & page.rect
        original_font_size = max(4.0, min(200.0, float(metadata.get("font_size") or 10.0)))
        color = self._int_to_rgb(int(metadata.get("color") or 0))
        exact_rotation = float(metadata.get("rotation_degrees", metadata.get("rotation", 0)) or 0)
        rotation = int(metadata.get("rotation") or 0)
        rotation_error = abs(((exact_rotation - rotation + 180) % 360) - 180)
        # AI 里 x/y 方向向量经过 atan2 计算后常带浮点噪声或轻微设计漂移，
        # 允许 2° 以内的偏差吸附到最近的正交方向；超过则视为真正的任意角度并拒绝。
        _ROTATION_SNAP_TOLERANCE_DEG = 2.0
        if rotation not in {0, 90, 180, 270} or rotation_error > _ROTATION_SNAP_TOLERANCE_DEG:
            raise ExportError(
                format="PDF",
                reason=(
                    f"句段 {replacement.segment_id} 使用了 {exact_rotation:.1f}° 的任意角度文字，"
                    "当前 Linux 导出仅支持 0°、90°、180° 和 270°。"
                ),
            )

        requires_embedded_font = any(ord(character) > 255 for character in replacement.target_text)
        # 针对当前译文选择“覆盖字符最多”的字体，兼顾 CJK 与下标/数学符号等边缘字符。
        font_path = _pick_export_font_for_text(replacement.target_text) if requires_embedded_font else None
        if requires_embedded_font and font_path is None:
            raise ExportError(
                format="PDF",
                reason=(
                    "译文包含当前内置字体无法显示的字符，且未找到 Noto CJK 字体。"
                    "请在 Linux 安装 fonts-noto-cjk，或设置 AI_EXPORT_FONT_PATH。"
                ),
            )

        font_kwargs: dict[str, Any]
        if font_path:
            font_kwargs = {"fontname": "AITranslation", "fontfile": font_path}
        else:
            font_kwargs = {"fontname": "helv"}

        self._validate_font_glyphs(replacement.target_text, font_path)
        insert_rect = self._build_insert_rect(page, rect, original_font_size, rotation)
        minimum_size = max(3.0, min(6.0, original_font_size * 0.45))
        if "\n" not in replacement.target_text and "\r" not in replacement.target_text:
            font_size = self._fit_single_line_font_size(
                replacement.target_text,
                original_font_size,
                insert_rect,
                rotation,
                font_path,
            )
            if font_size >= minimum_size:
                origin = metadata.get("origin") or (rect.x0, rect.y1)
                page.insert_text(
                    fitz.Point(float(origin[0]), float(origin[1])),
                    replacement.target_text,
                    fontsize=font_size,
                    color=color,
                    rotate=rotation,
                    overlay=True,
                    **font_kwargs,
                )
                return
            # 单行放不下：落到下面的 insert_textbox 多行流程，让译文自动换行。
            logger.warning(
                "AI export single-line too long, wrapping to multi-line: segment_id=%s "
                "required_size=%.1fpt minimum_size=%.1fpt text=%s",
                replacement.segment_id,
                font_size,
                minimum_size,
                _snippet(replacement.target_text),
            )

        font_size = original_font_size
        while font_size >= minimum_size:
            remaining = page.insert_textbox(
                insert_rect,
                replacement.target_text,
                fontsize=font_size,
                lineheight=1.08,
                color=color,
                align=fitz.TEXT_ALIGN_LEFT,
                rotate=rotation,
                overlay=True,
                **font_kwargs,
            )
            if remaining >= 0:
                return
            font_size -= max(0.5, original_font_size * 0.06)

        fallback_rect = self._build_fallback_rect(page, insert_rect, minimum_size, rotation)
        remaining = page.insert_textbox(
            fallback_rect,
            replacement.target_text,
            fontsize=minimum_size,
            lineheight=1.05,
            color=color,
            align=fitz.TEXT_ALIGN_LEFT,
            rotate=rotation,
            overlay=True,
            **font_kwargs,
        )
        if remaining < 0:
            raise ExportError(
                format="PDF",
                reason=f"句段 {replacement.segment_id} 的译文无法放入原文本区域。",
            )

    def _fit_single_line_font_size(
        self,
        text: str,
        original_size: float,
        rect: fitz.Rect,
        rotation: int,
        font_path: str | None,
    ) -> float:
        try:
            font = _load_export_font(font_path) if font_path else fitz.Font("helv")
            if font is None:
                return original_size
            unit_length = float(font.text_length(text, fontsize=1))
        except Exception:
            return original_size
        if unit_length <= 0:
            return original_size

        available_length = rect.height if rotation in {90, 270} else rect.width
        fitted_size = available_length * 0.88 / unit_length
        return min(original_size, fitted_size)

    def _validate_font_glyphs(self, text: str, font_path: str | None) -> None:
        if font_path and _load_export_font(font_path) is None:
            raise ExportError(format="PDF", reason=f"译文字体加载失败：{font_path}")

        missing = _missing_glyphs_by_path(text, font_path, limit=8)
        if missing:
            # 缺字降级为警告：残缺字符会渲染为空框但不阻塞导出。
            # 用户可通过 AI_EXPORT_FONT_PATH 指定覆盖更全的字体。
            display = " ".join(repr(character) for character in missing)
            logger.warning(
                "AI export font missing glyphs: font=%s missing_chars=%s",
                font_path or "helv",
                display,
            )

    def _build_insert_rect(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        font_size: float,
        rotation: int,
    ) -> fitz.Rect:
        vertical_padding = max(1.5, font_size * 0.35)
        horizontal_padding = max(0.75, font_size * 0.08)
        expanded = fitz.Rect(
            rect.x0 - horizontal_padding,
            rect.y0 - vertical_padding,
            rect.x1 + horizontal_padding,
            rect.y1 + vertical_padding,
        )
        if rotation in {90, 270}:
            expanded.x1 = min(page.rect.x1, expanded.x1 + max(font_size, rect.height))
        else:
            expanded.y1 = min(page.rect.y1, expanded.y1 + max(font_size * 0.7, rect.height * 0.5))
        return expanded & page.rect

    def _build_fallback_rect(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        font_size: float,
        rotation: int,
    ) -> fitz.Rect:
        if rotation in {90, 270}:
            return fitz.Rect(
                max(page.rect.x0, rect.x0 - max(rect.width * 3, font_size * 6)),
                rect.y0,
                rect.x1,
                page.rect.y1 - 2,
            ) & page.rect
        return fitz.Rect(
            rect.x0,
            rect.y0,
            page.rect.x1 - 2,
            min(page.rect.y1 - 2, rect.y0 + max(rect.height * 4, font_size * 8)),
        ) & page.rect

    def _serialize_svg_document_to_path(
        self,
        document: fitz.Document,
        output_path: Path,
    ) -> AiSvgExportReport:
        settings = get_settings()
        max_output_bytes = max(int(settings.ai_max_svg_output_mb), 1) * 1024 * 1024
        xref_count = document.xref_length()
        if xref_count > settings.ai_max_svg_xref_objects:
            raise ExportError(
                format="SVG",
                reason=self._build_svg_xref_overflow_reason(xref_count, settings),
            )

        temporary = output_path.with_name(f".{output_path.name}.part")
        temporary.unlink(missing_ok=True)
        rasterized_pages: list[int] = []
        total_pages = len(document)
        try:
            with temporary.open("wb") as output:
                if total_pages == 1:
                    page_bytes = self._render_page_svg(
                        document[0],
                        page_index=1,
                        total_pages=1,
                        settings=settings,
                        placement=None,
                        rasterized_pages=rasterized_pages,
                    )
                    if len(page_bytes) > max_output_bytes:
                        raise ExportError(
                            format="SVG",
                            reason=self._build_svg_size_overflow_reason(
                                actual_bytes=len(page_bytes),
                                limit_mb=settings.ai_max_svg_output_mb,
                                page_index=1,
                                total_pages=1,
                            ),
                        )
                    output.write(page_bytes)
                else:
                    page_sizes = [
                        (float(page.rect.width), float(page.rect.height))
                        for page in document
                    ]
                    gap = 20.0
                    total_width = max(width for width, _ in page_sizes)
                    total_height = sum(height for _, height in page_sizes) + gap * (len(page_sizes) - 1)
                    opening = (
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        f'<svg xmlns="{SVG_NAMESPACE}" version="1.1" '
                        f'width="{self._format_number(total_width)}" '
                        f'height="{self._format_number(total_height)}" '
                        f'viewBox="0 0 {self._format_number(total_width)} '
                        f'{self._format_number(total_height)}">\n'
                    ).encode("utf-8")
                    output.write(opening)
                    offset_y = 0.0
                    for page_index, page in enumerate(document, start=1):
                        width, height = page_sizes[page_index - 1]
                        page_bytes = self._render_page_svg(
                            page,
                            page_index=page_index,
                            total_pages=total_pages,
                            settings=settings,
                            placement=(offset_y, width, height),
                            rasterized_pages=rasterized_pages,
                        )
                        if output.tell() + len(page_bytes) + len(b"\n</svg>") > max_output_bytes:
                            raise ExportError(
                                format="SVG",
                                reason=self._build_svg_size_overflow_reason(
                                    actual_bytes=output.tell() + len(page_bytes),
                                    limit_mb=settings.ai_max_svg_output_mb,
                                    page_index=page_index,
                                    total_pages=total_pages,
                                    aggregated=True,
                                ),
                            )
                        output.write(page_bytes)
                        output.write(b"\n")
                        offset_y += height + gap
                    output.write(b"</svg>")
            os.replace(temporary, output_path)
            self._log_rasterized_pages(rasterized_pages, total_pages)
            return AiSvgExportReport(
                total_pages=total_pages,
                rasterized_pages=tuple(rasterized_pages),
            )
        finally:
            temporary.unlink(missing_ok=True)

    def _serialize_svg_document(self, document: fitz.Document) -> bytes:
        settings = get_settings()
        max_output_bytes = max(int(settings.ai_max_svg_output_mb), 1) * 1024 * 1024
        xref_count = document.xref_length()
        if xref_count > settings.ai_max_svg_xref_objects:
            raise ExportError(
                format="SVG",
                reason=self._build_svg_xref_overflow_reason(xref_count, settings),
            )
        total_pages = len(document)
        rasterized_pages: list[int] = []
        if total_pages == 1:
            output = self._render_page_svg(
                document[0],
                page_index=1,
                total_pages=1,
                settings=settings,
                placement=None,
                rasterized_pages=rasterized_pages,
            )
            if len(output) > max_output_bytes:
                raise ExportError(
                    format="SVG",
                    reason=self._build_svg_size_overflow_reason(
                        actual_bytes=len(output),
                        limit_mb=settings.ai_max_svg_output_mb,
                        page_index=1,
                        total_pages=1,
                    ),
                )
            self._log_rasterized_pages(rasterized_pages, total_pages)
            return output

        page_sizes = [
            (float(page.rect.width), float(page.rect.height))
            for page in document
        ]
        gap = 20.0
        total_width = max(width for width, _ in page_sizes)
        total_height = sum(height for _, height in page_sizes) + gap * (len(page_sizes) - 1)
        opening = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="{SVG_NAMESPACE}" version="1.1" '
            f'width="{self._format_number(total_width)}" '
            f'height="{self._format_number(total_height)}" '
            f'viewBox="0 0 {self._format_number(total_width)} '
            f'{self._format_number(total_height)}">\n'
        ).encode("utf-8")
        output = BytesIO()
        output.write(opening)
        offset_y = 0.0
        for page_index, page in enumerate(document, start=1):
            width, height = page_sizes[page_index - 1]
            page_bytes = self._render_page_svg(
                page,
                page_index=page_index,
                total_pages=total_pages,
                settings=settings,
                placement=(offset_y, width, height),
                rasterized_pages=rasterized_pages,
            )
            if output.tell() + len(page_bytes) + len(b"\n</svg>") > max_output_bytes:
                raise ExportError(
                    format="SVG",
                    reason=self._build_svg_size_overflow_reason(
                        actual_bytes=output.tell() + len(page_bytes),
                        limit_mb=settings.ai_max_svg_output_mb,
                        page_index=page_index,
                        total_pages=total_pages,
                        aggregated=True,
                    ),
                )
            output.write(page_bytes)
            output.write(b"\n")
            offset_y += height + gap
        output.write(b"</svg>")
        self._log_rasterized_pages(rasterized_pages, total_pages)
        return output.getvalue()

    def _combine_svg_pages(self, pages: list[tuple[etree._Element, float, float]]) -> bytes:
        gap = 20.0
        total_width = max(width for _, width, _ in pages)
        total_height = sum(height for _, _, height in pages) + gap * (len(pages) - 1)
        outer = etree.Element(
            f"{{{SVG_NAMESPACE}}}svg",
            nsmap={None: SVG_NAMESPACE},
            version="1.1",
            width=self._format_number(total_width),
            height=self._format_number(total_height),
            viewBox=f"0 0 {self._format_number(total_width)} {self._format_number(total_height)}",
        )
        offset_y = 0.0
        for page_index, (root, width, height) in enumerate(pages, start=1):
            self._namespace_svg_ids(root, page_index)
            root.set("x", "0")
            root.set("y", self._format_number(offset_y))
            root.set("width", self._format_number(width))
            root.set("height", self._format_number(height))
            outer.append(root)
            offset_y += height + gap
        return etree.tostring(
            outer,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
        )

    def _namespace_svg_ids(self, root: etree._Element, page_index: int) -> None:
        id_map: dict[str, str] = {}
        for element in root.iter():
            resource_id = element.get("id")
            if resource_id:
                id_map[resource_id] = f"ai-page-{page_index}-{resource_id}"

        if not id_map:
            return
        ordered_ids = sorted(id_map, key=len, reverse=True)
        for element in root.iter():
            resource_id = element.get("id")
            if resource_id:
                element.set("id", id_map[resource_id])
            for attribute_name, attribute_value in list(element.attrib.items()):
                updated_value = attribute_value
                for old_id in ordered_ids:
                    updated_value = updated_value.replace(f"#{old_id}", f"#{id_map[old_id]}")
                if updated_value != attribute_value:
                    element.set(attribute_name, updated_value)
            if element.text and "#" in element.text:
                updated_text = element.text
                for old_id in ordered_ids:
                    updated_text = updated_text.replace(f"#{old_id}", f"#{id_map[old_id]}")
                element.text = updated_text

    def _int_to_rgb(self, color: int) -> tuple[float, float, float]:
        return (
            ((color >> 16) & 0xFF) / 255.0,
            ((color >> 8) & 0xFF) / 255.0,
            (color & 0xFF) / 255.0,
        )

    def _format_number(self, value: float) -> str:
        return f"{value:.3f}".rstrip("0").rstrip(".")

    def _render_page_svg(
        self,
        page: fitz.Page,
        *,
        page_index: int,
        total_pages: int,
        settings: Any,
        placement: tuple[float, float, float] | None,
        rasterized_pages: list[int],
    ) -> bytes:
        """渲染单个画板。

        优先输出纯矢量 SVG；当矢量结果超过单页阈值时，降级为
        “内嵌 PNG 底图 + 矢量文字层”，以保证译文仍然可选中、可编辑。
        placement 为 None 表示独立成文件（单画板），否则为 (offset_y, width, height)。
        """

        raster_threshold = max(int(settings.ai_svg_page_raster_threshold_mb), 1) * 1024 * 1024

        svg_bytes = page.get_svg_image(text_as_path=0).encode("utf-8")
        if len(svg_bytes) > raster_threshold:
            vector_bytes = len(svg_bytes)
            # 必须先释放巨大的矢量字符串，再去分配位图，避免两份峰值叠加。
            del svg_bytes
            rasterized_pages.append(page_index)
            logger.warning(
                "AI export rasterizing oversized SVG page: page_index=%d/%d "
                "vector_size=%.1fMB threshold=%dMB",
                page_index,
                total_pages,
                vector_bytes / (1024 * 1024),
                settings.ai_svg_page_raster_threshold_mb,
            )
            return self._build_hybrid_page_svg(
                page,
                settings=settings,
                placement=placement,
            )

        root = etree.fromstring(svg_bytes, parser=etree.XMLParser(huge_tree=True))
        del svg_bytes
        if placement is None:
            return etree.tostring(
                root,
                encoding="UTF-8",
                xml_declaration=True,
                pretty_print=True,
            )
        offset_y, width, height = placement
        self._namespace_svg_ids(root, page_index)
        root.set("x", "0")
        root.set("y", self._format_number(offset_y))
        root.set("width", self._format_number(width))
        root.set("height", self._format_number(height))
        return etree.tostring(root, encoding="UTF-8", pretty_print=False)

    def _build_hybrid_page_svg(
        self,
        page: fitz.Page,
        *,
        settings: Any,
        placement: tuple[float, float, float] | None,
    ) -> bytes:
        """把画板拆成“位图底图 + 矢量文字层”两层后重新组装成 SVG。

        文字先从页面上采集下来，再用 redaction 从 PDF 内容里抹掉，
        因此栅格化出来的底图只含图形，不会和上层矢量文字重影。
        """

        page_width = max(float(page.rect.width), 1.0)
        page_height = max(float(page.rect.height), 1.0)

        spans = self._collect_page_text_spans(page)
        self._remove_page_text(page, spans)
        raster_bytes, raster_media_type = self._render_page_raster(page, settings)

        root = etree.Element(
            f"{{{SVG_NAMESPACE}}}svg",
            nsmap={None: SVG_NAMESPACE, "xlink": XLINK_NAMESPACE},
            version="1.1",
        )
        root.set("viewBox", f"0 0 {self._format_number(page_width)} {self._format_number(page_height)}")
        if placement is None:
            root.set("width", self._format_number(page_width))
            root.set("height", self._format_number(page_height))
        else:
            offset_y, width, height = placement
            root.set("x", "0")
            root.set("y", self._format_number(offset_y))
            root.set("width", self._format_number(width))
            root.set("height", self._format_number(height))

        image = etree.SubElement(root, f"{{{SVG_NAMESPACE}}}image")
        image.set("x", "0")
        image.set("y", "0")
        image.set("width", self._format_number(page_width))
        image.set("height", self._format_number(page_height))
        image.set("preserveAspectRatio", "none")
        image.set(
            f"{{{XLINK_NAMESPACE}}}href",
            f"data:{raster_media_type};base64,"
            + base64.b64encode(raster_bytes).decode("ascii"),
        )
        del raster_bytes

        if spans:
            root.append(self._build_hybrid_text_layer(spans))

        return etree.tostring(
            root,
            encoding="UTF-8",
            xml_declaration=placement is None,
            pretty_print=False,
        )

    def _collect_page_text_spans(self, page: fitz.Page) -> list[dict[str, Any]]:
        """采集页面文字的逐字定位信息，用于重建矢量文字层。

        使用 rawdict 是因为它给出每个字符的 origin，能让 <tspan> 的 x 列表
        与 PyMuPDF 原生 SVG 输出逐字对齐，不依赖查看器的字距计算。
        """

        spans: list[dict[str, Any]] = []
        text_page = page.get_text("rawdict")
        for block in text_page.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                raw_direction = line.get("dir") or (1.0, 0.0)
                direction = (float(raw_direction[0]), float(raw_direction[1]))
                for span in line.get("spans", []):
                    chars = span.get("chars") or []
                    text = "".join(str(char.get("c") or "") for char in chars)
                    if not text.strip():
                        continue
                    origins: list[tuple[float, float]] = []
                    for char in chars:
                        char_origin = char.get("origin") or (0.0, 0.0)
                        origins.append((float(char_origin[0]), float(char_origin[1])))
                    bbox = span.get("bbox") or (0.0, 0.0, 0.0, 0.0)
                    span_origin = span.get("origin") or (origins[0] if origins else (0.0, 0.0))
                    spans.append(
                        {
                            "text": text,
                            "char_origins": origins,
                            "bbox": tuple(float(value) for value in bbox),
                            "origin": (float(span_origin[0]), float(span_origin[1])),
                            "size": float(span.get("size") or 0.0),
                            "color": int(span.get("color") or 0),
                            "alpha": int(span.get("alpha", 255)),
                            "font": str(span.get("font") or ""),
                            "flags": int(span.get("flags") or 0),
                            "dir": direction,
                        }
                    )
        return spans

    def _remove_page_text(self, page: fitz.Page, spans: list[dict[str, Any]]) -> None:
        """从页面内容里抹掉所有文字，只保留图形，供底图栅格化使用。"""

        if not spans:
            return
        applied = False
        for span in spans:
            rect = fitz.Rect(*span["bbox"]) & page.rect
            if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                continue
            page.add_redact_annot(rect, fill=None, cross_out=False)
            applied = True
        if not applied:
            return
        try:
            # images=0 / graphics=0 保证只擦文字，不动图形与图片。
            self._apply_text_only_redactions(page)
        except Exception:
            # 抹字失败时底图会包含原文字，与上层矢量文字重影，
            # 但仍然是可交付结果，因此只告警不中断导出。
            logger.warning("AI export failed to strip text before rasterizing", exc_info=True)

    def _render_page_raster(self, page: fitz.Page, settings: Any) -> tuple[bytes, str]:
        """把画板渲染成底图字节流，返回 (数据, media type)。

        dpi 会按像素总量上限自动回落，避免超大画板一次性分配数百 MB 位图。
        """

        dpi = max(int(settings.ai_svg_raster_dpi), 36)
        max_pixels = max(int(settings.ai_svg_raster_max_pixels), 1_000_000)
        width_inch = max(float(page.rect.width), 1.0) / 72.0
        height_inch = max(float(page.rect.height), 1.0) / 72.0
        estimated_pixels = width_inch * height_inch * dpi * dpi
        if estimated_pixels > max_pixels:
            dpi = max(int(dpi * math.sqrt(max_pixels / estimated_pixels)), 36)
            logger.warning(
                "AI export reduced raster dpi to %d to respect pixel budget %d",
                dpi,
                max_pixels,
            )

        use_jpeg = str(settings.ai_svg_raster_format or "jpeg").strip().lower() in {"jpeg", "jpg"}
        try:
            # alpha=False 会把画板合成到白底；JPEG 本身也不支持透明通道。
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            if use_jpeg:
                quality = min(max(int(settings.ai_svg_raster_jpeg_quality), 40), 100)
                return pixmap.tobytes("jpg", jpg_quality=quality), "image/jpeg"
            return pixmap.tobytes("png"), "image/png"
        except Exception as exc:
            raise ExportError(
                format="SVG",
                reason=f"AI 画板栅格化失败，无法生成 SVG 底图：{exc}",
            ) from exc

    def _build_hybrid_text_layer(self, spans: list[dict[str, Any]]) -> etree._Element:
        """按采集到的 span 重建可选中、可编辑的矢量文字层。"""

        group = etree.Element(f"{{{SVG_NAMESPACE}}}g")
        group.set("class", "ai-text-layer")
        for span in spans:
            element = etree.SubElement(group, f"{{{SVG_NAMESPACE}}}text")
            element.set(f"{{{XML_NAMESPACE}}}space", "preserve")
            element.set("font-size", self._format_number(span["size"]))
            element.set("font-family", self._build_svg_font_family(span["font"], span["flags"]))
            element.set("fill", self._int_to_hex_color(span["color"]))
            if span["flags"] & _SPAN_FLAG_BOLD:
                element.set("font-weight", "bold")
            if span["flags"] & _SPAN_FLAG_ITALIC:
                element.set("font-style", "italic")
            alpha = span["alpha"]
            if 0 <= alpha < 255:
                element.set("fill-opacity", self._format_number(alpha / 255.0))

            origin_x, origin_y = span["origin"]
            direction_x, direction_y = span["dir"]
            angle = math.degrees(math.atan2(direction_y, direction_x))
            if abs(angle) > 0.01:
                # 绕基线起点旋转，字符再沿书写方向线性排布，任意角度都精确。
                element.set(
                    "transform",
                    f"rotate({self._format_number(angle)} "
                    f"{self._format_number(origin_x)} {self._format_number(origin_y)})",
                )

            tspan = etree.SubElement(element, f"{{{SVG_NAMESPACE}}}tspan")
            tspan.set("y", self._format_number(origin_y))
            tspan.set(
                "x",
                " ".join(
                    self._format_number(
                        origin_x
                        + (char_x - origin_x) * direction_x
                        + (char_y - origin_y) * direction_y
                    )
                    for char_x, char_y in span["char_origins"]
                ),
            )
            tspan.text = span["text"]
        return group

    def _build_svg_font_family(self, font_name: str, flags: int) -> str:
        """把 PDF 字体名转成带兜底的 CSS font-family 列表。

        与 PyMuPDF 原生 SVG 一致，字体不内嵌，依赖查看器替换，
        因此额外补上去掉字重后缀的族名和通用族，提高替换命中率。
        """

        generic = "serif" if flags & _SPAN_FLAG_SERIF else "sans-serif"
        # rawdict 通常已去掉 "ABCDEF+" 子集前缀，这里再防御性处理一次。
        name = str(font_name or "").rsplit("+", 1)[-1].strip()
        if not name:
            return generic

        candidates = [name]
        base_name = name.rsplit("-", 1)[0].strip()
        if base_name and base_name != name:
            candidates.append(base_name)
        candidates.append(generic)

        rendered: list[str] = []
        for candidate in candidates:
            if candidate == generic:
                rendered.append(candidate)
            elif any(character in candidate for character in " ,'\""):
                rendered.append("'" + candidate.replace("'", "") + "'")
            else:
                rendered.append(candidate)
        return ", ".join(rendered)

    def _int_to_hex_color(self, color: int) -> str:
        return f"#{int(color) & 0xFFFFFF:06x}"

    def _log_rasterized_pages(self, rasterized_pages: list[int], total_pages: int) -> None:
        if not rasterized_pages:
            return
        logger.warning(
            "AI export produced hybrid SVG: %d/%d artboards had their graphics rasterized "
            "into an embedded image while keeping an editable vector text layer; page_indexes=%s",
            len(rasterized_pages),
            total_pages,
            rasterized_pages[:20],
        )

    def _build_svg_size_overflow_reason(
        self,
        *,
        actual_bytes: int,
        limit_mb: int,
        page_index: int,
        total_pages: int,
        aggregated: bool = False,
    ) -> str:
        """生成 SVG 体积超限的用户友好文案。

        aggregated=True 表示是累计到当前页时越界（说明前面几页也偏大），
        False 表示单页自身就已经超过阈值。
        """

        actual_mb = actual_bytes / (1024 * 1024)
        # 单页文档不额外强调页码，避免出现“第 1 / 1 页”这种冗余信息。
        if total_pages <= 1:
            location = "AI 画板"
        elif aggregated:
            location = f"前 {page_index} 个画板累计"
        else:
            location = f"第 {page_index} / {total_pages} 个画板"
        return (
            f"SVG 导出失败：{location}生成的 SVG 已达 {actual_mb:.1f} MB，"
            f"超过安全上限 {limit_mb} MB。"
            "该画板包含过多矢量路径 / 渐变 / 蒙版，浏览器和 Illustrator 也难以正常打开。"
            "建议改用 PDF 导出，或在 Illustrator 中将复杂效果光栅化后重新上传源文件。"
        )

    def _build_svg_xref_overflow_reason(self, xref_count: int, settings: Any) -> str:
        """生成 SVG 对象数超限的用户友好文案。"""

        return (
            f"SVG 导出失败：AI 内部对象数量 {xref_count} 超过安全上限 "
            f"{settings.ai_max_svg_xref_objects}。"
            "源文件多次修改 / 恢复后残留了大量隐藏对象，展开成 SVG 会导致服务不稳定。"
            "建议改用 PDF 导出，或在 Illustrator 中另存为一份干净的 AI 文件后重新上传。"
        )


@lru_cache(maxsize=16)
def _load_export_font(font_path: str) -> "fitz.Font | None":
    """加载并缓存字体对象；大 TTC 文件的 open 成本较高，必须整个进程仅一次。"""
    try:
        return fitz.Font(fontfile=font_path)
    except Exception:
        return None


@lru_cache(maxsize=100000)
def _font_has_char(font_path: str, character: str) -> bool:
    """按 (font, 单字符) 缓存 glyph 检查结果，避免逐段重复扫描相同字符。"""
    font = _load_export_font(font_path)
    if font is None:
        return True  # 加载失败按“有”对待，不误报缺字。
    try:
        return bool(font.has_glyph(ord(character)))
    except Exception:
        return True


def _missing_glyphs_by_path(text: str, font_path: str | None, *, limit: int) -> list[str]:
    """针对给定字体路径返回缺字列表，最多 ``limit`` 个；命中缓存后成本很低。"""
    if not font_path:
        # 内置 helv 字体只支持 Latin-1；非拉丁字符统一视为缺失。
        missing: list[str] = []
        for character in dict.fromkeys(text):
            if character.isspace():
                continue
            if ord(character) > 255:
                missing.append(character)
            if len(missing) >= limit:
                break
        return missing

    missing = []
    for character in dict.fromkeys(text):
        if character.isspace():
            continue
        if not _font_has_char(font_path, character):
            missing.append(character)
        if len(missing) >= limit:
            break
    return missing


def _missing_glyphs(font: "fitz.Font", text: str, *, limit: int) -> list[str]:
    """兼容旧签名：直接使用给定 font 对象扫描缺字。"""
    missing: list[str] = []
    for character in dict.fromkeys(text):
        if character.isspace():
            continue
        try:
            has_glyph = bool(font.has_glyph(ord(character)))
        except Exception:
            has_glyph = True
        if not has_glyph:
            missing.append(character)
        if len(missing) >= limit:
            break
    return missing


@lru_cache(maxsize=1)
def _list_export_font_candidates() -> tuple[str, ...]:
    """按优先级列出实际存在的字体文件路径；缓存一次文件系统检查。"""
    configured = (get_settings().ai_export_font_path or "").strip()
    raw_candidates = [
        configured,
        # Linux Noto CJK：Docker 生产环境的默认字体，覆盖 CJK 与常见符号。
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        # Windows 本地开发：SimSun 与雅黑既覆盖 CJK 又覆盖多数下标/数学符号。
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    resolved: list[str] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        if not candidate:
            continue
        if candidate in seen:
            continue
        if Path(candidate).is_file():
            resolved.append(candidate)
            seen.add(candidate)
    return tuple(resolved)


@lru_cache(maxsize=1)
def _resolve_export_font_path() -> str | None:
    """返回默认字体路径，用于不区分文字内容的场景。"""
    candidates = _list_export_font_candidates()
    return candidates[0] if candidates else None


@lru_cache(maxsize=4096)
def _pick_export_font_for_charset(charset_key: str) -> str | None:
    """按“对给定字符集覆盖最全”的顺序挑选字体；charset_key 是排序后的去空白唯一字符串。"""
    candidates = _list_export_font_candidates()
    if not candidates:
        return None

    best_path = candidates[0]
    best_missing_count: int | None = None
    for candidate in candidates:
        font = _load_export_font(candidate)
        if font is None:
            continue
        missing_count = 0
        for character in charset_key:
            if not _font_has_char(candidate, character):
                missing_count += 1
        if missing_count == 0:
            return candidate
        if best_missing_count is None or missing_count < best_missing_count:
            best_path = candidate
            best_missing_count = missing_count
    return best_path


def _pick_export_font_for_text(text: str) -> str | None:
    """挑选覆盖当前译文最完整的字体。以字符集为 key 缓存，命中率远高于按整段文本。"""
    unique_chars = sorted({ch for ch in text if not ch.isspace()})
    if not unique_chars:
        candidates = _list_export_font_candidates()
        return candidates[0] if candidates else None
    return _pick_export_font_for_charset("".join(unique_chars))
