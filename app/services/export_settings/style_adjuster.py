"""
style_adjuster.py
样式调整器：直接修改 styles.xml 中的样式定义，让所有段落通过继承自动获得新样式。

核心思路：
  - 不修改任何 segment 的 run 直接格式
  - 直接在 styles.xml 中修改对应样式的 w:rPr（字体/字号/加粗等）
  - Word 打开时所有段落通过样式继承自动应用新格式
  - 未被修改的样式完全保持原文定义

支持：
  - 按样式名修改（'Normal'、'heading 1' 等）
  - 修改 docDefaults（文档默认字体，影响所有未指定字体的样式）
  - 交互式向导
  - 预设
"""

from __future__ import annotations
import copy
from typing import Optional, TYPE_CHECKING
from lxml import etree

if TYPE_CHECKING:
    from style_main import TextSegment, RunStyle as _RunStyle

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _q(tag: str) -> str:
    return f"{{{_W}}}{tag}"


# ══════════════════════════════════════════════
# 单条样式修改规则
# ══════════════════════════════════════════════

class StyleRule:
    """
    描述对某个具名样式的修改。
    所有字段均为 Optional，None 表示"不修改该字段"。

    Run 级别（字符格式）
    ─────────────────────────────────────────────
    font_ascii        西文字体，如 "Times New Roman"
    font_east_asia    中文字体，如 "宋体"
    font_hAnsi        hAnsi 字体（默认同 font_ascii）
    font_cs           复杂文种字体（默认同 font_east_asia）
    font_size         字号（磅），如 12.0
    font_size_cs      复杂文种字号（默认同 font_size）
    bold              加粗，True/False
    italic            斜体，True/False
    strike            单删除线，True/False
    dstrike           双删除线，True/False
    underline         下划线样式，如 "single"/"double"/"dotted"/"dash"/"none"
    color             字体颜色 hex（不带#），如 "FF0000"
    highlight         高亮颜色名，如 "yellow"/"cyan"/"green" 等
    shading           字符底纹 hex（不带#），如 "FFFF00"
    char_spacing      字符间距（磅，正=加宽，负=紧缩），对应 w:spacing
    char_scale        字符缩放百分比（整数），如 100/80/120，对应 w:w
    kern              字距调整起始字号（磅），如 14.0，对应 w:kern
    position          字符位置偏移（磅，正=上升，负=下降），对应 w:position
    vertical_align    上下标，"superscript"/"subscript"/"baseline"
    small_caps        小型大写字母，True/False
    all_caps          全部大写，True/False
    vanish            隐藏文字，True/False

    段落级别
    ─────────────────────────────────────────────
    alignment         对齐，"left"/"center"/"right"/"both"/"distribute"
    line_spacing      行间距（磅）
    line_spacing_rule 行距规则，"auto"/"exact"/"atLeast"
    space_before      段前间距（磅）
    space_after       段后间距（磅）
    indent_left       左缩进（磅）
    indent_right      右缩进（磅）
    indent_first_line 首行缩进（磅，与 indent_hanging 互斥）
    indent_hanging    悬挂缩进（磅，与 indent_first_line 互斥）
    keep_next         与下段同页，True/False
    keep_lines        段落内不分页，True/False
    page_break_before 段前分页，True/False

    表格样式（仅对 table 样式有效）
    ─────────────────────────────────────────────
    tbl_style_name    应用的表格样式名，如 "Table Grid"
    tbl_layout_type   对应 Word「表格属性 → 表格 → 自动调整」下的三个选项，
                       取值必须是以下三者之一（不再是旧版的 "autofit"/"fixed" 两值）：
                         "autofit_content" = 根据内容自动调整表格
                             列宽随单元格文字内容撑开/收缩，不受页面宽度限制。
                             对应 XML: w:tblLayout=autofit + w:tblW type=auto。
                         "autofit_window"  = 根据窗口自动调整表格
                             表格总宽跟随页面/窗口宽度自适应，各列按原有宽度比例分配。
                             对应 XML: w:tblLayout=autofit + w:tblW type=pct
                             （用 tbl_width_pct 指定百分比，默认 100，即占满页面可用宽度）。
                         "fixed"           = 固定列宽
                             列宽固定为文档中已有的数值，不随内容或窗口变化。
                             对应 XML: w:tblLayout=fixed，原有的 dxa 宽度保持不变。
                       兼容说明：旧版传入 "autofit" 会自动当作 "autofit_content" 处理。
    tbl_width_pct     表格总宽度百分比（仅 "autofit_window" 时生效，默认 100）
    tbl_cell_margin_top    单元格上边距（磅）
    tbl_cell_margin_bottom 单元格下边距（磅）
    tbl_cell_margin_left   单元格左边距（磅）
    tbl_cell_margin_right  单元格右边距（磅）
    """

    def __init__(
        self,
        # ── 字体 ──
        font_ascii: Optional[str] = None,
        font_east_asia: Optional[str] = None,
        font_hAnsi: Optional[str] = None,
        font_cs: Optional[str] = None,
        font_size: Optional[float] = None,
        font_size_cs: Optional[float] = None,
        # ── 字重/字形 ──
        bold: Optional[bool] = None,
        italic: Optional[bool] = None,
        strike: Optional[bool] = None,
        dstrike: Optional[bool] = None,
        underline: Optional[str] = None,       # "single"/"double"/"dotted"/"dash"/"none" 等
        small_caps: Optional[bool] = None,
        all_caps: Optional[bool] = None,
        vanish: Optional[bool] = None,
        # ── 颜色/底纹 ──
        color: Optional[str] = None,           # hex 不带 #，如 "FF0000"
        highlight: Optional[str] = None,       # "yellow"/"cyan"/"green" 等
        shading: Optional[str] = None,         # hex 不带 #
        # ── 字符间距 ──
        char_spacing: Optional[float] = None,  # 磅，w:spacing val（正=加宽，负=紧缩）
        char_scale: Optional[int] = None,      # 百分比整数，w:w val
        kern: Optional[float] = None,          # 磅，w:kern val
        position: Optional[float] = None,      # 磅，w:position val
        vertical_align: Optional[str] = None,  # "superscript"/"subscript"/"baseline"
        # ── 段落 ──
        alignment: Optional[str] = None,
        line_spacing: Optional[float] = None,
        line_spacing_rule: Optional[str] = None,
        space_before: Optional[float] = None,
        space_after: Optional[float] = None,
        indent_left: Optional[float] = None,
        indent_right: Optional[float] = None,
        indent_first_line: Optional[float] = None,
        indent_hanging: Optional[float] = None,
        keep_next: Optional[bool] = None,
        keep_lines: Optional[bool] = None,
        page_break_before: Optional[bool] = None,
        # ── 表格整体 ──
        tbl_style_name: Optional[str] = None,
        # "autofit_content"=根据内容自动调整 / "autofit_window"=根据窗口自动调整 / "fixed"=固定列宽
        # （旧值 "autofit" 兼容处理为 "autofit_content"）
        tbl_layout_type: Optional[str] = None,
        tbl_width_pct: Optional[int] = None,         # 表格总宽度百分比，仅 autofit_window 时生效，默认 100
        tbl_indent: Optional[float] = None,          # 表格左缩进（磅）
        tbl_align: Optional[str] = None,             # 表格对齐 "left"/"center"/"right"
        tbl_border_style: Optional[str] = None,      # 边框样式 "single"/"none"/"double" 等
        tbl_border_size: Optional[int] = None,       # 边框粗细（1/8 磅单位），如 4=0.5pt
        tbl_border_color: Optional[str] = None,      # 边框颜色 hex，如 "000000"
        # ── 单元格边距 ──
        tbl_cell_margin_top: Optional[float] = None,
        tbl_cell_margin_bottom: Optional[float] = None,
        tbl_cell_margin_left: Optional[float] = None,
        tbl_cell_margin_right: Optional[float] = None,
        # ── 表格内段落格式（应用到所有单元格段落）──
        tbl_para_line_spacing: Optional[float] = None,       # 行间距（磅）
        tbl_para_line_spacing_rule: Optional[str] = None,    # "auto"/"exact"/"atLeast"
        tbl_para_space_before: Optional[float] = None,       # 段前间距（磅）
        tbl_para_space_after: Optional[float] = None,        # 段后间距（磅）
        tbl_para_alignment: Optional[str] = None,            # 段落对齐
        # ── 表格内 Run 格式（应用到所有单元格文字）──
        tbl_run_font_ascii: Optional[str] = None,
        tbl_run_font_east_asia: Optional[str] = None,
        tbl_run_font_size: Optional[float] = None,
        tbl_run_bold: Optional[bool] = None,
        tbl_run_color: Optional[str] = None,
        tbl_run_char_spacing: Optional[float] = None,        # 字符间距（磅）
        # ── 单元格文字方向（应用到所有单元格）──
        # "lrTb"  = 水平方向（默认）
        # "tbRl"  = 垂直方向，从右往左
        # "btLr"  = 垂直方向，从左往右
        # "lrTbV" = 所有文字顺时针旋转90°
        # "tbRlV" = 所有文字逆时针旋转90°
        # "tbLrV" = 中文字符逆时针旋转90°
        tbl_cell_text_direction: Optional[str] = None,
    ):
        self.font_ascii = font_ascii
        self.font_east_asia = font_east_asia
        self.font_hAnsi = font_hAnsi or font_ascii
        self.font_cs = font_cs or font_east_asia
        self.font_size = font_size
        self.font_size_cs = font_size_cs or font_size
        self.bold = bold
        self.italic = italic
        self.strike = strike
        self.dstrike = dstrike
        self.underline = underline
        self.small_caps = small_caps
        self.all_caps = all_caps
        self.vanish = vanish
        self.color = color
        self.highlight = highlight
        self.shading = shading
        self.char_spacing = char_spacing
        self.char_scale = char_scale
        self.kern = kern
        self.position = position
        self.vertical_align = vertical_align
        self.alignment = alignment
        self.line_spacing = line_spacing
        self.line_spacing_rule = line_spacing_rule
        self.space_before = space_before
        self.space_after = space_after
        self.indent_left = indent_left
        self.indent_right = indent_right
        self.indent_first_line = indent_first_line
        self.indent_hanging = indent_hanging
        self.keep_next = keep_next
        self.keep_lines = keep_lines
        self.page_break_before = page_break_before
        self.tbl_style_name = tbl_style_name
        self.tbl_layout_type = tbl_layout_type
        self.tbl_width_pct = tbl_width_pct
        self.tbl_indent = tbl_indent
        self.tbl_align = tbl_align
        self.tbl_border_style = tbl_border_style
        self.tbl_border_size = tbl_border_size
        self.tbl_border_color = tbl_border_color
        self.tbl_cell_margin_top = tbl_cell_margin_top
        self.tbl_cell_margin_bottom = tbl_cell_margin_bottom
        self.tbl_cell_margin_left = tbl_cell_margin_left
        self.tbl_cell_margin_right = tbl_cell_margin_right
        self.tbl_para_line_spacing = tbl_para_line_spacing
        self.tbl_para_line_spacing_rule = tbl_para_line_spacing_rule
        self.tbl_para_space_before = tbl_para_space_before
        self.tbl_para_space_after = tbl_para_space_after
        self.tbl_para_alignment = tbl_para_alignment
        self.tbl_run_font_ascii = tbl_run_font_ascii
        self.tbl_run_font_east_asia = tbl_run_font_east_asia
        self.tbl_run_font_size = tbl_run_font_size
        self.tbl_run_bold = tbl_run_bold
        self.tbl_run_color = tbl_run_color
        self.tbl_run_char_spacing = tbl_run_char_spacing
        self.tbl_cell_text_direction = tbl_cell_text_direction

    def has_font(self) -> bool:
        return any(v is not None for v in [
            self.font_ascii, self.font_east_asia, self.font_hAnsi, self.font_cs
        ])

    def has_size(self) -> bool:
        return self.font_size is not None or self.font_size_cs is not None

    def has_run_props(self) -> bool:
        return self.has_font() or self.has_size() or any(v is not None for v in [
            self.bold, self.italic, self.strike, self.dstrike, self.underline,
            self.small_caps, self.all_caps, self.vanish,
            self.color, self.highlight, self.shading,
            self.char_spacing, self.char_scale, self.kern, self.position, self.vertical_align,
        ])

    def has_para_props(self) -> bool:
        return any(v is not None for v in [
            self.alignment, self.line_spacing, self.space_before, self.space_after,
            self.indent_left, self.indent_right, self.indent_first_line, self.indent_hanging,
            self.keep_next, self.keep_lines, self.page_break_before,
        ])

    def has_tbl_props(self) -> bool:
        return any(v is not None for v in [
            self.tbl_style_name, self.tbl_layout_type, self.tbl_width_pct,
            self.tbl_indent, self.tbl_align,
            self.tbl_border_style, self.tbl_border_size, self.tbl_border_color,
            self.tbl_cell_margin_top, self.tbl_cell_margin_bottom,
            self.tbl_cell_margin_left, self.tbl_cell_margin_right,
            self.tbl_para_line_spacing, self.tbl_para_line_spacing_rule,
            self.tbl_para_space_before, self.tbl_para_space_after, self.tbl_para_alignment,
            self.tbl_run_font_ascii, self.tbl_run_font_east_asia, self.tbl_run_font_size,
            self.tbl_run_bold, self.tbl_run_color, self.tbl_run_char_spacing,
            self.tbl_cell_text_direction,
        ])

    def __repr__(self) -> str:
        fields = {k: v for k, v in self.__dict__.items() if v is not None}
        return f"StyleRule({fields})"


# ══════════════════════════════════════════════
# 调整器主类
# ══════════════════════════════════════════════

class StyleAdjuster:
    """
    通过直接修改 styles.xml 来调整文档样式。

    使用方式：
        adjuster = StyleAdjuster()
        adjuster.set_style("Normal", font_east_asia="宋体", font_size=12)
        adjuster.set_style("heading 1", font_size=16, bold=True)
        adjuster.set_defaults(font_ascii="Times New Roman", font_size=12)

    然后传入 extract_and_render：
        extract_and_render(input_docx, output_docx, adjuster=adjuster)
    """

    def __init__(self):
        self._style_rules: dict[str, StyleRule] = {}
        self._defaults_rule: Optional[StyleRule] = None
        self._settings_rule: Optional[dict] = None

    def set_style(self, style_name: str, **kwargs) -> "StyleAdjuster":
        """
        修改指定样式名的字体/字号等属性。
        style_name: Word 样式名，如 'Normal'、'heading 1'、'heading 2'、'标题 1' 等。
        """
        self._style_rules[style_name] = StyleRule(**kwargs)
        return self

    def set_defaults(self, **kwargs) -> "StyleAdjuster":
        """
        修改文档默认字体（docDefaults），影响所有未显式指定字体的样式。
        """
        self._defaults_rule = StyleRule(**kwargs)
        return self

    def set_hyphenation(
        self,
        auto: Optional[bool] = None,
        consecutive_limit: Optional[int] = None,
        zone_pt: Optional[float] = None,
        do_not_hyphenate_caps: Optional[bool] = None,
    ) -> "StyleAdjuster":
        """
        设置文档级自动断字（对应 Word「布局→断字」菜单）。
        这些设置存放在 word/settings.xml 里，不是 styles.xml，所以单独用
        一个方法配置，最终由 apply_to_settings_xml 写入。

        参数（全部可选，None = 不修改，保持原文档设置不变）：
            auto                  是否开启自动断字。
                                   True  -> "布局→断字→自动"
                                   False -> "布局→断字→无"
            consecutive_limit     最大连续断字行数（int），对应 Word「断字选项→
                                   连续断字符限制」。0 或不填 = 不限制。
            zone_pt                断字区宽度（磅），对应 Word「断字选项→断字区」。
                                   值越小断字越积极（断字更频繁），Word 默认 0.25
                                   英寸 = 18 磅。
            do_not_hyphenate_caps  True = 全部大写的单词不参与断字（Word「断字
                                   选项→大写单词不断字」勾选项）。

        示例：
            adjuster.set_hyphenation(auto=True, consecutive_limit=2, zone_pt=18)
        """
        self._settings_rule = {
            "auto": auto,
            "consecutive_limit": consecutive_limit,
            "zone_pt": zone_pt,
            "do_not_hyphenate_caps": do_not_hyphenate_caps,
        }
        return self

    def clear(self) -> "StyleAdjuster":
        self._style_rules.clear()
        self._defaults_rule = None
        self._settings_rule = None
        return self

    def has_rule_for(self, style_name: Optional[str]) -> bool:
        """判断是否对指定样式名（或 styleId）设置了规则。
        style_name 为 None 表示段落没有显式样式（实际继承 Normal），
        此时检查是否有 Normal 规则或 defaults 规则。
        """
        if not style_name:
            return (
                self._defaults_rule is not None
                or "Normal" in self._style_rules
                or "normal" in self._style_rules
            )
        return style_name in self._style_rules

    def get_rule_for(self, style_name: Optional[str]) -> Optional["StyleRule"]:
        """获取指定样式名的规则，无则返回 None"""
        if not style_name:
            return self._defaults_rule
        return self._style_rules.get(style_name)

    def apply_to_table_xml(self, table_xml: str, rule: Optional["StyleRule"] = None) -> str:
        """
        对表格 XML 应用所有调整：
        - 表格整体属性（布局/宽度/对齐/边框/单元格边距）
        - 所有单元格段落的行间距/段前后/对齐
        - 所有单元格 run 的字体/字号/加粗/颜色/字符间距
        rule 为 None 时使用 _defaults_rule（如果有）
        """
        if rule is None:
            rule = self._defaults_rule
        if rule is None:
            return table_xml
        try:
            root = etree.fromstring(table_xml.encode("utf-8"))
        except Exception:
            return table_xml

        # ── 1. 表格整体属性 (tblPr) ──
        if rule.has_tbl_props():
            tblpr = root.find(_q("tblPr"))
            if tblpr is None:
                tblpr = etree.Element(_q("tblPr"))
                root.insert(0, tblpr)
            self._write_tblpr(tblpr, rule)

        # ── 2. 自动调整列宽 ──
        # tblLayout 本身已在 _write_tblpr 里写好，这里只负责清理列宽/单元格宽度，
        # 且只有「根据内容自动调整」需要清零宽度让 Word 按内容重新计算；
        # 「根据窗口自动调整」和「固定列宽」都要保留/使用现有的宽度数值，
        # 不能清零，否则窗口宽度模式会退化成内容模式。
        layout_type = rule.tbl_layout_type
        if layout_type == "autofit":  # 兼容旧值
            layout_type = "autofit_content"
        if layout_type == "autofit_content":
            tblgrid = root.find(_q("tblGrid"))
            if tblgrid is not None:
                for gc in tblgrid.findall(_q("gridCol")):
                    gc.attrib.pop(_q("w:w"), None)
                    gc.set(_q("w"), "0")
            # 清除每个单元格的固定宽度
            for tc in root.iter(_q("tc")):
                tcpr = tc.find(_q("tcPr"))
                if tcpr is not None:
                    tcw = tcpr.find(_q("tcW"))
                    if tcw is not None:
                        tcw.set(_q("type"), "auto")
                        tcw.set(_q("w"), "0")

        # ── 3. 单元格文字方向（写入每个 tcPr）──
        if rule.tbl_cell_text_direction is not None:
            for tc in root.iter(_q("tc")):
                tcpr = tc.find(_q("tcPr"))
                if tcpr is None:
                    tcpr = etree.Element(_q("tcPr"))
                    tc.insert(0, tcpr)
                old_td = tcpr.find(_q("textDirection"))
                if old_td is not None:
                    tcpr.remove(old_td)
                td = etree.SubElement(tcpr, _q("textDirection"))
                td.set(_q("val"), rule.tbl_cell_text_direction)

        # ── 4. 单元格段落格式 ──
        has_para = any(v is not None for v in [
            rule.tbl_para_line_spacing, rule.tbl_para_line_spacing_rule,
            rule.tbl_para_space_before, rule.tbl_para_space_after,
            rule.tbl_para_alignment,
        ])
        # ── 5. 单元格 run 格式 ──
        has_run = any(v is not None for v in [
            rule.tbl_run_font_ascii, rule.tbl_run_font_east_asia, rule.tbl_run_font_size,
            rule.tbl_run_bold, rule.tbl_run_color, rule.tbl_run_char_spacing,
        ])

        if has_para or has_run:
            for para in root.iter(_q("p")):
                if has_para:
                    ppr = para.find(_q("pPr"))
                    if ppr is None:
                        ppr = etree.Element(_q("pPr"))
                        para.insert(0, ppr)
                    # 行间距
                    if any(v is not None for v in [
                        rule.tbl_para_line_spacing, rule.tbl_para_line_spacing_rule,
                        rule.tbl_para_space_before, rule.tbl_para_space_after,
                    ]):
                        sp = ppr.find(_q("spacing"))
                        if sp is None:
                            sp = etree.SubElement(ppr, _q("spacing"))
                        if rule.tbl_para_line_spacing is not None:
                            # auto = 倍数（× 240），exact/atLeast = 磅值（× 20）
                            if rule.tbl_para_line_spacing_rule in ("auto", None):
                                sp.set(_q("line"), str(int(rule.tbl_para_line_spacing * 240)))
                            else:
                                sp.set(_q("line"), str(int(rule.tbl_para_line_spacing * 20)))
                        if rule.tbl_para_line_spacing_rule is not None:
                            sp.set(_q("lineRule"), rule.tbl_para_line_spacing_rule)
                        if rule.tbl_para_space_before is not None:
                            sp.set(_q("before"), str(int(rule.tbl_para_space_before * 20)))
                        if rule.tbl_para_space_after is not None:
                            sp.set(_q("after"), str(int(rule.tbl_para_space_after * 20)))
                    # 对齐
                    if rule.tbl_para_alignment is not None:
                        jc = ppr.find(_q("jc"))
                        if jc is None:
                            jc = etree.SubElement(ppr, _q("jc"))
                        jc.set(_q("val"), rule.tbl_para_alignment)

                if has_run:
                    for run in para.findall(_q("r")):
                        rpr = run.find(_q("rPr"))
                        if rpr is None:
                            rpr = etree.Element(_q("rPr"))
                            run.insert(0, rpr)
                        # 字体
                        if rule.tbl_run_font_ascii or rule.tbl_run_font_east_asia:
                            rf = rpr.find(_q("rFonts"))
                            if rf is None:
                                rf = etree.Element(_q("rFonts"))
                                rpr.insert(0, rf)
                            if rule.tbl_run_font_ascii:
                                rf.set(_q("ascii"), rule.tbl_run_font_ascii)
                                rf.set(_q("hAnsi"), rule.tbl_run_font_ascii)
                            if rule.tbl_run_font_east_asia:
                                rf.set(_q("eastAsia"), rule.tbl_run_font_east_asia)
                        # 字号
                        if rule.tbl_run_font_size is not None:
                            for tag in (_q("sz"), _q("szCs")):
                                e = rpr.find(tag)
                                if e is None:
                                    e = etree.SubElement(rpr, tag)
                                e.set(_q("val"), str(int(rule.tbl_run_font_size * 2)))
                        # 加粗
                        if rule.tbl_run_bold is not None:
                            StyleAdjuster._set_toggle(rpr, _q("b"), rule.tbl_run_bold)
                            StyleAdjuster._set_toggle(rpr, _q("bCs"), rule.tbl_run_bold)
                        # 颜色
                        if rule.tbl_run_color is not None:
                            c = rpr.find(_q("color"))
                            if c is None:
                                c = etree.SubElement(rpr, _q("color"))
                            c.set(_q("val"), rule.tbl_run_color)
                        # 字符间距
                        if rule.tbl_run_char_spacing is not None:
                            sp = rpr.find(_q("spacing"))
                            if sp is None:
                                sp = etree.SubElement(rpr, _q("spacing"))
                            sp.set(_q("val"), str(int(rule.tbl_run_char_spacing * 20)))

        return etree.tostring(root, encoding="unicode")

    # 保留旧名称作为别名，兼容渲染器现有调用
    def strip_font_from_table_xml(self, table_xml: str) -> str:
        """兼容旧调用，等同于 apply_to_table_xml"""
        return self.apply_to_table_xml(table_xml)

    def apply_to_segments(self, segments: list) -> None:
        """
        直接修改正文 segment 的 computed_run_style，按段落样式名匹配规则覆盖字体/字号等字段。
        表格 segment 不处理（表格走 table_xml 路径）。
        """
        if not self._style_rules and self._defaults_rule is None:
            return

        for seg in segments:
            if seg.source != "正文" or seg.is_embedded_object:
                continue
            rule = self._style_rules.get(seg.paragraph_style_name)
            if rule is None and not seg.paragraph_style_name:
                rule = (
                    self._style_rules.get("Normal")
                    or self._style_rules.get("normal")
                    or self._defaults_rule
                )
            if rule is None:
                continue

            if seg.computed_run_style is None:
                from style_main import RunStyle
                seg.computed_run_style = RunStyle()

            rs = seg.computed_run_style
            if rule.font_ascii is not None:
                rs.font_ascii = rule.font_ascii
            if rule.font_east_asia is not None:
                rs.font_east_asia = rule.font_east_asia
            if rule.font_hAnsi is not None:
                rs.font_hAnsi = rule.font_hAnsi
            if rule.font_cs is not None:
                rs.font_cs = rule.font_cs
            if rule.font_size is not None:
                rs.font_size = rule.font_size
            if rule.font_size_cs is not None:
                rs.font_size_cs = rule.font_size_cs
            if rule.bold is not None:
                rs.bold = rule.bold
                rs.bold_cs = rule.bold
            if rule.italic is not None:
                rs.italic = rule.italic
                rs.italic_cs = rule.italic
            if rule.strike is not None:
                rs.strike = rule.strike
            if rule.dstrike is not None:
                rs.dstrike = rule.dstrike
            if rule.underline is not None:
                rs.underline = rule.underline
            if rule.small_caps is not None:
                rs.small_caps = rule.small_caps
            if rule.all_caps is not None:
                rs.all_caps = rule.all_caps
            if rule.vanish is not None:
                rs.vanish = rule.vanish
            if rule.color is not None:
                rs.color = rule.color
            if rule.highlight is not None:
                rs.highlight = rule.highlight
            if rule.shading is not None:
                rs.shading = f"#{rule.shading.upper()}"
            if rule.char_spacing is not None:
                rs.spacing = rule.char_spacing
            if rule.kern is not None:
                rs.kern = rule.kern
            if rule.position is not None:
                rs.position = rule.position
            if rule.vertical_align is not None:
                rs.vertical_align = rule.vertical_align

            seg.direct_run_style = None

    def apply_to_styles_xml(self, styles_xml_bytes: bytes) -> bytes:
        """
        直接修改 styles.xml 中对应样式的定义，返回修改后的 bytes。
        未被规则覆盖的样式完全保持原文不变。
        """
        if not self._style_rules and self._defaults_rule is None:
            return styles_xml_bytes

        try:
            root = etree.fromstring(styles_xml_bytes)
        except Exception:
            return styles_xml_bytes

        if self._defaults_rule is not None:
            self._apply_rule_to_doc_defaults(root, self._defaults_rule)

        for style_elem in root.findall(_q("style")):
            style_name_elem = style_elem.find(_q("name"))
            if style_name_elem is None:
                continue
            style_name = style_name_elem.get(_q("val"), "")
            style_id = style_elem.get(_q("styleId"), "")

            rule = self._style_rules.get(style_name) or self._style_rules.get(style_id)
            if rule is None:
                continue

            if rule.has_run_props():
                self._apply_rule_to_rpr(style_elem, rule)
            if rule.has_para_props():
                self._apply_rule_to_ppr(style_elem, rule)
            if rule.has_tbl_props():
                self._apply_rule_to_tblpr(style_elem, rule)

        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    # ── schema 中断字四元素的相对顺序（CT_Settings 是有严格顺序要求的
    #    xsd:sequence，插错顺序 Word 打开会触发"发现无法读取的内容"修复）──
    _HYPHEN_TAGS = ("autoHyphenation", "consecutiveHyphenLimit", "hyphenationZone", "doNotHyphenateCaps")
    # 断字元素必须插在 defaultTabStop 之后（如果存在），没有则退化插最前面
    _HYPHEN_INSERT_AFTER = "defaultTabStop"

    def apply_to_settings_xml(self, settings_xml_bytes: bytes) -> bytes:
        """
        直接修改 word/settings.xml，写入 set_hyphenation() 配置的断字设置。
        未调用 set_hyphenation() 时原样返回，不做任何改动。
        """
        if not self._settings_rule:
            return settings_xml_bytes
        rule = self._settings_rule
        if all(v is None for v in rule.values()):
            return settings_xml_bytes

        try:
            root = etree.fromstring(settings_xml_bytes)
        except Exception:
            return settings_xml_bytes

        # 找插入锚点：优先插在 defaultTabStop 之后
        anchor_idx = 0
        for i, child in enumerate(root):
            if etree.QName(child).localname == self._HYPHEN_INSERT_AFTER:
                anchor_idx = i + 1
                break

        # 先移除已存在的同名元素，统一按 schema 顺序重新插入，避免顺序冲突
        for tag in self._HYPHEN_TAGS:
            old = root.find(_q(tag))
            if old is not None:
                root.remove(old)

        new_elems = []
        if rule.get("auto") is not None:
            e = etree.Element(_q("autoHyphenation"))
            e.set(_q("val"), "true" if rule["auto"] else "false")
            new_elems.append(e)
        if rule.get("consecutive_limit") is not None:
            e = etree.Element(_q("consecutiveHyphenLimit"))
            e.set(_q("val"), str(int(rule["consecutive_limit"])))
            new_elems.append(e)
        if rule.get("zone_pt") is not None:
            e = etree.Element(_q("hyphenationZone"))
            # 磅 -> 缇（1 磅 = 20 缇）
            e.set(_q("val"), str(int(rule["zone_pt"] * 20)))
            new_elems.append(e)
        if rule.get("do_not_hyphenate_caps") is not None:
            e = etree.Element(_q("doNotHyphenateCaps"))
            e.set(_q("val"), "1" if rule["do_not_hyphenate_caps"] else "0")
            new_elems.append(e)

        for offset, elem in enumerate(new_elems):
            root.insert(anchor_idx + offset, elem)

        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    @staticmethod
    def _get_or_create(parent: etree._Element, tag: str) -> etree._Element:
        elem = parent.find(tag)
        if elem is None:
            elem = etree.SubElement(parent, tag)
        return elem

    def _apply_rule_to_doc_defaults(self, root: etree._Element, rule: StyleRule) -> None:
        doc_defaults = self._get_or_create(root, _q("docDefaults"))
        rpr_default = self._get_or_create(doc_defaults, _q("rPrDefault"))
        rpr = self._get_or_create(rpr_default, _q("rPr"))
        self._write_rpr(rpr, rule)

    def _apply_rule_to_rpr(self, style_elem: etree._Element, rule: StyleRule) -> None:
        rpr = style_elem.find(_q("rPr"))
        if rpr is None:
            rpr = etree.SubElement(style_elem, _q("rPr"))
        self._write_rpr(rpr, rule)

    def _apply_rule_to_ppr(self, style_elem: etree._Element, rule: StyleRule) -> None:
        ppr = style_elem.find(_q("pPr"))
        if ppr is None:
            ppr = etree.SubElement(style_elem, _q("pPr"))
        self._write_ppr(ppr, rule)

    def _apply_rule_to_tblpr(self, style_elem: etree._Element, rule: StyleRule) -> None:
        tblpr = style_elem.find(_q("tblPr"))
        if tblpr is None:
            tblpr = etree.SubElement(style_elem, _q("tblPr"))
        self._write_tblpr(tblpr, rule)

    @staticmethod
    def _set_toggle(rpr: etree._Element, tag: str, value: Optional[bool]) -> None:
        """写入 toggle 属性（bold/italic/strike 等）：True=添加元素，False=添加 val=0，None=不动"""
        if value is None:
            return
        old = rpr.find(tag)
        if old is not None:
            rpr.remove(old)
        elem = etree.SubElement(rpr, tag)
        if not value:
            elem.set(_q("val"), "0")

    @staticmethod
    def _write_rpr(rpr: etree._Element, rule: StyleRule) -> None:
        # ── 字体 ──
        if rule.has_font():
            old_rf = rpr.find(_q("rFonts"))
            if old_rf is not None:
                rpr.remove(old_rf)
            rf = etree.Element(_q("rFonts"))
            if rule.font_ascii:
                rf.set(_q("ascii"), rule.font_ascii)
                rf.set(_q("hAnsi"), rule.font_hAnsi or rule.font_ascii)
            if rule.font_east_asia:
                rf.set(_q("eastAsia"), rule.font_east_asia)
            if rule.font_cs:
                rf.set(_q("cs"), rule.font_cs)
            rpr.insert(0, rf)

        # ── 字号 ──
        if rule.has_size():
            for tag in (_q("sz"), _q("szCs")):
                old = rpr.find(tag)
                if old is not None:
                    rpr.remove(old)
            if rule.font_size is not None:
                sz_elem = etree.SubElement(rpr, _q("sz"))
                sz_elem.set(_q("val"), str(int(rule.font_size * 2)))
            szcs_val = rule.font_size_cs or rule.font_size
            if szcs_val is not None:
                szcs_elem = etree.SubElement(rpr, _q("szCs"))
                szcs_elem.set(_q("val"), str(int(szcs_val * 2)))

        # ── toggle 属性 ──
        _set = StyleAdjuster._set_toggle
        _set(rpr, _q("b"),        rule.bold)
        _set(rpr, _q("bCs"),      rule.bold)
        _set(rpr, _q("i"),        rule.italic)
        _set(rpr, _q("iCs"),      rule.italic)
        _set(rpr, _q("strike"),   rule.strike)
        _set(rpr, _q("dstrike"),  rule.dstrike)
        _set(rpr, _q("smallCaps"), rule.small_caps)
        _set(rpr, _q("caps"),     rule.all_caps)
        _set(rpr, _q("vanish"),   rule.vanish)

        # ── 下划线 ──
        if rule.underline is not None:
            old = rpr.find(_q("u"))
            if old is not None:
                rpr.remove(old)
            u_elem = etree.SubElement(rpr, _q("u"))
            u_elem.set(_q("val"), rule.underline)

        # ── 颜色 ──
        if rule.color is not None:
            old = rpr.find(_q("color"))
            if old is not None:
                rpr.remove(old)
            color_elem = etree.SubElement(rpr, _q("color"))
            color_elem.set(_q("val"), rule.color)

        # ── 高亮 ──
        if rule.highlight is not None:
            old = rpr.find(_q("highlight"))
            if old is not None:
                rpr.remove(old)
            hl_elem = etree.SubElement(rpr, _q("highlight"))
            hl_elem.set(_q("val"), rule.highlight)

        # ── 字符底纹 ──
        if rule.shading is not None:
            old = rpr.find(_q("shd"))
            if old is not None:
                rpr.remove(old)
            shd_elem = etree.SubElement(rpr, _q("shd"))
            shd_elem.set(_q("val"), "clear")
            shd_elem.set(_q("color"), "auto")
            shd_elem.set(_q("fill"), rule.shading)

        # ── 字符间距 w:spacing（磅 -> 缇*20/2 = 磅*20） ──
        if rule.char_spacing is not None:
            old = rpr.find(_q("spacing"))
            if old is not None:
                rpr.remove(old)
            sp_elem = etree.SubElement(rpr, _q("spacing"))
            sp_elem.set(_q("val"), str(int(rule.char_spacing * 20)))

        # ── 字符缩放 w:w ──
        if rule.char_scale is not None:
            old = rpr.find(_q("w"))
            if old is not None:
                rpr.remove(old)
            w_elem = etree.SubElement(rpr, _q("w"))
            w_elem.set(_q("val"), str(int(rule.char_scale)))

        # ── 字距调整 w:kern（磅 -> 半磅*2） ──
        if rule.kern is not None:
            old = rpr.find(_q("kern"))
            if old is not None:
                rpr.remove(old)
            kern_elem = etree.SubElement(rpr, _q("kern"))
            kern_elem.set(_q("val"), str(int(rule.kern * 2)))

        # ── 位置偏移 w:position（磅 -> 半磅*2） ──
        if rule.position is not None:
            old = rpr.find(_q("position"))
            if old is not None:
                rpr.remove(old)
            pos_elem = etree.SubElement(rpr, _q("position"))
            pos_elem.set(_q("val"), str(int(rule.position * 2)))

        # ── 上下标 w:vertAlign ──
        if rule.vertical_align is not None:
            old = rpr.find(_q("vertAlign"))
            if old is not None:
                rpr.remove(old)
            va_elem = etree.SubElement(rpr, _q("vertAlign"))
            va_elem.set(_q("val"), rule.vertical_align)

    @staticmethod
    def _write_ppr(ppr: etree._Element, rule: StyleRule) -> None:
        # ── 对齐 ──
        if rule.alignment is not None:
            old = ppr.find(_q("jc"))
            if old is not None:
                ppr.remove(old)
            jc = etree.SubElement(ppr, _q("jc"))
            jc.set(_q("val"), rule.alignment)

        # ── 行距 / 段前后间距 ──
        has_spacing = any(v is not None for v in [
            rule.line_spacing, rule.line_spacing_rule, rule.space_before, rule.space_after
        ])
        if has_spacing:
            old = ppr.find(_q("spacing"))
            spacing = old if old is not None else etree.SubElement(ppr, _q("spacing"))
            if old is None:
                ppr.append(spacing)
            if rule.line_spacing is not None:
                # auto = 倍数（× 240），exact/atLeast = 磅值（× 20）
                if rule.line_spacing_rule in ("auto", None):
                    spacing.set(_q("line"), str(int(rule.line_spacing * 240)))
                else:
                    spacing.set(_q("line"), str(int(rule.line_spacing * 20)))
            if rule.line_spacing_rule is not None:
                spacing.set(_q("lineRule"), rule.line_spacing_rule)
            if rule.space_before is not None:
                spacing.set(_q("before"), str(int(rule.space_before * 20)))
            if rule.space_after is not None:
                spacing.set(_q("after"), str(int(rule.space_after * 20)))

        # ── 缩进 ──
        has_indent = any(v is not None for v in [
            rule.indent_left, rule.indent_right,
            rule.indent_first_line, rule.indent_hanging,
        ])
        if has_indent:
            old = ppr.find(_q("ind"))
            ind = old if old is not None else etree.SubElement(ppr, _q("ind"))
            if old is None:
                ppr.append(ind)
            if rule.indent_left is not None:
                ind.set(_q("left"), str(int(rule.indent_left * 20)))
            if rule.indent_right is not None:
                ind.set(_q("right"), str(int(rule.indent_right * 20)))
            if rule.indent_first_line is not None:
                # firstLine 和 hanging 互斥，写 firstLine 时清除 hanging
                ind.attrib.pop(_q("hanging"), None)
                ind.set(_q("firstLine"), str(int(rule.indent_first_line * 20)))
            if rule.indent_hanging is not None:
                ind.attrib.pop(_q("firstLine"), None)
                ind.set(_q("hanging"), str(int(rule.indent_hanging * 20)))

        # ── 分页控制 ──
        _set = StyleAdjuster._set_toggle
        _set(ppr, _q("keepNext"),          rule.keep_next)
        _set(ppr, _q("keepLines"),         rule.keep_lines)
        _set(ppr, _q("pageBreakBefore"),   rule.page_break_before)

    @staticmethod
    def _write_tblpr(tblpr: etree._Element, rule: StyleRule) -> None:
        # ── 表格样式名 ──
        if rule.tbl_style_name is not None:
            old = tblpr.find(_q("tblStyle"))
            if old is not None:
                tblpr.remove(old)
            ts = etree.Element(_q("tblStyle"))
            ts.set(_q("val"), rule.tbl_style_name)
            tblpr.insert(0, ts)

        # ── 表格宽度 ──
        # 注意：当 tbl_layout_type="autofit_window" 时，宽度由下面的
        # 布局类型分支统一处理（写 type=pct），此处仅处理用户单独指定
        # tbl_width_pct、但没有搭配 autofit_window 的场景。
        if rule.tbl_width_pct is not None and rule.tbl_layout_type != "autofit_window":
            old = tblpr.find(_q("tblW"))
            if old is not None:
                tblpr.remove(old)
            tw = etree.SubElement(tblpr, _q("tblW"))
            tw.set(_q("w"), str(rule.tbl_width_pct * 50))  # pct 单位：1/50 %
            tw.set(_q("type"), "pct")

        # ── 表格对齐 ──
        if rule.tbl_align is not None:
            old = tblpr.find(_q("jc"))
            if old is not None:
                tblpr.remove(old)
            jc = etree.SubElement(tblpr, _q("jc"))
            jc.set(_q("val"), rule.tbl_align)

        # ── 表格左缩进 ──
        if rule.tbl_indent is not None:
            old = tblpr.find(_q("tblInd"))
            if old is not None:
                tblpr.remove(old)
            ind = etree.SubElement(tblpr, _q("tblInd"))
            ind.set(_q("w"), str(int(rule.tbl_indent * 20)))
            ind.set(_q("type"), "dxa")

        # ── 布局类型：对应 Word「自动调整」的三个选项 ──
        # "autofit_content" / "autofit_window" / "fixed"（兼容旧值 "autofit"）
        if rule.tbl_layout_type is not None:
            layout_type = rule.tbl_layout_type
            if layout_type == "autofit":          # 兼容旧版两值写法
                layout_type = "autofit_content"

            old = tblpr.find(_q("tblLayout"))
            if old is not None:
                tblpr.remove(old)
            layout = etree.SubElement(tblpr, _q("tblLayout"))

            if layout_type == "fixed":
                # 固定列宽：w:tblLayout=fixed，宽度用现有 dxa 值，不在此处改动 tblW
                layout.set(_q("type"), "fixed")
            else:
                # autofit_content / autofit_window 在 XML 层都是 w:tblLayout=autofit，
                # 区别在于 tblW 的 type：
                #   autofit_content -> auto（跟内容走，本函数不改 tblW，交给
                #                       apply_to_table_xml 里清理 gridCol/tcW）
                #   autofit_window  -> pct（跟窗口走，占用页面宽度的百分比）
                layout.set(_q("type"), "autofit")
                if layout_type == "autofit_window":
                    old_w = tblpr.find(_q("tblW"))
                    if old_w is not None:
                        tblpr.remove(old_w)
                    tw = etree.SubElement(tblpr, _q("tblW"))
                    pct = rule.tbl_width_pct if rule.tbl_width_pct is not None else 100
                    tw.set(_q("w"), str(int(pct * 50)))  # pct 单位：1/50 %
                    tw.set(_q("type"), "pct")

        # ── 边框 ──
        if any(v is not None for v in [
            rule.tbl_border_style, rule.tbl_border_size, rule.tbl_border_color
        ]):
            old = tblpr.find(_q("tblBorders"))
            if old is not None:
                tblpr.remove(old)
            borders = etree.SubElement(tblpr, _q("tblBorders"))
            style = rule.tbl_border_style or "single"
            size  = rule.tbl_border_size  or 4       # 4 = 0.5pt
            color = rule.tbl_border_color or "000000"
            for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                b = etree.SubElement(borders, _q(side))
                b.set(_q("val"),   style)
                b.set(_q("sz"),    str(size))
                b.set(_q("space"), "0")
                b.set(_q("color"), color)

        # ── 单元格边距 ──
        has_margin = any(v is not None for v in [
            rule.tbl_cell_margin_top, rule.tbl_cell_margin_bottom,
            rule.tbl_cell_margin_left, rule.tbl_cell_margin_right,
        ])
        if has_margin:
            old = tblpr.find(_q("tblCellMar"))
            if old is not None:
                tblpr.remove(old)
            mar = etree.SubElement(tblpr, _q("tblCellMar"))
            for side, val in (
                ("top",    rule.tbl_cell_margin_top),
                ("bottom", rule.tbl_cell_margin_bottom),
                ("left",   rule.tbl_cell_margin_left),
                ("right",  rule.tbl_cell_margin_right),
            ):
                if val is not None:
                    side_elem = etree.SubElement(mar, _q(side))
                    side_elem.set(_q("w"), str(int(val * 20)))
                    side_elem.set(_q("type"), "dxa")

    def summary(self) -> str:
        lines = ["StyleAdjuster 规则汇总："]
        if self._defaults_rule:
            lines.append(f"  docDefaults: {self._defaults_rule}")
        for name, rule in self._style_rules.items():
            lines.append(f"  样式 {name!r}: {rule}")
        if len(lines) == 1:
            lines.append("  （无规则）")
        return "\n".join(lines)


# ══════════════════════════════════════════════
# 交互式配置向导
# ══════════════════════════════════════════════

def _ask(prompt: str, default: str = "") -> str:
    val = input(f"{prompt} [{default}]: ").strip()
    return val if val else default


def _ask_float(prompt: str, default: Optional[float] = None) -> Optional[float]:
    d = str(default) if default is not None else ""
    val = input(f"{prompt} [{d}]: ").strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        print("  输入无效，已跳过")
        return default


def _ask_bool(prompt: str, default: Optional[bool] = None) -> Optional[bool]:
    d = {True: "y", False: "n", None: ""}[default]
    val = input(f"{prompt} (y/n) [{d}]: ").strip().lower()
    if val in ("y", "yes", "1"):
        return True
    if val in ("n", "no", "0"):
        return False
    return default


def _collect_rule(label: str) -> Optional[StyleRule]:
    print(f"\n  ── {label} ──")
    print("  （直接回车跳过该项，保持原文不变）")

    font_ascii    = _ask("  西文字体 (font_ascii, 如 Times New Roman)") or None
    font_ea       = _ask("  中文字体 (font_east_asia, 如 宋体/仿宋/黑体)") or None
    font_size     = _ask_float("  字号(磅) (font_size, 如 12)")
    bold          = _ask_bool("  加粗 (bold)")
    italic        = _ask_bool("  斜体 (italic)")
    strike        = _ask_bool("  单删除线 (strike)")
    dstrike       = _ask_bool("  双删除线 (dstrike)")
    underline_raw = _ask("  下划线 (underline: single/double/dotted/dash/none)") or None
    small_caps    = _ask_bool("  小型大写 (small_caps)")
    all_caps      = _ask_bool("  全部大写 (all_caps)")
    vanish        = _ask_bool("  隐藏文字 (vanish)")
    color         = _ask("  字体颜色 hex (color, 如 FF0000，空=不改)") or None
    highlight_raw = _ask("  高亮颜色 (highlight: yellow/cyan/green/magenta/blue/red/darkBlue/darkCyan/darkGreen/darkMagenta/darkRed/darkYellow/darkGray/lightGray/black)") or None
    shading       = _ask("  字符底纹 hex (shading, 如 FFFF00，空=不改)") or None
    char_spacing  = _ask_float("  字符间距(磅) (char_spacing, 正=加宽 负=紧缩)")
    char_scale_r  = _ask_float("  字符缩放% (char_scale, 如 100/80/120)")
    kern_r        = _ask_float("  字距调整起始字号(磅) (kern, 如 14)")
    position_r    = _ask_float("  字符位置偏移(磅) (position, 正=上升 负=下降)")
    vert_align    = _ask("  上下标 (vertical_align: superscript/subscript/baseline)") or None
    line_spacing  = _ask_float("  行间距(磅) (line_spacing, 如 20)")
    space_before  = _ask_float("  段前间距(磅) (space_before)")
    space_after   = _ask_float("  段后间距(磅) (space_after)")
    indent_left   = _ask_float("  左缩进(磅) (indent_left)")
    indent_right  = _ask_float("  右缩进(磅) (indent_right)")
    indent_fl     = _ask_float("  首行缩进(磅) (indent_first_line)")
    indent_hang   = _ask_float("  悬挂缩进(磅) (indent_hanging)")
    keep_next     = _ask_bool("  与下段同页 (keep_next)")
    keep_lines    = _ask_bool("  段落内不分页 (keep_lines)")
    page_break    = _ask_bool("  段前分页 (page_break_before)")
    align_raw     = _ask("  对齐 (left/center/right/both/distribute)") or None
    tbl_style     = _ask("  表格样式名 (tbl_style_name, 如 Table Grid)") or None
    tbl_mar_top   = _ask_float("  单元格上边距(磅) (tbl_cell_margin_top)")
    tbl_mar_bot   = _ask_float("  单元格下边距(磅) (tbl_cell_margin_bottom)")
    tbl_mar_left  = _ask_float("  单元格左边距(磅) (tbl_cell_margin_left)")
    tbl_mar_right = _ask_float("  单元格右边距(磅) (tbl_cell_margin_right)")

    char_scale_int = int(char_scale_r) if char_scale_r is not None else None

    has_any = any(v is not None for v in [
        font_ascii, font_ea, font_size, bold, italic, strike, dstrike, underline_raw,
        small_caps, all_caps, vanish, color, highlight_raw, shading,
        char_spacing, char_scale_int, kern_r, position_r, vert_align,
        line_spacing, space_before, space_after,
        indent_left, indent_right, indent_fl, indent_hang,
        keep_next, keep_lines, page_break, align_raw,
        tbl_style, tbl_mar_top, tbl_mar_bot, tbl_mar_left, tbl_mar_right,
    ])
    if not has_any:
        return None

    return StyleRule(
        font_ascii=font_ascii,
        font_east_asia=font_ea,
        font_size=font_size,
        bold=bold,
        italic=italic,
        strike=strike,
        dstrike=dstrike,
        underline=underline_raw,
        small_caps=small_caps,
        all_caps=all_caps,
        vanish=vanish,
        color=color,
        highlight=highlight_raw,
        shading=shading,
        char_spacing=char_spacing,
        char_scale=char_scale_int,
        kern=kern_r,
        position=position_r,
        vertical_align=vert_align,
        line_spacing=line_spacing,
        space_before=space_before,
        space_after=space_after,
        indent_left=indent_left,
        indent_right=indent_right,
        indent_first_line=indent_fl,
        indent_hanging=indent_hang,
        keep_next=keep_next,
        keep_lines=keep_lines,
        page_break_before=page_break,
        alignment=align_raw,
        tbl_style_name=tbl_style,
        tbl_cell_margin_top=tbl_mar_top,
        tbl_cell_margin_bottom=tbl_mar_bot,
        tbl_cell_margin_left=tbl_mar_left,
        tbl_cell_margin_right=tbl_mar_right,
    )


def interactive_configure(styles_xml_bytes: bytes) -> "StyleAdjuster":
    """
    交互式向导：展示文档中的样式列表，引导用户逐一修改。
    返回配置好的 StyleAdjuster。
    """
    adjuster = StyleAdjuster()

    try:
        root = etree.fromstring(styles_xml_bytes)
    except Exception:
        print("[WARN] 无法解析 styles.xml，向导退出")
        return adjuster

    styles_info: list[tuple[str, str, str, str]] = []
    for style_elem in root.findall(_q("style")):
        stype = style_elem.get(_q("type"), "")
        sid = style_elem.get(_q("styleId"), "")
        name_elem = style_elem.find(_q("name"))
        name = name_elem.get(_q("val"), "") if name_elem is not None else ""
        rpr = style_elem.find(_q("rPr"))
        rf = rpr.find(_q("rFonts")) if rpr is not None else None
        sz = rpr.find(_q("sz")) if rpr is not None else None
        font_info = ""
        if rf is not None:
            ea = rf.get(_q("eastAsia")) or rf.get(_q("eastAsiaTheme"), "")
            asc = rf.get(_q("ascii")) or rf.get(_q("asciiTheme"), "")
            font_info = f"{asc}/{ea}".strip("/")
        sz_info = ""
        if sz is not None:
            v = sz.get(_q("val"))
            if v and v.isdigit():
                sz_info = f"{int(v)//2}pt"
        styles_info.append((stype, sid, name, f"{font_info} {sz_info}".strip()))

    print("\n" + "═" * 65)
    print("  样式调整向导")
    print("═" * 65)
    print("\n文档中的样式（仅显示段落/字符样式）：")
    para_styles = [(sid, name, info) for stype, sid, name, info in styles_info if stype in ("paragraph", "character")]
    for i, (sid, name, info) in enumerate(para_styles[:30]):
        print(f"  [{i:2d}] {name:<30s}  {info}")

    print("\n" + "─" * 65)
    do_defaults = input("\n是否修改文档默认字体 (docDefaults)？(y/n) [n]: ").strip().lower()
    if do_defaults in ("y", "yes"):
        rule = _collect_rule("文档默认字体（影响所有未显式指定字体的样式）")
        if rule:
            adjuster._defaults_rule = rule
            print(f"  ✓ docDefaults 已设置: {rule}")

    print("\n" + "─" * 65)
    print("\n逐一选择要修改的样式（输入编号或样式名，空行结束）：")
    while True:
        raw = input("\n  选择样式（编号/名称，空行结束）: ").strip()
        if not raw:
            break
        if raw.isdigit() and int(raw) < len(para_styles):
            style_name = para_styles[int(raw)][1]
        else:
            style_name = raw
        rule = _collect_rule(f"样式 {style_name!r}")
        if rule:
            adjuster._style_rules[style_name] = rule
            print(f"  ✓ 样式 {style_name!r} 已设置: {rule}")

    print("\n" + "═" * 65)
    print(adjuster.summary())
    print("═" * 65 + "\n")
    return adjuster


# ══════════════════════════════════════════════
# 快捷预设
# ══════════════════════════════════════════════

class Presets:

    @staticmethod
    def song_ti_12pt() -> StyleAdjuster:
        """全文宋体 12 磅（修改 Normal 和 docDefaults）"""
        a = StyleAdjuster()
        a.set_defaults(font_ascii="Times New Roman", font_east_asia="宋体", font_size=12)
        a.set_style("Normal", font_ascii="Times New Roman", font_east_asia="宋体", font_size=12)
        return a

    @staticmethod
    def fang_song_14pt() -> StyleAdjuster:
        """全文仿宋 14 磅"""
        a = StyleAdjuster()
        a.set_defaults(font_ascii="仿宋", font_east_asia="仿宋", font_size=14)
        a.set_style("Normal", font_ascii="仿宋", font_east_asia="仿宋", font_size=14)
        return a

    @staticmethod
    def heading_black(sizes: Optional[dict[int, float]] = None) -> StyleAdjuster:
        """标题改为黑体，可指定各级字号 {1: 18, 2: 16, 3: 14}"""
        sizes = sizes or {1: 18.0, 2: 16.0, 3: 14.0}
        a = StyleAdjuster()
        for level, size in sizes.items():
            a.set_style(f"heading {level}", font_east_asia="黑体", font_size=size, bold=True)
        return a


# ══════════════════════════════════════════════
# 与 style_main.py 的集成入口
# ══════════════════════════════════════════════

def extract_adjust_render(
    input_path: str,
    output_path: str,
    json_path: Optional[str] = None,
    adjuster: Optional[StyleAdjuster] = None,
    interactive: bool = False,
) -> None:
    from pathlib import Path as _Path
    from zipfile import ZipFile
    from style_extract import EnhancedExtractorV2
    from style_randering import DocxRendererV2
    from style_main import SegmentSerializerV2

    input_path = _Path(input_path)
    output_path = _Path(output_path)

    print(f"\n{'═' * 70}")
    print(f"  [DOCX] DOCX 精确还原引擎 V2 + 样式调整器")
    print(f"  [IN]   {input_path}")
    print(f"  [OUT]  {output_path}")
    print(f"{'═' * 70}")

    print("\n[1/4] 提取文本与计算样式...")
    extractor = EnhancedExtractorV2(input_path)
    segments = extractor.extract_all()

    src_count: dict[str, int] = {}
    for seg in segments:
        src_count[seg.source] = src_count.get(seg.source, 0) + 1
    print(f"   共 {len(segments)} 个片段: {src_count}")

    if interactive:
        with ZipFile(str(input_path)) as zf:
            styles_bytes = zf.read("word/styles.xml")
        adjuster = interactive_configure(styles_bytes)

    if json_path:
        print(f"\n[2/4] 保存中间数据...")
        SegmentSerializerV2.to_json(segments, json_path)

    print(f"\n[3/4] 渲染为新 DOCX...")
    renderer = DocxRendererV2(source_path=input_path)
    renderer.render(segments, output_path, adjuster=adjuster)

    print(f"\n{'═' * 70}")
    print(f"  [OK] 完成！输出: {output_path}")
    print(f"{'═' * 70}\n")


# ══════════════════════════════════════════════
# 直接运行入口
# ══════════════════════════════════════════════

if __name__ == "__main__":
    INPUT  = r"C:\Users\H\Desktop\word解析和还原\雅本化学2025ESG报告文字稿-20260409.docx"
    OUTPUT = r"C:\Users\H\Desktop\还原文档测试.docx"
    JSON   = r"C:\Users\H\Desktop\word解析和还原\data.json"

    print("选择运行模式：")
    print("  [1] 交互式向导（手动选择并修改每个样式）")
    print("  [2] 预设：全文宋体 12 磅")
    print("  [3] 预设：全文仿宋 14 磅")
    print("  [4] 预设：标题改为黑体")
    print("  [5] 不调整，直接还原（等同于 style_main.py）")
    choice = input("请选择 [1-5]: ").strip()

    if choice == "1":
        extract_adjust_render(INPUT, OUTPUT, JSON, interactive=True)
    elif choice == "2":
        extract_adjust_render(INPUT, OUTPUT, JSON, adjuster=Presets.song_ti_12pt())
    elif choice == "3":
        extract_adjust_render(INPUT, OUTPUT, JSON, adjuster=Presets.fang_song_14pt())
    elif choice == "4":
        extract_adjust_render(INPUT, OUTPUT, JSON, adjuster=Presets.heading_black())
    else:
        extract_adjust_render(INPUT, OUTPUT, JSON)
