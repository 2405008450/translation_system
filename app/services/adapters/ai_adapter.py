"""Adobe Illustrator AI 适配器。

Linux 环境无法运行 Illustrator。本适配器仅处理保存时启用了
“Create PDF Compatible File”的 AI 文件，并按 PDF 文本行提取可翻译内容。
大型 AI 使用文件路径打开，避免把完整源文件复制到 Python 堆内存。
"""
from __future__ import annotations

import hashlib
import logging
import math
import tempfile
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, List

import fitz  # PyMuPDF

from app.config import get_settings
from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import BlockNode, DocumentAST, NodeType, ParseResult, Segment
from app.services.adapters.psd_ocr import PsdOcrError, recognize_image_text_lines


logger = logging.getLogger(__name__)


PDF_HEADER_SEARCH_LIMIT = 1024


@dataclass(frozen=True)
class AiTextLine:
    """AI 的 PDF 兼容层中一个可定位的文本行。"""

    text: str
    metadata: dict[str, Any]


def is_pdf_compatible_ai(raw_bytes: bytes) -> bool:
    """判断 AI 文件是否包含位于文件头区域的 PDF 签名。"""

    return b"%PDF-" in raw_bytes[:PDF_HEADER_SEARCH_LIMIT]


def is_pdf_compatible_ai_path(source_path: str | Path) -> bool:
    """只读取文件头判断磁盘上的 AI 是否包含 PDF 兼容层。"""

    with Path(source_path).open("rb") as stream:
        return is_pdf_compatible_ai(stream.read(PDF_HEADER_SEARCH_LIMIT))


def _raise_non_pdf_compatible() -> None:
    raise ParseError(
        filename="<unknown>.ai",
        reason=(
            "该 AI 文件未包含 PDF 兼容内容。请在 Adobe Illustrator 保存时启用 "
            "Create PDF Compatible File（创建 PDF 兼容文件）后重新上传。"
        ),
    )


def extract_ai_text_lines(
    raw_bytes: bytes,
    *,
    include_ocr: bool = True,
) -> tuple[list[AiTextLine], int]:
    """兼容小文件 bytes 调用；大型文件应使用 extract_ai_text_lines_from_path。"""

    if not is_pdf_compatible_ai(raw_bytes):
        _raise_non_pdf_compatible()
    try:
        document = fitz.open(stream=raw_bytes, filetype="pdf")
    except Exception as exc:
        raise ParseError(
            filename="<unknown>.ai",
            reason=f"AI 文件中的 PDF 兼容内容无法解析：{exc}",
        ) from exc
    try:
        return _extract_ai_text_lines_from_document(document, include_ocr=include_ocr)
    finally:
        document.close()


def extract_ai_text_lines_from_path(
    source_path: str | Path,
    *,
    include_ocr: bool = True,
) -> tuple[list[AiTextLine], int]:
    """从磁盘路径解析 PDF-compatible AI，不物化完整源文件 bytes。"""

    path = Path(source_path)
    if not is_pdf_compatible_ai_path(path):
        _raise_non_pdf_compatible()
    try:
        document = fitz.open(str(path))
    except Exception as exc:
        raise ParseError(
            filename=path.name or "<unknown>.ai",
            reason=f"AI 文件中的 PDF 兼容内容无法解析：{exc}",
        ) from exc
    try:
        return _extract_ai_text_lines_from_document(document, include_ocr=include_ocr)
    finally:
        document.close()


def _extract_ai_text_lines_from_document(
    document: fitz.Document,
    *,
    include_ocr: bool = True,
) -> tuple[list[AiTextLine], int]:
    text_lines: list[AiTextLine] = []
    settings = get_settings()
    page_count = len(document)
    xref_count = document.xref_length()
    if page_count > settings.ai_max_pages:
        raise ParseError(
            filename="<unknown>.ai",
            reason=f"AI 画板数量 {page_count} 超过安全上限 {settings.ai_max_pages}。",
        )
    if xref_count > settings.ai_max_xref_objects:
        raise ParseError(
            filename="<unknown>.ai",
            reason=f"AI PDF 对象数量 {xref_count} 超过安全上限 {settings.ai_max_xref_objects}。",
        )

    total_spans = 0
    total_characters = 0
    parse_started = time.perf_counter()
    for page_index, page in enumerate(document):
        page_started = time.perf_counter()
        try:
            page_payload = page.get_text(
                "dict",
                flags=fitz.TEXT_PRESERVE_WHITESPACE,
            )
        except Exception as exc:
            raise ParseError(
                filename="<unknown>.ai",
                reason=f"第 {page_index + 1} 个画板的文字提取失败：{exc}",
            ) from exc

        page_elapsed = time.perf_counter() - page_started
        if page_elapsed > settings.ai_max_page_parse_seconds:
            raise ParseError(
                filename="<unknown>.ai",
                reason=(
                    f"第 {page_index + 1} 个画板文字提取耗时 "
                    f"{page_elapsed:.2f} 秒，超过安全上限 "
                    f"{settings.ai_max_page_parse_seconds:.2f} 秒。"
                ),
            )
        _validate_total_parse_time(parse_started)

        page_text_start = len(text_lines)
        for block_index, block in enumerate(page_payload.get("blocks", [])):
            if block.get("type") != 0:
                continue
            for line_index, line in enumerate(block.get("lines", [])):
                spans = [span for span in line.get("spans", []) if span.get("text")]
                span_texts = [str(span.get("text") or "") for span in spans]
                total_spans += len(span_texts)
                total_characters += sum(len(value) for value in span_texts)
                if total_spans > settings.ai_max_text_spans:
                    raise ParseError(
                        filename="<unknown>.ai",
                        reason=f"AI 文本片段数量超过安全上限 {settings.ai_max_text_spans}。",
                    )
                if total_characters > settings.ai_max_text_characters:
                    raise ParseError(
                        filename="<unknown>.ai",
                        reason=f"AI 文本字符数量超过安全上限 {settings.ai_max_text_characters}。",
                    )
                _validate_total_parse_time(parse_started)
                text = "".join(span_texts).strip()
                if not text:
                    continue

                bbox = line.get("bbox") or _union_span_bboxes(spans)
                if not bbox or len(bbox) != 4:
                    continue

                first_span = spans[0] if spans else {}
                direction = line.get("dir") or (1.0, 0.0)
                metadata = {
                    "ai_text_id": _build_ai_text_id(page_index, bbox, text),
                    "page": page_index + 1,
                    "page_index": page_index,
                    "block_index": block_index,
                    "line_index": line_index,
                    "bbox": [float(value) for value in bbox],
                    "origin": [float(value) for value in first_span.get("origin", (bbox[0], bbox[3]))],
                    "font": str(first_span.get("font") or ""),
                    "font_size": float(first_span.get("size") or max(4.0, float(bbox[3]) - float(bbox[1]))),
                    "color": int(first_span.get("color") or 0),
                    "rotation_degrees": _direction_to_angle(direction),
                    "rotation": _direction_to_rotation(direction),
                    "page_width": float(page.rect.width),
                    "page_height": float(page.rect.height),
                    "source_format": ".ai",
                    "pdf_compatible": True,
                    "extraction_method": "native_text",
                }
                text_lines.append(AiTextLine(text=text, metadata=metadata))
                if len(text_lines) > settings.ai_max_text_lines:
                    raise ParseError(
                        filename="<unknown>.ai",
                        reason=f"AI 可编辑文本行数量超过安全上限 {settings.ai_max_text_lines}。",
                    )

        if include_ocr and settings.ai_ocr_enabled:
            ocr_lines = _extract_outlined_text_lines(
                page,
                page_index=page_index,
                native_lines=text_lines[page_text_start:],
            )
            text_lines.extend(ocr_lines)
            total_characters += sum(len(line.text) for line in ocr_lines)
            if len(text_lines) > settings.ai_max_text_lines:
                raise ParseError(
                    filename="<unknown>.ai",
                    reason=f"AI 可翻译文本行数量超过安全上限 {settings.ai_max_text_lines}。",
                )
            if total_characters > settings.ai_max_text_characters:
                raise ParseError(
                    filename="<unknown>.ai",
                    reason=f"AI 文本字符数量超过安全上限 {settings.ai_max_text_characters}。",
                )
            _validate_total_parse_time(parse_started)

    _validate_total_parse_time(parse_started)
    return text_lines, page_count


def _extract_outlined_text_lines(
    page: fitz.Page,
    *,
    page_index: int,
    native_lines: list[AiTextLine],
) -> list[AiTextLine]:
    """OCR 识别 Illustrator 转曲文字，只保留确实覆盖字形路径的新文本。"""
    settings = get_settings()
    glyph_drawings = _find_glyph_like_drawings(page)
    if len(glyph_drawings) < 2:
        return []

    requested_scale = max(float(settings.ai_ocr_scale), 1.0)
    page_pixels = max(float(page.rect.width) * float(page.rect.height), 1.0)
    max_pixels = max(int(settings.ai_ocr_max_pixels), 1_000_000)
    scale = min(requested_scale, math.sqrt(max_pixels / page_pixels))
    scale = max(scale, 1.0)

    try:
        with tempfile.TemporaryDirectory(prefix="translation-ai-ocr-") as temp_dir:
            image_path = Path(temp_dir) / f"page-{page_index + 1}.png"
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                colorspace=fitz.csRGB,
                alpha=False,
            )
            pixmap.save(str(image_path))
            ocr_payloads = recognize_image_text_lines(
                image_path,
                scale=scale,
                min_confidence=float(settings.ai_ocr_min_confidence),
            )
    except (PsdOcrError, OSError, RuntimeError, ValueError) as exc:
        # OCR 是转曲文字的补充路径；不可用时不能阻断原生可编辑文字的导入。
        logger.warning("AI outlined-text OCR skipped on page %d: %s", page_index + 1, exc)
        return []

    result: list[AiTextLine] = []
    for ocr_index, payload in enumerate(ocr_payloads):
        text = str(payload.get("text") or "").strip()
        raw_bbox = payload.get("bbox") or ()
        if not text or len(raw_bbox) != 4:
            continue
        bbox_rect = fitz.Rect(*(float(value) for value in raw_bbox)) & page.rect
        if bbox_rect.is_empty or bbox_rect.width <= 0 or bbox_rect.height <= 0:
            continue
        if not _overlaps_glyph_drawing(bbox_rect, glyph_drawings):
            continue
        bbox = [bbox_rect.x0, bbox_rect.y0, bbox_rect.x1, bbox_rect.y1]
        if any(_is_duplicate_ocr_line(text, bbox_rect, native) for native in native_lines):
            continue

        font_size = max(4.0, min(200.0, bbox_rect.height * 0.82))
        color = _drawing_color_at_bbox(bbox_rect, glyph_drawings)
        metadata = {
            "ai_text_id": _build_ai_text_id(page_index, bbox, text),
            "page": page_index + 1,
            "page_index": page_index,
            "block_index": -1,
            "line_index": ocr_index,
            "bbox": bbox,
            "origin": [bbox_rect.x0, bbox_rect.y1 - max(0.2, bbox_rect.height * 0.12)],
            "font": "",
            "font_size": font_size,
            "color": color,
            "rotation_degrees": 0.0,
            "rotation": 0,
            "page_width": float(page.rect.width),
            "page_height": float(page.rect.height),
            "source_format": ".ai",
            "pdf_compatible": True,
            "extraction_method": "ocr_outlined_text",
            "ocr_confidence": round(float(payload.get("confidence") or 0.0) * 100, 2),
            "ocr_engine": "paddleocr",
            "ocr_model": "PP-OCRv6",
            "ocr_scale": scale,
        }
        result.append(AiTextLine(text=text, metadata=metadata))

    if result:
        logger.info(
            "AI outlined-text OCR added %d lines on page %d",
            len(result),
            page_index + 1,
        )
    return result


def _find_glyph_like_drawings(page: fitz.Page) -> list[dict[str, Any]]:
    """筛选尺寸和结构接近转曲字形的填充路径，避免对纯文本画板启动 OCR。"""
    try:
        drawings = page.get_cdrawings() if hasattr(page, "get_cdrawings") else page.get_drawings()
    except Exception:
        return []

    maximum_size = max(36.0, min(float(page.rect.width), float(page.rect.height)) * 0.10)
    candidates: list[dict[str, Any]] = []
    for drawing in drawings:
        if drawing.get("type") not in {"f", "fs"} or drawing.get("fill") is None:
            continue
        raw_rect = drawing.get("rect")
        if raw_rect is None or len(drawing.get("items") or ()) < 4:
            continue
        rect = fitz.Rect(raw_rect)
        if (
            rect.is_empty
            or rect.width < 0.5
            or rect.height < 0.5
            or rect.width > maximum_size
            or rect.height > maximum_size
        ):
            continue
        candidates.append({**drawing, "rect": rect})
    return candidates


def _overlaps_glyph_drawing(
    text_rect: fitz.Rect,
    glyph_drawings: list[dict[str, Any]],
) -> bool:
    for drawing in glyph_drawings:
        glyph_rect = drawing["rect"]
        intersection = text_rect & glyph_rect
        if not intersection.is_empty and intersection.get_area() >= glyph_rect.get_area() * 0.2:
            return True
    return False


def _is_duplicate_ocr_line(text: str, rect: fitz.Rect, native_line: AiTextLine) -> bool:
    native_bbox = native_line.metadata.get("bbox") or ()
    if len(native_bbox) != 4:
        return False
    native_rect = fitz.Rect(*(float(value) for value in native_bbox))
    intersection = rect & native_rect
    minimum_area = min(rect.get_area(), native_rect.get_area())
    if intersection.is_empty or minimum_area <= 0 or intersection.get_area() / minimum_area < 0.30:
        return False

    left = _compact_comparison_text(text)
    right = _compact_comparison_text(native_line.text)
    if not left or not right:
        return False
    if left == right or (min(len(left), len(right)) >= 2 and (left in right or right in left)):
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.72


def _compact_comparison_text(text: str) -> str:
    return "".join(character.casefold() for character in str(text or "") if character.isalnum())


def _drawing_color_at_bbox(
    text_rect: fitz.Rect,
    glyph_drawings: list[dict[str, Any]],
) -> int:
    for drawing in glyph_drawings:
        if (text_rect & drawing["rect"]).is_empty:
            continue
        fill = drawing.get("fill") or ()
        if len(fill) >= 3:
            red, green, blue = (
                min(max(int(round(float(component) * 255)), 0), 255)
                for component in fill[:3]
            )
            return (red << 16) | (green << 8) | blue
    return 0


def _validate_total_parse_time(parse_started: float) -> None:
    settings = get_settings()
    elapsed = time.perf_counter() - parse_started
    if elapsed > settings.ai_max_total_parse_seconds:
        raise ParseError(
            filename="<unknown>.ai",
            reason=(
                f"AI 文字提取耗时 {elapsed:.2f} 秒，超过安全上限 "
                f"{settings.ai_max_total_parse_seconds:.2f} 秒。"
            ),
        )


def _build_ai_text_id(page_index: int, bbox: Any, text: str) -> str:
    coordinates = ",".join(f"{float(value):.3f}" for value in bbox)
    digest = hashlib.sha1(
        f"{page_index + 1}|{coordinates}|{' '.join(text.split())}".encode("utf-8")
    ).hexdigest()[:16]
    return f"ai-page-{page_index + 1}-{digest}"


def _union_span_bboxes(spans: list[dict[str, Any]]) -> list[float] | None:
    boxes = [span.get("bbox") for span in spans if span.get("bbox")]
    if not boxes:
        return None
    return [
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    ]


def _direction_to_angle(direction: Any) -> float:
    try:
        x, y = float(direction[0]), float(direction[1])
    except (IndexError, TypeError, ValueError):
        return 0.0
    return math.degrees(math.atan2(-y, x)) % 360


def _direction_to_rotation(direction: Any) -> int:
    angle = _direction_to_angle(direction)
    return min((0, 90, 180, 270), key=lambda value: abs(((angle - value + 180) % 360) - 180))


def _build_parse_result(text_lines: list[AiTextLine], page_count: int) -> ParseResult:
    if not text_lines:
        raise ParseError(
            filename="<unknown>.ai",
            reason=(
                "AI 文件中没有可编辑文字。文字可能已经转曲/创建轮廓，"
                "请保留可编辑文本并启用 PDF 兼容保存后重新上传。"
            ),
        )

    nodes: list[BlockNode] = []
    segments: list[Segment] = []
    for position, text_line in enumerate(text_lines):
        nodes.append(
            BlockNode(
                node_type=NodeType.PARAGRAPH,
                text_content=text_line.text,
                metadata=dict(text_line.metadata),
            )
        )
        segments.append(
            Segment(
                segment_id=f"seg-{position + 1:06d}",
                source_text=" ".join(text_line.text.split()),
                display_text=text_line.text,
                block_path=str(position),
                position=position,
                metadata=dict(text_line.metadata),
            )
        )

    ast = DocumentAST(
        nodes=nodes,
        source_format=".ai",
        metadata={"pdf_compatible": True, "page_count": page_count},
    )
    return ParseResult(
        ast=ast,
        segments=segments,
        metadata={
            "pdf_compatible": True,
            "page_count": page_count,
            "text_line_count": len(text_lines),
        },
    )


class AiAdapter(FormatAdapter):
    """解析 PDF-compatible Adobe Illustrator 文件。"""

    def supported_extensions(self) -> List[str]:
        return [".ai"]

    def get_max_file_size(self) -> int:
        return max(int(get_settings().ai_max_file_size_mb), 1) * 1024 * 1024

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".ai"),
                segments=[],
                metadata={"pdf_compatible": False, "page_count": 0},
            )
        text_lines, page_count = extract_ai_text_lines(raw_bytes)
        return _build_parse_result(text_lines, page_count)

    def parse_path(self, source_path: str | Path) -> ParseResult:
        path = Path(source_path)
        if path.stat().st_size <= 0:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".ai"),
                segments=[],
                metadata={"pdf_compatible": False, "page_count": 0},
            )
        text_lines, page_count = extract_ai_text_lines_from_path(path)
        return _build_parse_result(text_lines, page_count)
