from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re

from app.services.document_workspace import parse_docx_workspace
from app.services.libreoffice_service import convert_word_to_docx
from app.services.normalizer import normalize_text
from app.services.number_check.normalizer_total import extract_numbers
from app.services.sentence_splitter import SentenceSpan, split_sentence_spans


@dataclass(frozen=True)
class AlignUnit:
    index: int
    text: str
    norm_text: str
    para_index: int
    block_type: str
    block_index: int
    row_index: int | None
    cell_index: int | None
    numbering: str
    char_len: int
    numbers: tuple[str, ...]
    is_heading: bool
    parent_segment_id: str = ""
    source_start: int = 0
    source_end: int = 0


def _make_unit(index: int, text: str, *, para_index: int, block_type: str = "paragraph",
               block_index: int = 0, row_index: int | None = None,
               cell_index: int | None = None, numbering: str = "",
               is_heading: bool = False, parent_segment_id: str = "",
               source_start: int = 0, source_end: int | None = None) -> AlignUnit:
    clean = text.strip()
    return AlignUnit(
        index=index,
        text=clean,
        norm_text=normalize_text(clean),
        para_index=para_index,
        block_type=block_type,
        block_index=block_index,
        row_index=row_index,
        cell_index=cell_index,
        numbering=numbering.strip(),
        char_len=max(1, len(normalize_text(clean))),
        numbers=tuple(extract_numbers(clean)),
        is_heading=is_heading,
        parent_segment_id=parent_segment_id,
        source_start=source_start,
        source_end=len(text) if source_end is None else source_end,
    )


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_PARALLEL_CLAUSE_PREFIX_RE = re.compile(
    r"^\s*(?:一种|一是|二是|三是|四是|五是|其[一二三四五六]|[（(]?[一二三四五六七八九十\d]+[、.)）])"
)
def _split_alignment_atomic_spans(text: str, block_type: str) -> list[tuple[int, int]]:
    """在通用句界之内保守拆分中文并列分号句，并保留原始字符偏移。"""
    base_spans = split_sentence_spans(text)
    if not base_spans and text.strip():
        base_spans = [SentenceSpan(0, len(text))]
    result: list[tuple[int, int]] = []
    for base in base_spans:
        value = text[base.start:base.end]
        separators = [index for index, char in enumerate(value) if char == "；"]
        should_split = bool(_CJK_RE.search(value)) and (
            len(separators) >= 2
            or (
                block_type == "table_cell"
                and any(_PARALLEL_CLAUSE_PREFIX_RE.match(value[index + 1:]) for index in separators)
            )
        )
        if not should_split:
            result.append((base.start, base.end))
            continue
        local_start = 0
        pieces: list[tuple[int, int]] = []
        for separator in separators:
            end = separator + 1
            if value[local_start:end].strip():
                pieces.append((base.start + local_start, base.start + end))
            local_start = end
        if value[local_start:].strip():
            pieces.append((base.start + local_start, base.end))
        if len(pieces) >= 2 and all(text[start:end].strip() for start, end in pieces):
            result.extend(pieces)
        else:
            result.append((base.start, base.end))
    return result


def _parse_txt(raw_bytes: bytes, granularity: str) -> list[AlignUnit]:
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    paragraphs = [part.strip() for part in text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n") if part.strip()]
    units: list[AlignUnit] = []
    for para_index, paragraph in enumerate(paragraphs):
        if granularity == "paragraph":
            units.append(_make_unit(len(units), paragraph, para_index=para_index, block_index=para_index))
            continue
        spans = split_sentence_spans(paragraph)
        pieces = [paragraph[span.start:span.end].strip() for span in spans] or [paragraph]
        for piece in pieces:
            if piece:
                units.append(_make_unit(len(units), piece, para_index=para_index, block_index=para_index))
    return units


def _parse_docx(raw_bytes: bytes, granularity: str) -> list[AlignUnit]:
    segments = parse_docx_workspace(raw_bytes).get("segments", [])
    if granularity == "sentence":
        units = []
        for segment in segments:
            text = str(segment.get("display_text") or segment.get("source_text") or "").strip()
            if not text:
                continue
            block_index = int(segment.get("block_index") or 0)
            block_type = str(segment.get("block_type") or "paragraph")
            parent_segment_id = str(segment.get("sentence_id") or f"segment-{len(units)}")
            for start, end in _split_alignment_atomic_spans(text, block_type):
                piece = text[start:end].strip()
                if not piece:
                    continue
                units.append(_make_unit(
                    len(units), piece, para_index=block_index, block_type=block_type,
                    block_index=block_index, row_index=segment.get("row_index"),
                    cell_index=segment.get("cell_index"), numbering=str(segment.get("numbering_text") or ""),
                    is_heading=bool(segment.get("is_heading")) or block_type == "heading",
                    parent_segment_id=parent_segment_id, source_start=start, source_end=end,
                ))
        return units

    grouped: dict[tuple[int, str, int | None, int | None], list[dict]] = {}
    order: list[tuple[int, str, int | None, int | None]] = []
    for segment in segments:
        key = (
            int(segment.get("block_index") or 0), str(segment.get("block_type") or "paragraph"),
            segment.get("row_index"), segment.get("cell_index"),
        )
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(segment)
    units = []
    for para_index, key in enumerate(order):
        parts = grouped[key]
        text = "".join(str(item.get("display_text") or item.get("source_text") or "") for item in parts).strip()
        if not text:
            continue
        block_index, block_type, row_index, cell_index = key
        units.append(_make_unit(
            len(units), text, para_index=para_index, block_type=block_type,
            block_index=block_index, row_index=row_index, cell_index=cell_index,
            numbering=str(parts[0].get("numbering_text") or ""),
            is_heading=bool(parts[0].get("is_heading")) or block_type == "heading",
        ))
    return units


def parse_side(raw_bytes: bytes, filename: str, granularity: str = "sentence") -> list[AlignUnit]:
    """将 doc/docx/txt 解析为稳定、0-based 的对齐单元。"""
    if granularity not in {"sentence", "paragraph"}:
        raise ValueError("granularity 只能是 sentence 或 paragraph。")
    suffix = Path(filename).suffix.lower()
    if suffix == ".doc":
        raw_bytes = convert_word_to_docx(raw_bytes, filename)
        suffix = ".docx"
    if suffix == ".docx":
        result = _parse_docx(raw_bytes, granularity)
    elif suffix == ".txt":
        result = _parse_txt(raw_bytes, granularity)
    else:
        raise ValueError("仅支持 docx、doc 和 txt 文档。")
    return [replace(unit, index=index) for index, unit in enumerate(result)]
