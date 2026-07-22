"""
IDML 适配器模块 - 解析 Adobe InDesign IDML 文件

IDML 是一个 ZIP 压缩包，包含多个 XML 文件。
主要从 Stories 目录中的 Story_*.xml 文件提取文本。
"""
import zipfile
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, List

from lxml import etree

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments


# InDesign 命名空间
IDML_NS = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"
IDENTITY_TRANSFORM = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
VISUAL_ROW_TOLERANCE = 6.0


def _local_name(element) -> str:
    """返回 XML 元素的本地标签名，兼容带命名空间的 IDML。"""
    if not isinstance(getattr(element, "tag", None), str):
        return ""
    return etree.QName(element).localname


def _nearest_paragraph(element):
    """返回元素最近的 ParagraphStyleRange 祖先。"""
    return next(
        (
            ancestor
            for ancestor in element.iterancestors()
            if _local_name(ancestor) == "ParagraphStyleRange"
        ),
        None,
    )


def _paragraph_text_parts(paragraph) -> List[str]:
    """提取当前段落直接拥有的文本，避免递归吃进嵌套表格。

    IDML 会把项目符号、说明项等多个视觉段落放在同一个
    ``ParagraphStyleRange`` 中，并用 ``Br`` 标记边界。无论是否位于
    表格单元格，都必须按该边界拆分，否则整页内容会被拼成一个句段。
    """
    parts: List[str] = []
    current: List[str] = []

    def flush() -> None:
        text = "".join(current).strip()
        current.clear()
        if text:
            parts.append(text)

    for element in paragraph.iter():
        if element is paragraph or _nearest_paragraph(element) is not paragraph:
            continue
        element_name = _local_name(element)
        if element_name == "Content" and element.text:
            current.append(element.text)
        elif element_name == "Br":
            flush()

    flush()
    return parts


def _parse_number_list(value: str | None, expected: int) -> tuple[float, ...] | None:
    try:
        numbers = tuple(float(item) for item in (value or "").split())
    except (TypeError, ValueError):
        return None
    return numbers if len(numbers) == expected else None


def _compose_transforms(
    parent: tuple[float, ...],
    child: tuple[float, ...],
) -> tuple[float, ...]:
    """组合两个 IDML 仿射矩阵，结果等价于先应用 child、再应用 parent。"""
    pa, pb, pc, pd, ptx, pty = parent
    ca, cb, cc, cd, ctx, cty = child
    return (
        pa * ca + pc * cb,
        pb * ca + pd * cb,
        pa * cc + pc * cd,
        pb * cc + pd * cd,
        pa * ctx + pc * cty + ptx,
        pb * ctx + pd * cty + pty,
    )


def _element_transform(element: Any) -> tuple[float, ...]:
    transform = _parse_number_list(element.get("ItemTransform"), 6) or IDENTITY_TRANSFORM
    for ancestor in element.iterancestors():
        if _local_name(ancestor) in {"Spread", "MasterSpread"}:
            break
        ancestor_transform = _parse_number_list(ancestor.get("ItemTransform"), 6)
        if ancestor_transform:
            transform = _compose_transforms(ancestor_transform, transform)
    return transform


def _transform_point(
    point: tuple[float, float],
    transform: tuple[float, ...],
) -> tuple[float, float]:
    x, y = point
    a, b, c, d, tx, ty = transform
    return a * x + c * y + tx, b * x + d * y + ty


def _bounds_from_points(
    points: list[tuple[float, float]],
    transform: tuple[float, ...],
) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    transformed = [_transform_point(point, transform) for point in points]
    return (
        min(point[0] for point in transformed),
        min(point[1] for point in transformed),
        max(point[0] for point in transformed),
        max(point[1] for point in transformed),
    )


def _element_bounds(
    element: Any,
    *,
    is_page: bool = False,
) -> tuple[float, float, float, float] | None:
    transform = _element_transform(element)
    if is_page:
        geometric_bounds = _parse_number_list(element.get("GeometricBounds"), 4)
        if geometric_bounds:
            top, left, bottom, right = geometric_bounds
            return _bounds_from_points(
                [(left, top), (right, top), (right, bottom), (left, bottom)],
                transform,
            )

    points: list[tuple[float, float]] = []
    for child in element.iter():
        if _local_name(child) != "PathPointType":
            continue
        anchor = _parse_number_list(child.get("Anchor"), 2)
        if anchor:
            points.append((anchor[0], anchor[1]))
    bounds = _bounds_from_points(points, transform)
    if bounds:
        return bounds

    # 极少数 TextFrame 没有 PathGeometry，至少用变换原点确定所属页面。
    origin = _transform_point((0.0, 0.0), transform)
    return origin[0], origin[1], origin[0], origin[1]


def _transform_bounds(
    bounds: tuple[float, float, float, float],
    transform: tuple[float, ...],
) -> tuple[float, float, float, float]:
    left, top, right, bottom = bounds
    transformed = _bounds_from_points(
        [(left, top), (right, top), (right, bottom), (left, bottom)],
        transform,
    )
    return transformed or bounds


def _intersection_area(
    first: tuple[float, float, float, float] | None,
    second: tuple[float, float, float, float] | None,
) -> float:
    if not first or not second:
        return 0.0
    return max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0,
        min(first[3], second[3]) - max(first[1], second[1]),
    )


def _matching_page_index(
    bounds: tuple[float, float, float, float],
    pages: list[dict[str, Any]],
) -> int | None:
    if not pages:
        return None
    areas = [_intersection_area(bounds, page.get("bounds")) for page in pages]
    best_index = max(range(len(areas)), key=areas.__getitem__)
    if areas[best_index] > 0:
        return best_index

    center_x = (bounds[0] + bounds[2]) / 2
    center_y = (bounds[1] + bounds[3]) / 2
    for index, page in enumerate(pages):
        page_bounds = page.get("bounds")
        if (
            page_bounds
            and page_bounds[0] <= center_x <= page_bounds[2]
            and page_bounds[1] <= center_y <= page_bounds[3]
        ):
            return index
    return None


def _visual_item_order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按页面视觉行从上到下排序，同一行再从左到右。"""
    positioned = sorted(
        items,
        key=lambda item: (
            item["bounds"][1],
            item["bounds"][0],
            item.get("xml_order", 0),
        ),
    )
    rows: list[list[dict[str, Any]]] = []
    row_tops: list[float] = []
    for item in positioned:
        item_top = item["bounds"][1]
        if rows and item_top - row_tops[-1] <= VISUAL_ROW_TOLERANCE:
            rows[-1].append(item)
        else:
            rows.append([item])
            row_tops.append(item_top)

    result: list[dict[str, Any]] = []
    for row in rows:
        result.extend(
            sorted(
                row,
                key=lambda item: (
                    item["bounds"][0],
                    item["bounds"][1],
                    item.get("xml_order", 0),
                ),
            )
        )
    return result


def _story_id_from_name(story_name: str) -> str:
    stem = PurePosixPath(story_name).stem
    return stem.removeprefix("Story_")


def _idml_layout_story_order(
    archive: zipfile.ZipFile,
    story_names: list[str],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """根据页面、TextFrame 坐标和母版映射生成 Story 的视觉阅读顺序。"""
    archive_names = set(archive.namelist())
    if "designmap.xml" not in archive_names:
        return sorted(story_names), {}

    parser = etree.XMLParser(remove_blank_text=True, recover=True)
    designmap = etree.fromstring(archive.read("designmap.xml"), parser=parser)
    spread_names = [
        str(element.get("src")).lstrip("/")
        for element in designmap.iter()
        if _local_name(element) == "Spread" and element.get("src")
    ]
    master_names = [
        str(element.get("src")).lstrip("/")
        for element in designmap.iter()
        if _local_name(element) == "MasterSpread" and element.get("src")
    ]
    designmap_story_names = [
        str(element.get("src")).lstrip("/")
        for element in designmap.iter()
        if _local_name(element) == "Story" and element.get("src")
    ]
    story_id_to_name = {_story_id_from_name(name): name for name in story_names}

    page_records: list[dict[str, Any]] = []
    page_items: dict[int, list[dict[str, Any]]] = {}
    unplaced_items: list[tuple[int, dict[str, Any]]] = []
    for spread_index, spread_name in enumerate(spread_names):
        if spread_name not in archive_names:
            continue
        spread_root = etree.fromstring(archive.read(spread_name), parser=parser)
        spread_pages: list[dict[str, Any]] = []
        for element in spread_root.iter():
            if _local_name(element) != "Page":
                continue
            page_record = {
                "document_page_index": len(page_records),
                "page_name": element.get("Name") or "",
                "spread": spread_name,
                "bounds": _element_bounds(element, is_page=True),
                "applied_master": element.get("AppliedMaster") or "n",
                "master_transform": (
                    _parse_number_list(element.get("MasterPageTransform"), 6)
                    or IDENTITY_TRANSFORM
                ),
            }
            page_records.append(page_record)
            spread_pages.append(page_record)
            page_items[page_record["document_page_index"]] = []

        for xml_order, element in enumerate(spread_root.iter()):
            if _local_name(element) != "TextFrame":
                continue
            story_id = str(element.get("ParentStory") or "").strip()
            if not story_id or story_id == "n":
                continue
            item = {
                "story_id": story_id,
                "bounds": _element_bounds(element),
                "xml_order": xml_order,
                "spread": spread_name,
            }
            page_index = _matching_page_index(item["bounds"], spread_pages)
            if page_index is None:
                unplaced_items.append((spread_index, item))
                continue
            document_page_index = spread_pages[page_index]["document_page_index"]
            page_items[document_page_index].append(item)

    master_spreads: dict[str, dict[str, Any]] = {}
    for master_order, master_name in enumerate(master_names):
        if master_name not in archive_names:
            continue
        master_root = etree.fromstring(archive.read(master_name), parser=parser)
        master_element = next(
            (
                element
                for element in master_root.iter()
                if _local_name(element) == "MasterSpread" and element.get("Self")
            ),
            None,
        )
        if master_element is None:
            continue
        master_pages = [
            {"bounds": _element_bounds(element, is_page=True), "items": []}
            for element in master_root.iter()
            if _local_name(element) == "Page"
        ]
        master_unplaced: list[dict[str, Any]] = []
        for xml_order, element in enumerate(master_root.iter()):
            if _local_name(element) != "TextFrame":
                continue
            story_id = str(element.get("ParentStory") or "").strip()
            if not story_id or story_id == "n":
                continue
            item = {
                "story_id": story_id,
                "bounds": _element_bounds(element),
                "xml_order": xml_order,
                "spread": master_name,
            }
            page_index = _matching_page_index(item["bounds"], master_pages)
            if page_index is None:
                master_unplaced.append(item)
            else:
                master_pages[page_index]["items"].append(item)
        master_spreads[str(master_element.get("Self"))] = {
            "order": master_order,
            "name": master_name,
            "pages": master_pages,
            "unplaced": master_unplaced,
        }

    # 把母版 TextFrame 映射到首次实际显示它的页面；同一个母版 Story 只解析一次。
    for page in page_records:
        master = master_spreads.get(page["applied_master"])
        if not master:
            continue
        transform = page["master_transform"]
        mapped_page_bounds = [
            _transform_bounds(master_page["bounds"], transform)
            for master_page in master["pages"]
        ]
        regular_bounds = page["bounds"]
        areas = [
            _intersection_area(regular_bounds, master_bounds)
            for master_bounds in mapped_page_bounds
        ]
        if not areas:
            continue
        master_page_index = max(range(len(areas)), key=areas.__getitem__)
        if areas[master_page_index] <= 0:
            continue
        for item in master["pages"][master_page_index]["items"]:
            page_items[page["document_page_index"]].append(
                {
                    **item,
                    "bounds": _transform_bounds(item["bounds"], transform),
                }
            )

    ordered_story_ids: list[str] = []
    placements: dict[str, dict[str, Any]] = {}
    seen_story_ids: set[str] = set()

    def append_story(item: dict[str, Any], page: dict[str, Any] | None = None) -> None:
        story_id = item["story_id"]
        if story_id in seen_story_ids or story_id not in story_id_to_name:
            return
        seen_story_ids.add(story_id)
        ordered_story_ids.append(story_id)
        story_name = story_id_to_name[story_id]
        bounds = item["bounds"]
        metadata: dict[str, Any] = {
            "story_order": len(ordered_story_ids) - 1,
            "frame_x": round(bounds[0], 3),
            "frame_y": round(bounds[1], 3),
        }
        if page:
            metadata.update(
                {
                    "document_page_index": page["document_page_index"],
                    "page_name": page["page_name"],
                    "spread": page["spread"],
                }
            )
        placements[story_name] = metadata

    for page in page_records:
        for item in _visual_item_order(page_items[page["document_page_index"]]):
            append_story(item, page)

    for _, item in sorted(
        unplaced_items,
        key=lambda entry: (
            entry[0],
            entry[1]["bounds"][1],
            entry[1]["bounds"][0],
            entry[1]["xml_order"],
        ),
    ):
        append_story(item)

    # 未被实际页面使用的母版仍保留，但放在已放置内容之后。
    for master in sorted(master_spreads.values(), key=lambda item: item["order"]):
        for master_page in master["pages"]:
            for item in _visual_item_order(master_page["items"]):
                append_story(item)
        for item in _visual_item_order(master["unplaced"]):
            append_story(item)

    ordered_names = [story_id_to_name[story_id] for story_id in ordered_story_ids]
    for story_name in designmap_story_names + sorted(story_names):
        if story_name in story_names and story_name not in ordered_names:
            ordered_names.append(story_name)
            placements[story_name] = {"story_order": len(ordered_names) - 1}
    return ordered_names, placements


class IdmlAdapter(FormatAdapter):
    """Adobe InDesign IDML 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".idml"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".idml"),
                segments=[],
                metadata={},
            )
        
        try:
            zf = zipfile.ZipFile(BytesIO(raw_bytes), 'r')
        except zipfile.BadZipFile as e:
            raise ParseError(filename="<unknown>", reason=f"无效的 IDML 文件: {str(e)}")
        
        nodes = []
        story_count = 0

        story_names = [
            name
            for name in zf.namelist()
            if name.startswith('Stories/') and name.endswith('.xml')
        ]
        try:
            ordered_story_names, story_placements = _idml_layout_story_order(
                zf,
                story_names,
            )
            story_order_source = "layout"
        except Exception:
            # 旧版或非标准 IDML 缺少有效版面关系时，保持原有确定性兜底。
            ordered_story_names = sorted(story_names)
            story_placements = {}
            story_order_source = "filename"

        # 按页面和 TextFrame 的视觉顺序遍历 Stories。
        for name in ordered_story_names:
            try:
                content = zf.read(name)
                story_nodes = self._parse_story(content, name)
                placement = story_placements.get(name, {})
                for node in story_nodes:
                    node.metadata.update(placement)
                nodes.extend(story_nodes)
                story_count += 1
            except Exception:
                continue
        
        zf.close()
        
        ast = DocumentAST(nodes=nodes, source_format=".idml")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={
                "story_count": story_count,
                "story_order_source": story_order_source,
            },
        )

    def _parse_story(self, content: bytes, story_name: str) -> List[BlockNode]:
        """解析单个 Story XML 文件"""
        nodes = []
        
        try:
            parser = etree.XMLParser(remove_blank_text=True, recover=True)
            root = etree.fromstring(content, parser=parser)
        except etree.XMLSyntaxError:
            return nodes
        
        # paragraph_index 是 Story 内稳定的结构坐标，导出时据此把句段译文
        # 重新写回原 ParagraphStyleRange，避免依赖 Content 文本完全匹配。
        paragraph_index = -1

        # 查找所有 ParagraphStyleRange 或 Content 元素
        for para in root.iter():
            if _local_name(para) == 'ParagraphStyleRange':
                paragraph_index += 1
                paragraph_parts = _paragraph_text_parts(para)
                if paragraph_parts:
                    # 获取段落样式
                    style = para.get('AppliedParagraphStyle', '')
                    
                    # 判断是否是标题
                    node_type = NodeType.PARAGRAPH
                    if 'Heading' in style or 'Title' in style:
                        node_type = NodeType.HEADING
                    
                    for part_index, text in enumerate(paragraph_parts):
                        nodes.append(BlockNode(
                            node_type=node_type,
                            text_content=text,
                            metadata={
                                "story": story_name,
                                "style": style,
                                "paragraph_index": paragraph_index,
                                "paragraph_part_index": part_index,
                            },
                        ))
        
        return nodes
