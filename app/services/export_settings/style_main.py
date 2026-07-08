"""
docx_renderer_v2.py
忠实还原 DOCX 文件：精确字体渲染 + 图表/图片/Drawing 原生保留

核心策略：
  - 文本 Run：精确还原 rFonts 四槽位 + sz/szCs + 所有格式属性
  - 非文本元素（Chart/Image/Drawing/OLE）：保留原始 XML 片段，直接回写
  - 表格：保留行列坐标，精确重建结构

依赖: pip install python-docx lxml
"""

from __future__ import annotations
import copy
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any
from docx.oxml.ns import qn
from lxml import etree


# ══════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

ALL_NS = {
    "w": W_NS,
    "r": R_NS,
    "a": A_NS,
    "wp": WP_NS,
    "c": C_NS,
    "pic": PIC_NS,
    "mc": MC_NS,
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}


# ══════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════
"""Run 级别的计算样式——精确到四个字体槽位"""
# Toggle 字段集合：这些字段遵循 Word 的"翻转"继承语义
_TOGGLE_FIELDS = frozenset({
    "bold", "bold_cs", "italic", "italic_cs",
    "strike", "dstrike", "outline", "shadow",
    "emboss", "imprint", "vanish", "small_caps", "all_caps",
})

@dataclass
class RunStyle:
    font_ascii: Optional[str] = None
    font_east_asia: Optional[str] = None
    font_hAnsi: Optional[str] = None
    font_cs: Optional[str] = None
    font_size: Optional[float] = None      # 磅
    font_size_cs: Optional[float] = None   # 磅
    bold: Optional[bool] = None
    bold_cs: Optional[bool] = None
    italic: Optional[bool] = None
    italic_cs: Optional[bool] = None
    underline: Optional[str] = None
    strike: Optional[bool] = None
    dstrike: Optional[bool] = None
    outline: Optional[bool] = None
    shadow: Optional[bool] = None
    emboss: Optional[bool] = None
    imprint: Optional[bool] = None
    vanish: Optional[bool] = None
    small_caps: Optional[bool] = None
    all_caps: Optional[bool] = None
    color: Optional[str] = None
    highlight: Optional[str] = None
    shading: Optional[str] = None
    spacing: Optional[float] = None
    kern: Optional[float] = None
    position: Optional[float] = None
    # Issue #8: w:w (character scaling %) and w:fitText
    char_scale: Optional[int] = None       # w:w val, percentage e.g. 100
    fit_text: Optional[int] = None         # w:fitText val, twips width
    vertical_align: Optional[str] = None
    lang_val: Optional[str] = None
    lang_east_asia: Optional[str] = None
    lang_bidi: Optional[str] = None

    # Toggle 原始值：记录该层是否显式设置了 toggle（用于翻转计算）
    # 格式: {"bold": True/False, ...}  None 表示该层未设置
    _toggle_raw: dict = field(default_factory=dict, repr=False, compare=False)

    def merge_from(self, lower: "RunStyle") -> None:
        """
        高优先级（self）合并低优先级（lower）。
        Toggle 字段（bold/italic 等）遵循 Word 翻转语义：
          - 若 self 显式设置了该 toggle → 翻转 lower 的值作为最终值
          - 若 self 未设置 → 直接继承 lower 的值
        非 toggle 字段：self 有值则保留，否则从 lower 填充。
        """
        for fld in self.__dataclass_fields__:
            if fld.startswith("_"):
                continue
            if fld in _TOGGLE_FIELDS:
                self_raw = self._toggle_raw.get(fld)  # None=未设置, True/False=显式值
                lower_val = getattr(lower, fld)
                if self_raw is None:
                    # 当前层未设置，直接继承
                    if lower_val is not None:
                        setattr(self, fld, lower_val)
                else:
                    # 当前层显式设置了 toggle
                    if lower_val is None:
                        # 下层没有值，直接用当前层的值
                        setattr(self, fld, self_raw)
                    else:
                        # Word toggle 翻转规则：当前层 True → 翻转下层值
                        # 当前层 False (w:val="0") → 强制关闭
                        if self_raw is True:
                            setattr(self, fld, not lower_val)
                        else:
                            setattr(self, fld, False)
            else:
                if getattr(self, fld) is None:
                    val = getattr(lower, fld)
                    if val is not None:
                        setattr(self, fld, val)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None and not k.startswith("_")}

"""段落级别计算样式"""
@dataclass
class ParagraphStyle:
    alignment: Optional[str] = None
    outline_level: Optional[int] = None
    line_spacing: Optional[float] = None
    line_spacing_rule: Optional[str] = None
    space_before: Optional[float] = None
    space_after: Optional[float] = None
    indent_left: Optional[float] = None
    indent_right: Optional[float] = None
    indent_first_line: Optional[float] = None
    indent_hanging: Optional[float] = None
    keep_next: Optional[bool] = None
    keep_lines: Optional[bool] = None
    page_break_before: Optional[bool] = None
    embedded_rpr: Optional[RunStyle] = None
    section_xml: Optional[str] = None  # 存储 w:sectPr 的原始 XML，用于分节分栏配置
    # Issue #6: section layout fields parsed from sectPr
    page_width: Optional[float] = None      # 磅，页面宽度
    page_height: Optional[float] = None     # 磅，页面高度
    page_cols: Optional[int] = None         # 分栏数
    page_margin_left: Optional[float] = None
    page_margin_right: Optional[float] = None
    num_id: Optional[int] = None        # w:numPr -> w:numId 的值
    num_ilvl: Optional[int] = None      # w:numPr -> w:ilvl 的值（缩进级别，0-based）
    num_id_is_direct: bool = False      # numPr 是否来自段落直接格式（非样式继承）

    def merge_from(self, lower: "ParagraphStyle") -> None:
        for fld in self.__dataclass_fields__:
            if fld == "embedded_rpr":
                continue
            if getattr(self, fld) is None:
                val = getattr(lower, fld)
                if val is not None:
                    setattr(self, fld, val)
        if self.embedded_rpr is None and lower.embedded_rpr is not None:
            self.embedded_rpr = copy.deepcopy(lower.embedded_rpr)
        elif self.embedded_rpr is not None and lower.embedded_rpr is not None:
            self.embedded_rpr.merge_from(lower.embedded_rpr)

"""表格位置与样式元数据"""
@dataclass
class TableMeta:
    table_id: int = 0
    row: int = 0
    col: int = 0
    total_rows: int = 0
    total_cols: int = 0
    cell_width: Optional[float] = None
    table_pr_xml: Optional[str] = None  # 存储 w:tblPr 的原始 XML
    cell_pr_xml: Optional[str] = None   # 存储 w:tcPr 的原始 XML
    is_merged: bool = False             # 是否是被合并的单元格
    grid_span: int = 1                  # 跨列数
    v_merge: Optional[str] = None       # 垂直合并状态 ("restart" / "continue")
    table_xml: Optional[str] = None     # 存储整个 w:tbl 的原始 XML（用于精确还原合并单元格）
    # Issue #3: table style name for style inheritance
    table_style_name: Optional[str] = None  # tblStyle styleId

"""
非文本嵌入对象（图表/图片/Drawing/OLE/公式等）
保存原始 XML 片段，渲染时原样回写
"""
@dataclass
class EmbeddedObject:
    object_type: str                  # "drawing" / "chart" / "image" / "ole" / "pict" / "math"
    xml_snippet: str                  # 原始 XML 字符串
    relationship_ids: list[str] = field(default_factory=list)  # 引用的 rId 列表

"""增强版文本片段——支持嵌入对象"""
@dataclass
class TextSegment:
    index: int
    source: str                             # "正文" / "表格" / "页眉" / "页脚"
    text: str
    paragraph_style_name: Optional[str] = None
    character_style_name: Optional[str] = None
    computed_run_style: Optional[RunStyle] = None
    computed_para_style: Optional[ParagraphStyle] = None
    style_layers: dict[str, dict] = field(default_factory=dict)
    table_meta: Optional[TableMeta] = None
    paragraph_id: Optional[int] = None

    direct_run_style: Optional[RunStyle] = None  # 仅直接格式层（1_directFormat）的 run 样式

    # ── 嵌入对象 ──
    is_embedded_object: bool = False
    embedded_object: Optional[EmbeddedObject] = None

    # ── 原始段落 XML（用于精确还原无法解析的复杂段落）──
    raw_paragraph_xml: Optional[str] = None


# ══════════════════════════════════════════════
# XML 工具函数
# ══════════════════════════════════════════════

def _get_toggle(elem: Optional[etree._Element]) -> Optional[bool]:
    if elem is None:
        return None
    val = elem.get(qn("w:val"), "true")
    return val.lower() not in ("0", "false", "off")


def _get_toggle_raw(elem: Optional[etree._Element]) -> Optional[bool]:
    """
    与 _get_toggle 相同，但语义上表示"该层显式声明了此 toggle"。
    返回 None 表示元素不存在（未声明），True/False 表示显式值。
    用于 RunStyle._toggle_raw 记录，以支持 Word toggle 翻转继承。
    """
    return _get_toggle(elem)


def _half_pt(val_str: Optional[str]) -> Optional[float]:
    """半磅 -> 磅"""
    if val_str and val_str.lstrip("-").isdigit():
        return int(val_str) / 2.0
    return None


def _twips(val_str: Optional[str]) -> Optional[float]:
    """缇 (1/20 磅) -> 磅"""
    if val_str is None:
        return None
    try:
        return int(val_str) / 20.0
    except ValueError:
        return None


def _elem_to_xml_str(elem: etree._Element) -> str:
    """将 XML 元素序列化为字符串，保留命名空间"""
    return etree.tostring(elem, encoding="unicode")


def _xml_str_to_elem(xml_str: str) -> etree._Element:
    """将 XML 字符串反序列化为元素"""
    return etree.fromstring(xml_str.encode("utf-8") if isinstance(xml_str, str) else xml_str)


def _find_relationship_ids(elem: etree._Element) -> list[str]:
    """递归查找元素中所有 r:id / r:embed / r:link 属性值"""
    rids: list[str] = []
    r_id_attrs = [
        qn("r:id"), qn("r:embed"), qn("r:link"),
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed",
    ]
    for attr in r_id_attrs:
        val = elem.get(attr)
        if val:
            rids.append(val)
    for child in elem.iter():
        for attr in r_id_attrs:
            val = child.get(attr)
            if val and val not in rids:
                rids.append(val)
    return rids

# ══════════════════════════════════════════════
# 序列化工具
# ══════════════════════════════════════════════

class SegmentSerializerV2:
    @staticmethod
    def to_json(segments: list[TextSegment], json_path: str | Path) -> None:
        """将片段列表保存为 JSON"""
        data = [asdict(s) for s in segments]
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(json_path: str | Path) -> list[TextSegment]:
        """从 JSON 加载片段列表"""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        segments = []
        for d in data:
            # 还原嵌套 dataclass
            if d.get("computed_run_style"):
                d["computed_run_style"] = RunStyle(**d["computed_run_style"])
            if d.get("computed_para_style"):
                ps_data = d["computed_para_style"]
                if ps_data.get("embedded_rpr"):
                    ps_data["embedded_rpr"] = RunStyle(**ps_data["embedded_rpr"])
                d["computed_para_style"] = ParagraphStyle(**ps_data)
            if d.get("table_meta"):
                d["table_meta"] = TableMeta(**d["table_meta"])
            if d.get("embedded_object"):
                d["embedded_object"] = EmbeddedObject(**d["embedded_object"])
            
            segments.append(TextSegment(**d))
        return segments


# ══════════════════════════════════════════════
# 流水线
# ══════════════════════════════════════════════

def extract_and_render(
    input_path: str | Path,
    output_path: str | Path,
    json_path: Optional[str | Path] = None,
    adjuster: Any = None,
) -> None:
    """完整流水线：提取 -> (可选)样式调整 -> 渲染"""
    from style_extract import EnhancedExtractorV2
    from style_randering import DocxRendererV2

    input_path = Path(input_path)
    output_path = Path(output_path)

    print(f"\n{'=' * 70}")
    print(f"  [DOCX] DOCX 精确还原引擎 V2")
    print(f"  [IN]   输入: {input_path}")
    print(f"  [OUT]  输出: {output_path}")
    print(f"{'=' * 70}")

    # 提取
    print("\n[1/3] 提取文本与计算样式...")
    extractor = EnhancedExtractorV2(input_path)
    segments = extractor.extract_all()

    src_count: dict[str, int] = {}
    emb_count: dict[str, int] = {}
    for seg in segments:
        src_count[seg.source] = src_count.get(seg.source, 0) + 1
        if seg.is_embedded_object and seg.embedded_object:
            etype = seg.embedded_object.object_type
            emb_count[etype] = emb_count.get(etype, 0) + 1

    print(f"   共 {len(segments)} 个片段")
    for src, cnt in sorted(src_count.items()):
        print(f"     {src}: {cnt}")
    if emb_count:
        print(f"   嵌入对象:")
        for etype, cnt in sorted(emb_count.items()):
            print(f"     {etype}: {cnt}")

    # 保存 JSON
    if json_path:
        print(f"\n[2/3] 保存中间数据...")
        SegmentSerializerV2.to_json(segments, json_path)

    if adjuster is not None and hasattr(adjuster, "apply_to_segments"):
        adjuster.apply_to_segments(segments)

    print(f"\n[3/3] 渲染为新 DOCX（精确字体 + 嵌入资源注入）...")
    renderer = DocxRendererV2(source_path=input_path)
    renderer.render(segments, output_path, adjuster=adjuster)

    print(f"\n{'=' * 70}")
    print(f"  [OK] 完成！")
    print(f"{'=' * 70}\n")


def render_from_json(
    json_path: str | Path,
    output_path: str | Path,
    source_docx: Optional[str | Path] = None,
) -> None:
    """从 JSON 渲染（需要源 DOCX 来获取嵌入资源）"""
    from style_randering import DocxRendererV2
    
    segments = SegmentSerializerV2.from_json(json_path)
    renderer = DocxRendererV2(source_path=source_docx)
    renderer.render(segments, output_path)


# ══════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════

if __name__ == "__main__":
    # ===== 手动指定路径 =====
    input_docx = (
        r"C:\Users\H\Desktop\word解析和还原"
        r"\原文-含不可编辑_01 (2026-007)2025年年度报告.docx"
    )
    output_docx = r"C:\Users\H\Desktop\还原文档测试.docx"
    blueprint_dir = r"C:\Users\H\Desktop\word解析和还原\data.json"
    extract_and_render(input_docx, output_docx, blueprint_dir)
    # from style_adjuster import StyleAdjuster
    #
    # adjuster = StyleAdjuster()
    # adjuster.set_style("Normal", font_east_asia="Times New Roman", font_size=12)
    # adjuster.set_style("heading 1", font_size=16, bold=True)
    #
    # extract_and_render(input_docx, output_docx, adjuster=adjuster)
    #
    # # 或使用预设
    # # extract_adjust_render(input_docx, output_docx, adjuster=Presets.fang_song_14pt())