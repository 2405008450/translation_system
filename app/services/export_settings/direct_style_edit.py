"""
direct_style_edit.py
直接修改原文档的样式定义，不解析、不重组文档结构。

与 main.extract_and_render 的区别：
  - extract_and_render：把文档拆成 TextSegment（提取样式），可选调整，
    再逐段重新生成一份新文档 —— 结构是"重建"出来的。
  - apply_style_in_place（本文件）：直接在原始 ZIP 条目上打补丁，
    图片 / 图表 / 关系文件等原样保留，不做任何拆解和重组。具体修改三类内容：
      1. word/styles.xml —— 具名样式的 w:rPr / w:pPr 定义（继承生效）。
      2. word/document.xml、word/header*.xml、word/footer*.xml 中的
         每个 <w:tbl> —— 直接重写表格属性、单元格段落对齐/行距、
         单元格文字格式等（不经过继承，直接改写 XML）。
      3. word/settings.xml —— 文档级设置，目前支持自动断字
         （对应 Word「布局→断字」菜单），通过 adjuster.set_hyphenation() 配置。

原理：
  Word 文档里，大多数文字/段落的格式都是"继承自具名样式"（Normal、heading 1...），
  而不是逐字符写死的。只要改 styles.xml 里对应样式的 w:rPr / w:pPr 定义，
  Word 打开时所有引用该样式的内容会自动应用新格式 —— 这跟你在 Word 界面里
  右键"修改样式"效果完全一致，风险极低，因为原文件其余部分一字节都没变。

  但表格单元格里的段落格式（对齐、行距等）几乎总是"直接格式"，不是继承
  自具名样式（很多表格甚至压根没有引用任何 tblStyle），所以光改 styles.xml
  对表格没用。因此表格相关的 StyleRule 字段（tbl_para_alignment、
  tbl_run_* 等）走另一条路径：直接在 document.xml 里找到每个 <w:tbl>
  元素，用 StyleAdjuster.apply_to_table_xml 原地重写它的 XML 再放回去，
  同样不涉及拆解重组段落结构，只是把该表格这一段 XML 换成改过的版本。

局限（继承机制决定，无法绕过）：
  正文（非表格）段落如果带有"直接格式"（用户在 Word 里手动设置过字体、
  字号、对齐等，覆盖了样式继承），这部分直接格式的优先级高于样式定义，
  改 styles.xml 不会影响它们。这与 Word 自身"修改样式"功能的行为一致。
  表格不受此限制，因为表格走的是直接重写 XML，会覆盖已有的直接格式。

使用方式：
    from direct_style_edit import apply_style_in_place
    from style_adjuster import StyleAdjuster

    adjuster = StyleAdjuster()
    # Normal/heading 等具名样式 —— 通过 styles.xml 继承生效
    adjuster.set_style("Normal", font_east_asia="宋体", font_size=12)
    adjuster.set_style("heading 1", font_size=16, bold=True)
    adjuster.set_defaults(font_ascii="Times New Roman")

    # 表格 —— 用 "Table Grid"（或 "表格网格"）这个 key 配置，
    # 会直接应用到文档里的每一个表格，不要求表格本身引用了这个样式名
    adjuster.set_style("Table Grid",
        tbl_para_alignment="center",   # 单元格段落居中
        tbl_run_font_size=10.5,
    )

    # 自动断字 —— 对应 Word「布局→断字→自动」，写入 word/settings.xml
    adjuster.set_hyphenation(auto=True, consecutive_limit=2, zone_pt=18)

    apply_style_in_place(INPUT, OUTPUT, adjuster)
"""
from __future__ import annotations
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from typing import Any
from lxml import etree

from style_adjuster import _q

STYLES_ENTRY = "word/styles.xml"
SETTINGS_ENTRY = "word/settings.xml"
_TBL_TAG = _q("tbl")


def _get_table_rule(adjuster: Any) -> Any:
    """
    与渲染器 (style_randering.py._render_table) 完全一致的取规则逻辑：
    优先用名为 "Table Grid" / "表格网格" 的规则，否则退回 defaults 规则。
    不要求表格本身实际引用了这个样式名 —— 规则会应用到文档里的每一个表格。
    """
    return (
        adjuster._style_rules.get("Table Grid")
        or adjuster._style_rules.get("表格网格")
        or adjuster._defaults_rule
    )


def _patch_tables_in_xml(xml_bytes: bytes, adjuster: Any) -> tuple[bytes, int]:
    """
    在一份 XML（document.xml / header*.xml / footer*.xml）中找到所有
    「最外层」<w:tbl> 元素（跳过嵌套表格，因为外层表格的 XML 里已经
    包含了嵌套表格，apply_to_table_xml 会递归处理其中的段落），
    对每个表格调用 apply_to_table_xml 原地重写，其余内容不动。

    返回 (可能被修改后的 xml bytes, 实际发生改动的表格数量)。
    """
    try:
        root = etree.fromstring(xml_bytes)
    except Exception:
        return xml_bytes, 0

    all_tbls = list(root.iter(_TBL_TAG))
    if not all_tbls:
        return xml_bytes, 0

    tbl_rule = _get_table_rule(adjuster)
    # 只取最外层表格：祖先节点里不含另一个 <w:tbl>
    top_level = [
        t for t in all_tbls
        if not any(anc.tag == _TBL_TAG for anc in t.iterancestors())
    ]

    changed_count = 0
    for tbl in top_level:
        old_xml = etree.tostring(tbl, encoding="unicode")
        new_xml = adjuster.apply_to_table_xml(old_xml, tbl_rule)
        if new_xml == old_xml:
            continue
        try:
            new_elem = etree.fromstring(new_xml.encode("utf-8"))
        except Exception:
            continue  # 解析失败就跳过这一个表格，不影响其余内容
        tbl.getparent().replace(tbl, new_elem)
        changed_count += 1

    if changed_count == 0:
        return xml_bytes, 0

    new_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return new_bytes, changed_count


def apply_style_in_place(
    input_path: str | Path,
    output_path: str | Path,
    adjuster: Any,
) -> None:
    """
    直接在原始 ZIP 条目上打补丁，不拆解、不重组文档结构：
      - word/styles.xml：整份替换为改过的版本（具名样式继承生效）。
      - word/document.xml、word/header*.xml、word/footer*.xml：
        只替换其中被 StyleRule 实际改动过的 <w:tbl> 元素，其余 XML 原样保留。
      - word/settings.xml：写入 adjuster.set_hyphenation() 配置的自动断字设置
        （若未调用该方法则不改动这个文件）。
      - 其余所有 ZIP 条目（图片、图表、关系文件等）按原始字节 1:1 复制。

    input_path:  原始 docx 路径
    output_path: 输出 docx 路径（可与输入不同，避免覆盖原文件）
    adjuster:    StyleAdjuster 实例，需实现 apply_to_styles_xml / apply_to_table_xml
                 （apply_to_settings_xml 可选，没有就跳过断字设置这一步）
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    if adjuster is None or not hasattr(adjuster, "apply_to_styles_xml"):
        raise ValueError("adjuster 必须是 StyleAdjuster 实例（缺少 apply_to_styles_xml 方法）")

    print(f"\n{'=' * 70}")
    print(f"  [DIRECT] 直接修改原文样式（不拆解、不重组文档）")
    print(f"  [IN]  输入: {input_path}")
    print(f"  [OUT] 输出: {output_path}")
    print(f"{'=' * 70}")

    with ZipFile(str(input_path), "r") as src_zip:
        namelist = src_zip.namelist()
        if STYLES_ENTRY not in namelist:
            raise ValueError(f"源文档缺少 {STYLES_ENTRY}，无法应用样式规则")

        patched_entries: dict[str, bytes] = {}

        # ── 1. styles.xml：具名样式定义，靠继承生效 ──
        original_styles = src_zip.read(STYLES_ENTRY)
        new_styles = adjuster.apply_to_styles_xml(original_styles)
        styles_changed = new_styles != original_styles
        if styles_changed:
            patched_entries[STYLES_ENTRY] = new_styles

        # ── 2. document.xml / 页眉 / 页脚：直接重写其中的 <w:tbl> ──
        table_targets = [
            name for name in namelist
            if name == "word/document.xml"
            or (name.startswith("word/header") and name.endswith(".xml"))
            or (name.startswith("word/footer") and name.endswith(".xml"))
        ]
        total_tables_changed = 0
        for name in table_targets:
            original = src_zip.read(name)
            patched, count = _patch_tables_in_xml(original, adjuster)
            if count > 0:
                patched_entries[name] = patched
                total_tables_changed += count
                print(f"  [TABLE] {name}: 改动了 {count} 个表格")

        # ── 3. settings.xml：自动断字等文档级设置 ──
        settings_changed = False
        if SETTINGS_ENTRY in namelist and hasattr(adjuster, "apply_to_settings_xml"):
            original_settings = src_zip.read(SETTINGS_ENTRY)
            new_settings = adjuster.apply_to_settings_xml(original_settings)
            settings_changed = new_settings != original_settings
            if settings_changed:
                patched_entries[SETTINGS_ENTRY] = new_settings
                print(f"  [SETTINGS] {SETTINGS_ENTRY}: 已写入断字设置")

        with ZipFile(str(output_path), "w", ZIP_DEFLATED) as dst_zip:
            for item in src_zip.infolist():
                data = patched_entries.get(item.filename, src_zip.read(item.filename))
                dst_zip.writestr(item, data)

    if styles_changed or total_tables_changed or settings_changed:
        print(f"  [OK] 完成。styles.xml 改动: {styles_changed}；"
              f"表格改动: {total_tables_changed} 个；settings.xml 改动: {settings_changed}")
        print(f"       其余内容与原文 100% 一致\n")
    else:
        print(f"  [WARN] 未检测到任何样式改动（adjuster 未设置规则，或规则未匹配到样式名）\n")

if __name__ == '__main__':
    # ══════════════════════════════════════════════════════════════════
    # 如何运行本文件
    # ══════════════════════════════════════════════════════════════════
    # 直接在终端执行（不需要额外参数，所有配置都在下面写好了）：
    #     python direct_style_edit.py
    #
    # 运行流程：
    #   1. 打开 INPUT 指向的原始 docx（不会被修改，只读）
    #   2. 按下面 adjuster.set_style(...) / set_defaults(...) 里配置的规则，
    #      在内存里对 styles.xml 和每个表格的 XML 做修改
    #   3. 把改动写入 OUTPUT 指向的新文件，其余内容（图片/图表/关系文件等）
    #      原样复制，不会重新排版、不会丢失任何未涉及的格式
    #
    # 如果只想改别的文档，把下面 INPUT / OUTPUT 两个路径换掉即可；
    # 如果只想调整某几个样式，把对应的 adjuster.set_style(...) 调用里的
    # 参数改掉，不需要的样式段落可以整段注释掉（不会报错，未配置的样式
    # 保持原文不变）。
    # ══════════════════════════════════════════════════════════════════
    from style_adjuster import StyleAdjuster

    INPUT = r"C:\Users\H\Desktop\word解析和还原\parser_style\译文-含不可编辑_01 (2026-007)2025年年度报告(1).docx"
    OUTPUT = r"C:\Users\H\Desktop\word解析和还原\parser_style\译文-含不可编辑_01 (2026-007)2025年年度报告(1)-样式还原.docx"
    adjuster = StyleAdjuster()
    # ══════════════════════════════════════════════════════════════════
    # 文档默认字体（影响所有未显式指定字体的样式）
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_defaults(
        font_ascii="Times New Roman",  # 西文/数字字体
        font_east_asia="Times New Roman",  # 东亚/中文字体
        font_hAnsi="Times New Roman",  # 高位字符，通常与 font_ascii 一致
        font_cs="Times New Roman",  # 复杂文种，通常与中文字体一致
        font_size=10.5,  # 10.5磅=五号, 12.0=小四, 14.0=四号
        font_size_cs=10.5,
    )

    # ══════════════════════════════════════════════════════════════════
    # 正文 Normal —— 所有段落的基础样式，其他样式均继承自此
    # 修改此处会影响全文未单独设置样式的段落
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_style("Normal",
                       # ── 字体 ──
                       font_ascii="Times New Roman",
                       font_east_asia="Times New Roman",
                       font_size=10.5,

                       # ── 字重/字形 ──
                       bold=False,
                       italic=False,
                       strike=False,  # 单删除线
                       dstrike=False,  # 双删除线
                       underline="none",  # "single"(单线)/"double"(双线)/"dotted"(点线)/"dash"(虚线)/"none"
                       small_caps=False,  # 小型大写字母
                       all_caps=False,  # 全部大写
                       vanish=False,  # 隐藏文字

                       # ── 颜色/底纹 ──
                       color=None,  # hex不带#，如 "FF0000"(红) "0000FF"(蓝) "444444"(深灰)
                       highlight=None,  # "yellow"/"green"/"cyan"/"red"/"magenta"/"blue"/"darkBlue" 等
                       shading=None,  # 字符底纹 hex，如 "D9D9D9"(浅灰)

                       # ── 字符间距 ──
                       char_spacing=0.0,  # 磅值，正=加宽，负=紧缩
                       char_scale=100,  # 缩放百分比，100=正常，80=扁，150=宽
                       kern=0.0,  # 字距调整起始字号（磅），0=关闭
                       position=0.0,  # 字符升降（磅），正=上升，负=下降
                       # vertical_align="baseline",  # "superscript"(上标)/"subscript"(下标)/"baseline"

                       # ── 段落布局 ──
                       alignment="both",  # "left"/"center"/"right"/"both"(两端对齐)/"distribute"(分散)
                       line_spacing=1.5,
                       line_spacing_rule="auto",
                       space_before=0.0,  # 段前间距（磅）
                       space_after=0.0,  # 段后间距（磅）
                       indent_left=0.0,  # 左缩进（磅）
                       indent_right=0.0,  # 右缩进（磅）
                       indent_first_line=21.0,  # 首行缩进（磅）。21磅≈五号字2字符，24磅≈小四字2字符
                       indent_hanging=0.0,  # 悬挂缩进（磅），与 indent_first_line 互斥

                       # ── 换行与分页 ──
                       keep_next=False,  # 与下段同页
                       keep_lines=False,  # 段落内不分页
                       page_break_before=False,  # 段前分页
                       )

    # ══════════════════════════════════════════════════════════════════
    # 标题层级 heading 1 ~ heading 9
    # 说明：heading 1 对应 Word「标题1」，以此类推
    #       keep_next=True 防止标题孤立在页面底部，标题必须设置
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_style("heading 1",
                       font_east_asia="黑体",
                       font_size=16.0,  # 三号≈16磅
                       bold=True,
                       #color="000000",  # Word默认蓝色标题2E74B5，改"000000"=黑色
                       alignment="center",
                       line_spacing=1.5,
                       line_spacing_rule="auto",
                       space_before=12.0,
                       space_after=6.0,
                       keep_next=True,
                       indent_first_line=0.0,  # 标题通常不缩进
                       )

    adjuster.set_style("heading 2",
                       font_east_asia="黑体",
                       font_size=14.0,  # 四号≈14磅
                       bold=True,
                       #color="2E74B5",
                       alignment="left",
                       line_spacing=1.5,
                       line_spacing_rule="auto",
                       space_before=6.0,
                       space_after=3.0,
                       keep_next=True,
                       indent_first_line=0.0,
                       )

    adjuster.set_style("heading 3",
                       font_east_asia="黑体",
                       font_size=12.0,
                       bold=True,
                       #color="2E74B5",
                       alignment="left",
                       line_spacing=1.5,
                       line_spacing_rule="auto",
                       space_before=6.0,
                       space_after=3.0,
                       keep_next=True,
                       indent_first_line=0.0,
                       )

    # heading 4~9 通常用于深层嵌套，按需取消注释
    # adjuster.set_style("heading 4", font_size=12.0, bold=True, italic=True, keep_next=True, indent_first_line=0.0)
    # adjuster.set_style("heading 5", font_size=10.5, bold=True, keep_next=True, indent_first_line=0.0)
    # adjuster.set_style("heading 6", font_size=10.5, italic=True, keep_next=True, indent_first_line=0.0)
    # adjuster.set_style("heading 7", font_size=10.5, keep_next=True, indent_first_line=0.0)
    # adjuster.set_style("heading 8", font_size=10.5, keep_next=True, indent_first_line=0.0)
    # adjuster.set_style("heading 9", font_size=10.5, keep_next=True, indent_first_line=0.0)

    # ══════════════════════════════════════════════════════════════════
    # 目录 toc 1 ~ toc 3
    # 说明：对应 Word 自动生成目录的各级条目
    #       toc 1=一级目录，toc 2=二级，toc 3=三级
    #       indent_left 控制各级缩进量，形成层次感
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_style("toc 1",
                       font_east_asia="Times New Roman",
                       font_size=10.5,
                       bold=False,
                       alignment="left",
                       line_spacing=1.5,
                       line_spacing_rule="auto",
                       space_before=0.0,
                       space_after=0.0,
                       indent_left=0.0,  # 一级目录不缩进
                       indent_first_line=0.0,
                       )

    adjuster.set_style("toc 2",
                       font_east_asia="Times New Roman",
                       font_size=10.5,
                       bold=False,
                       alignment="left",
                       line_spacing=1.5,
                       line_spacing_rule="auto",
                       space_before=0.0,
                       space_after=0.0,
                       indent_left=14.0,  # 二级目录向右缩进约一个字符
                       indent_first_line=0.0,
                       )

    adjuster.set_style("toc 3",
                       font_east_asia="Times New Roman",
                       font_size=10.5,
                       bold=False,
                       alignment="left",
                       line_spacing=1.5,
                       line_spacing_rule="auto",
                       space_before=0.0,
                       space_after=0.0,
                       indent_left=28.0,  # 三级目录再缩进
                       indent_first_line=0.0,
                       )

    # ══════════════════════════════════════════════════════════════════
    # 页眉 Header / 页脚 Footer
    # 说明：页眉页脚的文字格式，alignment 控制左/中/右位置
    #       页码通常在页脚中，样式由 "page number" 控制
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_style("Header",
                       font_east_asia="Times New Roman",
                       font_size=9.0,
                       color=None,
                       alignment="center",  # "left"=左对齐/"center"=居中/"right"=右对齐
                       line_spacing=1.0,
                       line_spacing_rule="auto",
                       space_before=0.0,
                       space_after=0.0,
                       indent_first_line=0.0,
                       )

    adjuster.set_style("Footer",
                       font_east_asia="Times New Roman",
                       font_size=9.0,
                       color=None,
                       alignment="center",
                       line_spacing=1.0,
                       line_spacing_rule="auto",
                       space_before=0.0,
                       space_after=0.0,
                       indent_first_line=0.0,
                       )

    # 页码样式（页脚中的页码数字）
    # adjuster.set_style("page number",
    #     font_ascii="Times New Roman",
    #     font_size=9.0,
    #     alignment="center",
    # )

    # ══════════════════════════════════════════════════════════════════
    # 题注 caption —— 图表标题，如「图1-1 示意图」「表2-1 数据表」
    # 说明：通常居中，字号略小于正文，可加粗或用特定颜色区分
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_style("caption",
                       font_east_asia="Times New Roman",
                       font_size=9.0,
                       bold=False,
                       color=None,  # 可设 "595959" 灰色区分题注
                       alignment="center",
                       line_spacing=1.5,
                       line_spacing_rule="auto",
                       space_before=3.0,
                       space_after=3.0,
                       indent_first_line=0.0,
                       )

    # ══════════════════════════════════════════════════════════════════
    # 脚注 footnote text —— 页面底部的脚注正文
    # 脚注引用 footnote reference —— 正文中的上标数字/符号
    # 说明：脚注字号通常比正文小1~2磅，行距紧凑
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_style("footnote text",
                       font_east_asia="Times New Roman",
                       font_size=9.0,
                       alignment="left",
                       line_spacing=1.0,
                       line_spacing_rule="auto",
                       space_before=0.0,
                       space_after=0.0,
                       indent_first_line=0.0,
                       )

    # 脚注引用（正文中的上标标记，如 ¹ ² ³）
    # adjuster.set_style("footnote reference",
    #     font_size=7.0,
    #     vertical_align="superscript",  # 上标
    # )

    # ══════════════════════════════════════════════════════════════════
    # 尾注 endnote text / endnote reference
    # 说明：尾注出现在文档末尾，用法与脚注相同
    # ══════════════════════════════════════════════════════════════════
    # adjuster.set_style("endnote text",
    #     font_east_asia="宋体",
    #     font_size=9.0,
    #     alignment="left",
    #     line_spacing=1.0,
    #     line_spacing_rule="auto",
    #     indent_first_line=0.0,
    # )
    # adjuster.set_style("endnote reference",
    #     font_size=7.0,
    #     vertical_align="superscript",
    # )

    # ══════════════════════════════════════════════════════════════════
    # 超链接 Hyperlink
    # 说明：控制文档中所有超链接文字的外观
    #       默认 Word 蓝色+下划线，可改为与正文一致避免视觉干扰
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_style("Hyperlink",
                       color="0563C1",  # 标准超链接蓝色；改 "000000" 变黑色
                       underline="single",  # "single"=有下划线；"none"=去掉下划线
                       )

    # ══════════════════════════════════════════════════════════════════
    # 列表段落 List Paragraph —— 项目符号/编号列表的段落
    # 说明：indent_left 控制列表整体缩进，indent_hanging 控制悬挂缩进
    #       通常不设 indent_first_line（由列表编号自动控制）
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_style("List Paragraph",
                       font_east_asia="Times New Roman",
                       font_size=10.5,
                       alignment="left",
                       line_spacing=1.5,
                       line_spacing_rule="auto",
                       space_before=0.0,
                       space_after=0.0,
                       indent_left=21.0,  # 列表左缩进，与正文首行缩进对齐
                       indent_first_line=0.0,
                       )

    # ══════════════════════════════════════════════════════════════════
    # 引用类 Quote / Intense Quote
    # 说明：Block Text 是块引用（整段缩进），
    #       Intense Quote 是强调引用（通常有颜色/边框）
    # ══════════════════════════════════════════════════════════════════
    # adjuster.set_style("Block Text",
    #     font_east_asia="宋体",
    #     font_size=10.5,
    #     italic=True,
    #     alignment="both",
    #     indent_left=28.0,     # 左右缩进形成引用块效果
    #     indent_right=28.0,
    #     space_before=6.0,
    #     space_after=6.0,
    #     indent_first_line=0.0,
    # )
    # adjuster.set_style("Intense Quote",
    #     font_east_asia="宋体",
    #     font_size=10.5,
    #     italic=True,
    #     color="2E74B5",
    #     alignment="center",
    #     indent_first_line=0.0,
    # )

    # ══════════════════════════════════════════════════════════════════
    # 强调类 Emphasis / Strong / Intense Emphasis / Subtle Emphasis
    # 说明：这些是字符样式（inline），只影响选中文字，不影响段落布局
    #       通常只需设置字体颜色/加粗/斜体
    # ══════════════════════════════════════════════════════════════════
    # adjuster.set_style("Emphasis",
    #     italic=True,
    # )
    # adjuster.set_style("Strong",
    #     bold=True,
    # )
    # adjuster.set_style("Intense Emphasis",
    #     italic=True,
    #     color="2E74B5",
    # )
    # adjuster.set_style("Subtle Emphasis",
    #     italic=True,
    #     color="595959",
    # )

    # ══════════════════════════════════════════════════════════════════
    # 表格样式 Table Grid
    # 说明：tbl_para_* 控制单元格内段落格式
    #       tbl_run_*  控制单元格内文字格式
    #       tbl_cell_margin_* 控制单元格内边距（磅）
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_style("Table Grid",
                       # ── 表格整体 ──
                       tbl_style_name="Table Grid",
                       # 对应 Word「表格属性→表格→自动调整」三选项：
                       #   "autofit_content" = 根据内容自动调整表格
                       #   "autofit_window"  = 根据窗口自动调整表格（配合 tbl_width_pct）
                       #   "fixed"           = 固定列宽
                       tbl_layout_type="autofit_window",
                       tbl_width_pct=100,  # 仅 autofit_window 时生效，如 100=占满页面宽度
                       tbl_align="left",  # 表格对齐 "left"/"center"/"right"
                       tbl_indent=None,  # 表格左缩进（磅）
                       tbl_border_style="single",  # 边框样式 "single"/"none"/"double"/"thick" 等
                       tbl_border_size=None,  # 边框粗细（1/8磅单位），4=0.5pt，8=1pt，16=2pt
                       tbl_border_color=None,  # 边框颜色 hex，如 "000000"(黑) "BFBFBF"(灰)
                       # ── 单元格内边距 ──
                       tbl_cell_margin_top=0.0,  # 上边距（磅）
                       tbl_cell_margin_bottom=0.0,  # 下边距（磅）
                       tbl_cell_margin_left=5.4,  # 左边距（磅），Word默认约5.4磅
                       tbl_cell_margin_right=5.4,  # 右边距（磅）
                       # ── 单元格段落格式 ──
                       tbl_para_line_spacing=1.0,  # 行间距：auto时填倍数，exact/atLeast时填磅值
                       tbl_para_line_spacing_rule="auto",  # "auto"/"exact"/"atLeast"
                       tbl_para_space_before=0.0,  # 段前间距（磅）
                       tbl_para_space_after=0.0,  # 段后间距（磅）
                       tbl_para_alignment="center",  # "left"/"center"/"right"/"both"
                       # ── 单元格文字格式 ──
                       tbl_run_font_ascii="Times New Roman",
                       tbl_run_font_east_asia="Times New Roman",
                       tbl_run_font_size=10.5,
                       tbl_run_bold=False,
                       tbl_run_color=None,  # hex不带#，如 "000000"
                       tbl_run_char_spacing=None,  # 字符间距（磅）
                       # ── 单元格文字方向 ──
                       tbl_cell_text_direction=None,  # None=不修改（保持原文）
                       # "lrTb"  = 水平方向（默认，正常横排）
                       # "tbRl"  = 垂直方向，从右往左
                       # "btLr"  = 垂直方向，从左往右
                       # "lrTbV" = 所有文字顺时针旋转90°
                       # "tbRlV" = 所有文字逆时针旋转90°
                       # "tbLrV" = 中文字符逆时针旋转90°
                       )

    # ══════════════════════════════════════════════════════════════════
    # 自动断字 —— 对应 Word「布局→断字」菜单，写入 word/settings.xml
    # 说明：这是文档级设置，不是某个具名样式的属性，所以单独用
    #       set_hyphenation() 配置，不放在 set_style() 里。
    # 参数（全部可选，不传 = 不修改，保持原文档设置）：
    #   auto                  True="自动"/False="无"，对应菜单的两个选项
    #   consecutive_limit     最大连续断字行数，对应"断字选项→连续断字符限制"
    #   zone_pt               断字区宽度（磅），对应"断字选项→断字区"，
    #                         值越小断字越积极，Word 默认 18 磅（0.25 英寸）
    #   do_not_hyphenate_caps True=全大写单词不参与断字
    # 不需要断字功能时，把下面这行整行注释掉即可，其余流程不受影响
    # ══════════════════════════════════════════════════════════════════
    adjuster.set_hyphenation(
        auto=True,               # 开启自动断字
        consecutive_limit=2,     # 最多连续 2 行以连字符结尾
        zone_pt=18,               # 断字区 18 磅（Word 默认值）
        do_not_hyphenate_caps=True,  # 全大写单词不断字
    )

    # ══════════════════════════════════════════════════════════════════
    # 真正执行：调用本文件顶部定义的 apply_style_in_place
    # 参数含义：
    #   INPUT    源文档（只读，不会被修改）
    #   OUTPUT   输出文档（新文件，可以和 INPUT 同名同目录也可以不同，
    #            但不建议直接覆盖 INPUT，方便改错了可以重新跑）
    #   adjuster 上面配置好的 StyleAdjuster 实例
    #
    # 执行后会在终端打印：
    #   - styles.xml 是否被改动
    #   - document.xml / 页眉 / 页脚里各改了多少个表格
    #   - 最终输出文件的完整路径
    # ══════════════════════════════════════════════════════════════════
    apply_style_in_place(INPUT, OUTPUT, adjuster)