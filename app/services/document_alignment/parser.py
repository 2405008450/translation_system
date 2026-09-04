from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re

from app.services.adapters import ensure_default_adapters_registered
from app.services.adapters.models import DocumentAST, NodeType
from app.services.document_workspace import parse_docx_workspace
from app.config import get_settings
from app.services.libreoffice_service import convert_word_to_docx
from app.services.normalizer import normalize_text
from app.services.number_check.normalizer_total import extract_numbers
from app.services.sentence_splitter import (
    SentenceSpan,
    looks_like_numbered_heading,
    split_sentence_spans,
)


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
    cell_key: str = ""
    row_key: str = ""


def _make_unit(index: int, text: str, *, para_index: int, block_type: str = "paragraph",
               block_index: int = 0, row_index: int | None = None,
               cell_index: int | None = None, numbering: str = "",
               is_heading: bool = False, parent_segment_id: str = "",
               source_start: int = 0, source_end: int | None = None,
               cell_key: str = "", row_key: str = "") -> AlignUnit:
    clean = text.strip()
    inferred_heading = (
        block_type in {"paragraph", "heading"}
        and (is_heading or looks_like_numbered_heading(clean))
    )
    effective_block_type = "heading" if inferred_heading else block_type
    return AlignUnit(
        index=index,
        text=clean,
        norm_text=normalize_text(clean),
        para_index=para_index,
        block_type=effective_block_type,
        block_index=block_index,
        row_index=row_index,
        cell_index=cell_index,
        numbering=numbering.strip(),
        char_len=max(1, len(normalize_text(clean))),
        numbers=tuple(extract_numbers(clean)),
        is_heading=inferred_heading,
        parent_segment_id=parent_segment_id,
        source_start=source_start,
        source_end=len(text) if source_end is None else source_end,
        cell_key=cell_key,
        row_key=row_key,
    )


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_PARALLEL_CLAUSE_PREFIX_RE = re.compile(
    r"^\s*(?:一种|一是|二是|三是|四是|五是|其[一二三四五六]|[（(]?[一二三四五六七八九十\d]+[、.)）])"
)
_ATOMIC_TABLE_VALUE_RE = re.compile(
    r"^[\s+\-−–—()（）\[\]￥$€£¥₩₹\d,，.．:%％/／年月日]+$"
)
_TABLE_SENTENCE_END_RE = re.compile(r"[。！？!?；;]")


def _is_atomic_table_cell(text: str) -> bool:
    """短标签、金额和日期整格参与对齐，避免同一格再次被拆成多个候选。"""
    value = text.strip()
    if not value:
        return False
    compact = re.sub(r"\s+", "", value)
    if any(char.isdigit() for char in compact) and _ATOMIC_TABLE_VALUE_RE.fullmatch(compact):
        return True
    max_chars = max(1, int(get_settings().alignment_table_cell_atomic_max_chars))
    return len(normalize_text(value)) <= max_chars and not _TABLE_SENTENCE_END_RE.search(value)


def assign_table_boundary_keys(units: list[AlignUnit]) -> list[AlignUnit]:
    """按文档内表格首次出现顺序生成稳定键，不跨文档比较原始 block_index。"""
    table_ordinals: dict[int, int] = {}
    result: list[AlignUnit] = []
    for unit in units:
        if unit.block_type != "table_cell":
            result.append(replace(unit, cell_key="", row_key=""))
            continue
        if unit.block_index not in table_ordinals:
            table_ordinals[unit.block_index] = len(table_ordinals)
        ordinal = table_ordinals[unit.block_index]
        row = unit.row_index if unit.row_index is not None else -1
        cell = unit.cell_index if unit.cell_index is not None else -1
        result.append(replace(
            unit,
            cell_key=f"t{ordinal}:r{row}:c{cell}",
            row_key=f"t{ordinal}:r{row}",
        ))
    return result


def _split_alignment_atomic_spans(text: str, block_type: str) -> list[tuple[int, int]]:
    """在通用句界之内保守拆分中文并列分号句，并保留原始字符偏移。"""
    base_spans = split_sentence_spans(text, preserve_dotted_names=True)
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
        spans = split_sentence_spans(paragraph, preserve_dotted_names=True)
        pieces = [paragraph[span.start:span.end].strip() for span in spans] or [paragraph]
        for piece in pieces:
            if piece:
                units.append(_make_unit(len(units), piece, para_index=para_index, block_index=para_index))
    return units


def _parse_docx(raw_bytes: bytes, granularity: str) -> list[AlignUnit]:
    segments = parse_docx_workspace(raw_bytes).get("segments", [])
    if granularity == "sentence":
        grouped: list[list[dict]] = []
        for segment in segments:
            key = (
                int(segment.get("block_index") or 0),
                str(segment.get("block_type") or "paragraph"),
                segment.get("row_index"), segment.get("cell_index"),
            )
            previous_key = None
            if grouped:
                previous = grouped[-1][0]
                previous_key = (
                    int(previous.get("block_index") or 0),
                    str(previous.get("block_type") or "paragraph"),
                    previous.get("row_index"), previous.get("cell_index"),
                )
            # 工作区会先按通用规则生成句段。对齐需要按原结构块重组后使用更保守的
            # 外文断句规则，避免 C.V. PROPERTY、S.p.A. Holdings 一类专名被提前切断。
            if key == previous_key:
                grouped[-1].append(segment)
            else:
                grouped.append([segment])

        units = []
        for parts in grouped:
            segment = parts[0]
            block_type = str(segment.get("block_type") or "paragraph")
            joiner = "\n" if block_type == "table_cell" else " "
            text = joiner.join(
                str(item.get("display_text") or item.get("source_text") or "").strip()
                for item in parts
                if str(item.get("display_text") or item.get("source_text") or "").strip()
            ).strip()
            if not text:
                continue
            block_index = int(segment.get("block_index") or 0)
            parent_segment_id = str(segment.get("sentence_id") or f"segment-{len(units)}")
            spans = (
                [(0, len(text))]
                if block_type == "table_cell" and _is_atomic_table_cell(text)
                else _split_alignment_atomic_spans(text, block_type)
            )
            for start, end in spans:
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


def _resolve_adapter_node(ast: DocumentAST, block_path: str):
    parts = (block_path or "").split(".")
    try:
        node = ast.nodes[int(parts[0])]
    except (IndexError, TypeError, ValueError):
        return None
    cursor = 1
    while cursor < len(parts):
        if parts[cursor] != "children" or cursor + 1 >= len(parts):
            return None
        try:
            node = node.children[int(parts[cursor + 1])]
        except (IndexError, TypeError, ValueError):
            return None
        cursor += 2
    return node


def _adapter_root_index(block_path: str, fallback: int) -> int:
    try:
        return int((block_path or "").split(".", 1)[0])
    except (TypeError, ValueError):
        return fallback


def _parse_html(raw_bytes: bytes, filename: str, granularity: str) -> list[AlignUnit]:
    """复用翻译工作流的 HTML 适配器，将其 AST 句段映射为对齐单元。"""
    registry = ensure_default_adapters_registered()
    parse_result = registry.get_adapter(filename).parse_with_validation(raw_bytes, filename)
    grouped: dict[str, list] = {}
    order: list[str] = []
    for segment in parse_result.segments:
        key = str(segment.block_path or segment.position)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(segment)

    units: list[AlignUnit] = []
    for fallback_index, key in enumerate(order):
        parts = grouped[key]
        node = _resolve_adapter_node(parse_result.ast, key)
        metadata = dict((node.metadata if node else None) or parts[0].metadata or {})
        tag = str(metadata.get("tag") or "").lower()
        is_table_cell = bool(node and node.node_type == NodeType.TABLE_CELL) or tag in {"td", "th"}
        is_heading = bool(
            node and node.node_type in {NodeType.HEADING, NodeType.TITLE}
        ) or tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
        block_type = (
            "table_cell" if is_table_cell
            else "heading" if is_heading
            else tag if tag in {"header", "footer"}
            else "paragraph"
        )
        root_index = _adapter_root_index(key, fallback_index)
        block_index = (
            int(metadata.get("table_index", root_index))
            if is_table_cell else root_index
        )
        row_index = metadata.get("row_index") if is_table_cell else None
        cell_index = metadata.get("cell_index") if is_table_cell else None
        selected_parts = parts if granularity == "sentence" else [parts[0]]
        for part_index, segment in enumerate(selected_parts):
            text = (
                str(segment.display_text or segment.source_text or "").strip()
                if granularity == "sentence"
                else " ".join(
                    str(item.display_text or item.source_text or "").strip()
                    for item in parts
                    if str(item.display_text or item.source_text or "").strip()
                )
            )
            if not text:
                continue
            units.append(_make_unit(
                len(units), text,
                para_index=root_index,
                block_type=block_type,
                block_index=block_index,
                row_index=int(row_index) if row_index is not None else None,
                cell_index=int(cell_index) if cell_index is not None else None,
                is_heading=is_heading,
                parent_segment_id=str(segment.segment_id),
                source_start=part_index,
                source_end=part_index + 1,
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
    elif suffix in {".html", ".htm"}:
        result = _parse_html(raw_bytes, filename, granularity)
    else:
        raise ValueError("仅支持 docx、doc、txt、html 和 htm 文档。")
    indexed = [replace(unit, index=index) for index, unit in enumerate(result)]
    return assign_table_boundary_keys(indexed)
