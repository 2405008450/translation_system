"""复杂 Excel 的整格对齐与定点回写，不插列、不重建工作簿。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
import posixpath
import re
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DRAW = "http://schemas.openxmlformats.org/drawingml/2006/main"
XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"s": MAIN, "a": DRAW, "x": XDR}
_NUMBER = re.compile(r"^[+-]?(?:\d[\d,]*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?%?$")
_TAG = re.compile(r"^(?:SD|RV)_[A-Z0-9_]+$")


@dataclass
class XlsxContent:
    items: list[dict[str, Any]]
    summary: dict[str, Any]

    def units(self):
        from .parser import _make_unit

        return [_make_unit(
            index, item["text"], para_index=index,
            block_type="table_cell" if item["kind"] == "cell" else "drawing_text",
            block_index=item["sheet_index"], row_index=item.get("row"),
            cell_index=item.get("col"),
        ) for index, item in enumerate(self.items)]


def _relationships(archive: ZipFile, part: str) -> dict[str, str]:
    folder, name = posixpath.split(part)
    relpart = posixpath.join(folder, "_rels", name + ".rels")
    if relpart not in archive.namelist():
        return {}
    return {
        rel.get("Id", ""): (
            rel.get("Target", "").lstrip("/") if rel.get("Target", "").startswith("/")
            else posixpath.normpath(posixpath.join(folder, rel.get("Target", "")))
        ) for rel in ET.fromstring(archive.read(relpart))
        if rel.get("TargetMode") != "External"
    }


def _text(element: ET.Element, prefix: str) -> str:
    return "".join(node.text or "" for node in element.findall(f".//{prefix}:t", NS))


def read_xlsx_content(raw_bytes: bytes) -> XlsxContent:
    items: list[dict[str, Any]] = []
    sheets: list[dict[str, Any]] = []
    counts: Counter = Counter()
    try:
        with ZipFile(BytesIO(raw_bytes)) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = _relationships(archive, "xl/workbook.xml")
            strings = (
                [_text(si, "s") for si in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
                if "xl/sharedStrings.xml" in archive.namelist() else []
            )
            for sheet_index, sheet in enumerate(workbook.findall("s:sheets/s:sheet", NS)):
                part = rels[sheet.get(f"{{{REL}}}id", "")]
                root = ET.fromstring(archive.read(part))
                name = sheet.get("name", "")
                merges = [node.get("ref", "") for node in root.findall("s:mergeCells/s:mergeCell", NS)]
                bounds = [(ref, range_boundaries(ref)) for ref in merges]
                info = {"name": name, "sheet_index": sheet_index, "merged_regions": len(merges),
                        "formulas": 0, "text_cells": 0, "drawing_texts": 0, "pictures": 0,
                        "hidden": sheet.get("state", "visible") != "visible"}
                for cell in root.findall("s:sheetData/s:row/s:c", NS):
                    if cell.find("s:f", NS) is not None:
                        info["formulas"] += 1
                        continue
                    typ = cell.get("t", "n")
                    value = cell.findtext("s:v", "", NS)
                    if typ == "s":
                        value = strings[int(value)] if value else ""
                    elif typ == "inlineStr":
                        value = _text(cell, "s")
                    else:
                        if value:
                            counts["protected_values"] += 1
                        continue
                    value = value.strip()
                    if not value:
                        continue
                    if _NUMBER.fullmatch(value) or _TAG.fullmatch(value):
                        counts["protected_values"] += 1
                        continue
                    ref = cell.get("r", "")
                    row, col = coordinate_to_tuple(ref)
                    merge = next((area for area, (c1, r1, c2, r2) in bounds
                                  if c1 <= col <= c2 and r1 <= row <= r2), "")
                    if merge and ref != merge.split(":")[0]:
                        counts["merged_nonanchors"] += 1
                        continue
                    items.append({"kind": "cell", "sheet_index": sheet_index, "sheet_name": name,
                                  "part_name": part, "cell_ref": ref, "row": row - 1, "col": col - 1,
                                  "merge_ref": merge, "text": value})
                    info["text_cells"] += 1

                sheet_rels = _relationships(archive, part)
                for drawing in root.findall("s:drawing", NS):
                    drawing_part = sheet_rels.get(drawing.get(f"{{{REL}}}id", ""))
                    if not drawing_part:
                        continue
                    drawing_root = ET.fromstring(archive.read(drawing_part))
                    info["pictures"] += len(drawing_root.findall(".//x:pic", NS))
                    paragraphs = {id(p): index for index, p in enumerate(drawing_root.findall(".//a:p", NS))}
                    for anchor in drawing_root:
                        origin = anchor.find("x:from", NS)
                        anchor_key = tuple(origin.findtext(f"x:{key}", "", NS) for key in ("row", "col", "rowOff", "colOff")) if origin is not None else ()
                        for shape in anchor.findall(".//x:sp", NS):
                            props = shape.find("x:nvSpPr/x:cNvPr", NS)
                            shape_id = props.get("id", "") if props is not None else ""
                            shape_paras = shape.findall(".//a:p", NS)
                            for local_index, paragraph in enumerate(shape_paras):
                                value = _text(paragraph, "a").strip()
                                if not value or _NUMBER.fullmatch(value) or _TAG.fullmatch(value):
                                    continue
                                items.append({"kind": "drawing_text", "sheet_index": sheet_index,
                                              "sheet_name": name, "part_name": drawing_part,
                                              "shape_id": shape_id, "shape_paragraph": local_index,
                                              "paragraph_index": paragraphs[id(paragraph)],
                                              "paragraph_count": len(shape_paras), "anchor": list(anchor_key),
                                              "text": value})
                                info["drawing_texts"] += 1
                sheets.append(info)
    except (BadZipFile, ET.ParseError, KeyError, ValueError, IndexError) as exc:
        raise ValueError(f"无法读取 Excel 文件：{exc}") from exc
    warnings = ["图片内部文字未自动校对，请人工检查。"] if any(s["pictures"] for s in sheets) else []
    if any(s["hidden"] for s in sheets):
        warnings.append("已包含隐藏工作表中的文字。")
    return XlsxContent(items, {
        "sheets": sheets, "sheet_count": len(sheets),
        "merged_regions": sum(s["merged_regions"] for s in sheets),
        "formula_cells": sum(s["formulas"] for s in sheets),
        "text_cells": sum(s["text_cells"] for s in sheets),
        "drawing_texts": sum(s["drawing_texts"] for s in sheets),
        "pictures": sum(s["pictures"] for s in sheets),
        "protected_values": counts["protected_values"], "warnings": warnings,
    })


def item_key(item: dict[str, Any]) -> tuple:
    if item["kind"] == "cell":
        return item["sheet_name"], "cell", item["cell_ref"]
    return item["sheet_name"], "drawing_text", item["shape_id"], item["shape_paragraph"]


def build_xlsx_pairs(src, tgt, config: dict[str, Any], locked_anchors=None):
    from .dp import AlignPair

    manifest = config["xlsx_manifest"]
    source_items, target_items = manifest["source"], manifest["target"]
    locked_anchors = locked_anchors or []
    locked_source = {index for source, _ in locked_anchors for index in source}
    locked_target = {index for _, target in locked_anchors for index in target}
    src = [unit for unit in src if unit.index not in locked_source]
    tgt = [unit for unit in tgt if unit.index not in locked_target]
    targets = {item_key(target_items[unit.index]): unit.index for unit in tgt}
    used: set[int] = set()
    pairs = []
    for unit in src:
        item = source_items[unit.index]
        other_index = targets.get(item_key(item))
        other = target_items[other_index] if other_index is not None else None
        reason = ""
        if other is not None:
            used.add(other_index)
            if item["kind"] == "cell" and item.get("merge_ref") != other.get("merge_ref"):
                reason = "合并范围不一致，请确认单元格对应关系。"
            elif item["kind"] == "drawing_text" and (
                not item.get("shape_id") or item.get("anchor") != other.get("anchor")
                or item.get("paragraph_count") != other.get("paragraph_count")
            ):
                reason = "图形位置或段落结构不一致，请人工确认。"
        features = {"xlsx_location": True, "source_location": location_label(item),
                    "target_location": location_label(other) if other else "",
                    "structure_warning": reason}
        pairs.append(AlignPair([unit.index], [other_index] if other is not None else [],
                               0.55 if reason else 0.98 if other is not None else 0.3,
                               method="xlsx_coordinate", features=features))
    for unit in tgt:
        if unit.index not in used:
            pairs.append(AlignPair([], [unit.index], 0.3, method="xlsx_coordinate",
                                   features={"xlsx_location": True, "source_location": "",
                                             "target_location": location_label(target_items[unit.index])}))
    if locked_anchors:
        pairs.extend(AlignPair(source, target, 1.0, method="manual", features={"locked_anchor": True})
                     for source, target in locked_anchors)
        pairs.sort(key=lambda pair: min(pair.src_indices) if pair.src_indices
                   else len(source_items) + min(pair.tgt_indices))
    return pairs


def location_label(item: dict[str, Any]) -> str:
    if item["kind"] == "cell":
        return f"{item['sheet_name']}!{item['cell_ref']}"
    return f"{item['sheet_name']}!图形{item['shape_id']}·段落{item['shape_paragraph'] + 1}"


def pair_locations(config: dict, indices: list[int], side: str) -> str:
    items = config.get("xlsx_manifest", {}).get(side, [])
    return "\n".join(location_label(items[index]) for index in indices if 0 <= index < len(items))


def _rewrite_text(elements: list[ET.Element], new_text: str) -> None:
    """保留富文本 run 的样式，通过字符边界投影分配修订后的文本。"""
    from .export import _project_boundaries
    from app.services.adapters.xlsx_exporter import _sanitize_xml_text

    old_text = "".join(node.text or "" for node in elements)
    boundaries = [0]
    for node in elements:
        boundaries.append(boundaries[-1] + len(node.text or ""))
    new_text = _sanitize_xml_text(new_text)
    projected = _project_boundaries(old_text, new_text, boundaries)
    # 字符边界投影在末尾插入时会停在旧文本尾部，显式包含新增前后缀。
    projected[0], projected[-1] = 0, len(new_text)
    for index, node in enumerate(elements):
        node.text = new_text[projected[index]:projected[index + 1]]
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    if "".join(node.text or "" for node in elements) != new_text:
        raise ValueError("富文本回写未完整保留修订文字。")


def patch_xlsx_text(raw_bytes: bytes, replacements: list[tuple[dict[str, Any], str]]) -> bytes:
    from app.services.adapters.xlsx_exporter import _register_namespaces, _serialize_xml

    if not replacements:
        return raw_bytes
    _register_namespaces()
    modified: dict[str, bytes] = {}
    with ZipFile(BytesIO(raw_bytes)) as archive:
        strings = list(ET.fromstring(archive.read("xl/sharedStrings.xml"))) if "xl/sharedStrings.xml" in archive.namelist() else []
        grouped: dict[str, list] = {}
        for item, text in replacements:
            grouped.setdefault(item["part_name"], []).append((item, text))
        for part, entries in grouped.items():
            original = archive.read(part)
            root = ET.fromstring(original)
            cells = {c.get("r"): c for c in root.findall("s:sheetData/s:row/s:c", NS)}
            paragraphs = root.findall(".//a:p", NS)
            for item, text in entries:
                if item["kind"] == "cell":
                    cell = cells.get(item["cell_ref"])
                    if cell is None or cell.find("s:f", NS) is not None or cell.get("t") not in {"s", "inlineStr"}:
                        raise ValueError(f"{location_label(item)} 不是可回写的文字单元格。")
                    if cell.get("t") == "s":
                        container = deepcopy(strings[int(cell.findtext("s:v", "", NS))])
                        container.tag = f"{{{MAIN}}}is"
                    else:
                        container = deepcopy(cell.find("s:is", NS))
                    elements = container.findall(".//s:t", NS)
                    if _text(container, "s").strip() != item["text"]:
                        raise ValueError(f"{location_label(item)} 与导入时的文字不一致，请重新导入。")
                    _rewrite_text(elements, text)
                    for child in list(cell):
                        if child.tag in {f"{{{MAIN}}}v", f"{{{MAIN}}}is"}:
                            cell.remove(child)
                    cell.set("t", "inlineStr")
                    cell.insert(0, container)
                else:
                    paragraph = paragraphs[item["paragraph_index"]]
                    if _text(paragraph, "a").strip() != item["text"]:
                        raise ValueError(f"{location_label(item)} 与导入时的文字不一致，请重新导入。")
                    _rewrite_text(paragraph.findall(".//a:t", NS), text)
            modified[part] = _serialize_xml(root, original)
        output = BytesIO()
        with ZipFile(output, "w") as target:
            for info in archive.infolist():
                target.writestr(info, modified.get(info.filename, archive.read(info.filename)))
        return output.getvalue()
