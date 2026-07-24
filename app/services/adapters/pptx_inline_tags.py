"""PPTX 内联格式标签引擎。

用途：在翻译 PPTX 段落时保留 run 级格式（加粗/颜色/字号等）。

设计要点：
- 确定性：标签的生成、校验、run 重建全部是纯代码逻辑，零随机性。
- 最小化标签：段落里占比最大的样式作为“基准样式”，只有“异类 run”才打标签。
- 兜底降级：只要译文里的标签数量/配对/ID 有任何异常，rebuild 直接返回失败，
  由调用方回退到“把整段译文写进第一个 run（统一样式）”的原有行为。

标签形态使用罕见的数学白括号 ``⟦id⟧ ... ⟦/id⟧``，LLM 基本不会去翻译或改写；
``sanitize_tagged_text`` 负责修复模型可能插入的多余空格。
"""

from __future__ import annotations

import copy
import html as _html
import re
from collections import defaultdict
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from app.services.adapters.pptx_adapter import A_NS

_A = f"{{{A_NS}}}"
XML_SPACE_ATTR = "{http://www.w3.org/XML/1998/namespace}space"

# 标签形态：⟦1⟧内容⟦/1⟧
_TAG_OPEN = "⟦{}⟧"
_TAG_CLOSE = "⟦/{}⟧"
# 容忍模型在括号内塞入空格：⟦ 1 ⟧ / ⟦ / 1 ⟧
_MARKER_RE = re.compile(r"⟦\s*(/?)\s*(\d+)\s*⟧")


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def sanitize_tagged_text(text: str) -> str:
    """清洗译文里的标签：折叠括号内多余空格，统一为规范形态。"""
    if not text:
        return text

    def _fix(match: re.Match) -> str:
        template = _TAG_CLOSE if match.group(1) else _TAG_OPEN
        return template.format(match.group(2))

    return _MARKER_RE.sub(_fix, text)


def strip_format_tags(text: str) -> str:
    """移除文本中的全部行内格式标签，返回纯文本。

    用于降级/兜底路径：一旦不做 run 重建，必须把标记从可见文本中清掉，
    避免 ``⟦1⟧`` 之类的标记泄漏到最终 PPTX 里。
    """
    if not text:
        return text
    return _MARKER_RE.sub("", text)


def slice_tagged_paragraph(
    tagged_text: str, sentence_bounds: list[tuple[int, int]]
) -> list[str] | None:
    """把整段带标签文本按句子边界切成逐句的带标签片段。

    ``sentence_bounds`` 是相对“纯文本”（即 ``strip_format_tags(tagged_text)``）的
    连续升序 ``(start, end)`` 偏移，需完整覆盖 ``[0, len(plain))``。返回与
    ``sentence_bounds`` 等长的逐句带标签片段列表。

    生产端注入用途：整段样式由 :func:`build_tagged_paragraph` 编码，但句段是按句
    存储的，需把标签按句拆开。任何一对标签跨越句子边界都会返回 ``None``，调用方据
    此放弃注入、回退到无标签纯文本（保证零倒退，且导出端重建仍走兜底）。
    """
    if not sentence_bounds:
        return None

    total = sentence_bounds[-1][1]

    def bucket_of(pos: int) -> int:
        for index, (start, end) in enumerate(sentence_bounds):
            if start <= pos < end:
                return index
        # 末尾位置（pos == total）归入最后一句
        return len(sentence_bounds) - 1

    buckets: list[list[str]] = [[] for _ in sentence_bounds]
    open_bucket: dict[int, int] = {}
    plain_index = 0
    cursor = 0

    for match in _MARKER_RE.finditer(tagged_text):
        for char in tagged_text[cursor:match.start()]:
            buckets[bucket_of(plain_index)].append(char)
            plain_index += 1
        cursor = match.end()

        is_close = bool(match.group(1))
        marker_id = int(match.group(2))
        template = _TAG_CLOSE if is_close else _TAG_OPEN
        if is_close:
            # 闭合标签跟随前一个普通字符所在句子
            target = bucket_of(max(plain_index - 1, 0))
            if open_bucket.pop(marker_id, None) != target:
                return None
        else:
            # 开启标签跟随后一个普通字符所在句子
            target = bucket_of(min(plain_index, max(total - 1, 0)))
            open_bucket[marker_id] = target
        buckets[target].append(template.format(marker_id))

    for char in tagged_text[cursor:]:
        buckets[bucket_of(plain_index)].append(char)
        plain_index += 1

    if open_bucket:
        return None

    return ["".join(bucket).strip() for bucket in buckets]


def _rpr_style_css(rpr: ET.Element) -> str:
    """把 run 属性 ``<a:rPr>`` 汇总为一条内联 CSS（粗/斜/下划线/删除线/颜色/字号/字体族）。

    统一用内联样式而非 ``<b>/<u>`` 标签：前端渲染译文时用它包裹样式 span，序列化会
    忽略 style span（只保留文本与 ⟦n⟧ 标记），因此不会污染 target_html。工作台的原文
    列同样能识别（getStyleFormatTags 会把 font-weight 等映射回基础格式）。
    """
    styles: list[str] = []

    if rpr.get("b") == "1":
        styles.append("font-weight:bold")
    if rpr.get("i") == "1":
        styles.append("font-style:italic")

    decorations: list[str] = []
    underline = rpr.get("u")
    if underline and underline != "none":
        decorations.append("underline")
    strike = rpr.get("strike")
    if strike and strike != "noStrike":
        decorations.append("line-through")
    if decorations:
        styles.append("text-decoration:" + " ".join(decorations))

    color_element = rpr.find(f"{_A}solidFill/{_A}srgbClr")
    color = color_element.get("val") if color_element is not None else None
    if color:
        styles.append(f"color:#{color}")

    size = rpr.get("sz")
    if size and size.isdigit():
        # PPTX 字号单位是 1/100 磅
        points = int(size) / 100
        styles.append(f"font-size:{points:g}pt")

    latin = rpr.find(f"{_A}latin")
    typeface = latin.get("typeface") if latin is not None else None
    if typeface:
        styles.append(f"font-family:'{typeface}'")

    return ";".join(styles)


def _rpr_format_tokens(rpr: ET.Element | None) -> tuple[str, str]:
    """把 run 属性 ``<a:rPr>`` 映射为 (开标签, 闭标签)：单个内联样式 span。"""
    if rpr is None:
        return ("", "")
    css = _rpr_style_css(rpr)
    if not css:
        return ("", "")
    return (f'<span style="{css}">', "</span>")


def build_paragraph_format_map(tagged: TaggedParagraph) -> dict[str, list[str]]:
    """由标签化段落导出 ``{标签 id / "base": [开标签, 闭标签]}`` 的可 JSON 序列化格式表。"""
    open_tokens, close_tokens = _rpr_format_tokens(tagged.base_rpr)
    format_map: dict[str, list[str]] = {"base": [open_tokens, close_tokens]}
    for tag_id, rpr in tagged.id_to_rpr.items():
        format_map[str(tag_id)] = list(_rpr_format_tokens(rpr))
    return format_map


def tagged_fragment_to_html(fragment: str, format_map: dict[str, list[str]]) -> str:
    """把逐句带标签片段渲染为带基础格式的原文 HTML。

    ``format_map`` 来自 :func:`build_paragraph_format_map`；未打标签文本用 ``base``
    样式，标签内文本用对应 id 的样式，文本本身做 HTML 转义。
    """
    base_open, base_close = format_map.get("base", ["", ""])
    parts: list[str] = []
    cursor = 0
    current_id: str | None = None

    def emit(text: str, tag_id: str | None) -> None:
        if not text:
            return
        if tag_id is None:
            open_tag, close_tag = base_open, base_close
        else:
            open_tag, close_tag = format_map.get(tag_id, ["", ""])
        parts.append(f"{open_tag}{_html.escape(text)}{close_tag}")

    for match in _MARKER_RE.finditer(fragment):
        emit(fragment[cursor:match.start()], current_id)
        cursor = match.end()
        current_id = None if match.group(1) else match.group(2)
    emit(fragment[cursor:], None)
    return "".join(parts)


# run 属性里与“视觉样式”无关的噪声属性：仅影响拼写/语言/校对标记，不影响外观。
# 归一化时剔除它们，使视觉一致但 lang/dirty 等不同的相邻 run 能合并为同一个标签。
_NON_VISUAL_RPR_ATTRS = ("lang", "altLang", "dirty", "err", "noProof", "smtClean", "smtId")


def _visual_rpr_key(rpr: ET.Element | None) -> str:
    """把 ``<a:rPr>`` 归一化为“视觉样式” key：剥掉非视觉属性后序列化。

    这样相邻且外观一致的 run（例如仅 lang 不同的中英混排数字）会被合并成一个标签，
    任何真实格式差异（粗斜下划删/颜色/字号/字体等）仍会分到不同 key。
    """
    if rpr is None:
        return ""
    clone = copy.deepcopy(rpr)
    for attr in _NON_VISUAL_RPR_ATTRS:
        clone.attrib.pop(attr, None)
    return ET.tostring(clone, encoding="unicode")


@dataclass
class TaggedParagraph:
    """一个段落的标签化结果。"""

    tagged_text: str
    # 标签 id -> 对应 run 的 rPr 元素（已 deepcopy，脱离原段落）
    id_to_rpr: dict[int, ET.Element | None] = field(default_factory=dict)
    # 基准样式（未打标签文本使用），已 deepcopy
    base_rpr: ET.Element | None = None

    @property
    def has_tags(self) -> bool:
        return bool(self.id_to_rpr)


def build_tagged_paragraph(paragraph: ET.Element) -> TaggedParagraph | None:
    """把一个 ``<a:p>`` 段落编码为带标签的文本。

    仅处理“纯 run 序列”的段落；一旦遇到 ``<a:br>``、``<a:fld>`` 等无法安全重建
    的结构，返回 ``None``，交给调用方走原有回填逻辑（保证不产生倒退）。
    """
    runs: list[ET.Element] = []
    for child in list(paragraph):
        name = _local_name(child)
        if name in ("pPr", "endParaRPr"):
            continue
        if name != "r":
            # 含 br / fld 等结构，放弃标签化以免破坏段落
            return None
        runs.append(child)

    if not runs:
        return None

    # (文本, rPr 元素, rPr 规范化字符串作为比较键)
    segments: list[tuple[str, ET.Element | None, str]] = []
    for run in runs:
        t_element = run.find(f"{_A}t")
        rpr = run.find(f"{_A}rPr")
        text = t_element.text if (t_element is not None and t_element.text) else ""
        key = ET.tostring(rpr, encoding="unicode") if rpr is not None else ""
        segments.append((text, rpr, key))

    # 选“文本量最大”的样式作为基准，标签数量最少
    weight: dict[str, int] = defaultdict(int)
    representative: dict[str, ET.Element | None] = {}
    for text, rpr, key in segments:
        weight[key] += len(text)
        representative.setdefault(key, rpr)
    base_key = max(weight, key=lambda k: weight[k])

    def effective_key(text: str, key: str) -> str:
        # 纯空白 run（含段落首尾的换行/空格）不单独打标签，并入基准样式：
        # 否则首尾空白被包在标签内，段落 strip 无法去除，会导致与纯文本对齐失败，
        # 且会给译文引入永远不会出现的空白标签，使导出端 run 重建整体降级。
        return base_key if not text.strip() else key

    # 合并相邻同样式 run，减少标签数量（保留该 span 的代表 rPr）
    spans: list[tuple[str, str, ET.Element | None]] = []
    for text, rpr, key in segments:
        eff_key = effective_key(text, key)
        if spans and spans[-1][0] == eff_key:
            spans[-1] = (eff_key, spans[-1][1] + text, spans[-1][2])
        else:
            spans.append((eff_key, text, rpr))

    # 每个非基准 span 分配唯一 id（即使样式相同也不复用），保证每个标签在整段里恰好
    # 出现一次——否则同一样式在不相邻处重复出现会产生重复 id，导出端 _partition_by_tags
    # 因“id 只能出现一次”判定失败而整体降级，run 级格式无法还原。
    id_to_rpr: dict[int, ET.Element | None] = {}
    parts: list[str] = []
    next_id = 1
    for eff_key, text, rpr in spans:
        if eff_key == base_key:
            parts.append(text)
        else:
            tag_id = next_id
            next_id += 1
            id_to_rpr[tag_id] = copy.deepcopy(rpr) if rpr is not None else None
            parts.append(f"{_TAG_OPEN.format(tag_id)}{text}{_TAG_CLOSE.format(tag_id)}")

    tagged_text = "".join(parts).strip()
    base_rpr = representative[base_key]
    return TaggedParagraph(
        tagged_text=tagged_text,
        id_to_rpr=id_to_rpr,
        base_rpr=copy.deepcopy(base_rpr) if base_rpr is not None else None,
    )


def _partition_by_tags(
    text: str, valid_ids: set[int]
) -> list[tuple[int | None, str]] | None:
    """按标签把译文切成 ``(tag_id 或 None, 文本)`` 片段。

    严格校验（任一不满足即返回 ``None``，触发降级）：
    - 标签必须成对、扁平（open 后紧跟同 id 的 close，不允许嵌套/交叉）；
    - 每个 id 恰好出现一次；
    - 不允许未知 id、重复 id、未闭合标签。
    """
    result: list[tuple[int | None, str]] = []
    cursor = 0
    expected_close: int | None = None
    seen_ids: set[int] = set()

    for match in _MARKER_RE.finditer(text):
        is_close = bool(match.group(1))
        marker_id = int(match.group(2))
        preceding = text[cursor:match.start()]
        cursor = match.end()

        if expected_close is None:
            if is_close or marker_id not in valid_ids or marker_id in seen_ids:
                return None
            if preceding:
                result.append((None, preceding))
            expected_close = marker_id
        else:
            if not is_close or marker_id != expected_close:
                return None
            result.append((expected_close, preceding))
            seen_ids.add(expected_close)
            expected_close = None

    if expected_close is not None:
        return None
    if seen_ids != valid_ids:
        return None
    if cursor < len(text):
        result.append((None, text[cursor:]))
    return result


def _make_run(rpr: ET.Element | None, text: str) -> ET.Element:
    run = ET.Element(f"{_A}r")
    if rpr is not None:
        run.append(copy.deepcopy(rpr))
    t_element = ET.SubElement(run, f"{_A}t")
    t_element.text = text
    if text[:1].isspace() or text[-1:].isspace():
        t_element.set(XML_SPACE_ATTR, "preserve")
    return run


def rebuild_paragraph_runs(paragraph: ET.Element, translated_text: str) -> bool:
    """用带标签的译文重建段落的 run 序列。

    成功返回 ``True``；任何异常（结构不可标签化 / 标签损坏 / 无有效文本）返回
    ``False``，调用方据此回退到“整段写入第一个 run”的原有逻辑。
    """
    tagged = build_tagged_paragraph(paragraph)
    if tagged is None or not tagged.has_tags:
        return False

    text = sanitize_tagged_text(translated_text or "")
    pieces = _partition_by_tags(text, set(tagged.id_to_rpr.keys()))
    if pieces is None:
        return False

    new_runs: list[ET.Element] = []
    for tag_id, piece_text in pieces:
        if piece_text == "":
            continue
        rpr = tagged.base_rpr if tag_id is None else tagged.id_to_rpr.get(tag_id)
        new_runs.append(_make_run(rpr, piece_text))

    if not new_runs:
        return False

    p_pr = paragraph.find(f"{_A}pPr")
    end_pr = paragraph.find(f"{_A}endParaRPr")
    for run in paragraph.findall(f"{_A}r"):
        paragraph.remove(run)

    current = list(paragraph)
    insert_at = current.index(p_pr) + 1 if p_pr is not None else 0
    if end_pr is not None:
        insert_at = min(insert_at, current.index(end_pr))

    for offset, run in enumerate(new_runs):
        paragraph.insert(insert_at + offset, run)
    return True


def _build_translated_runs(paragraph: ET.Element, translated_text: str) -> list[ET.Element] | None:
    """根据带标签译文构造译文 run 列表（尽量还原 run 级格式）。

    成功返回 run 列表；无法按标签重建时回退为“单个 run + 基准/首个 run 样式 + 去标记
    纯文本”，仍返回一个 run；无有效文本返回 ``None``。
    """
    tagged = build_tagged_paragraph(paragraph)

    if tagged is not None and tagged.has_tags:
        text = sanitize_tagged_text(translated_text or "")
        pieces = _partition_by_tags(text, set(tagged.id_to_rpr.keys()))
        if pieces is not None:
            runs: list[ET.Element] = []
            for tag_id, piece_text in pieces:
                if piece_text == "":
                    continue
                rpr = tagged.base_rpr if tag_id is None else tagged.id_to_rpr.get(tag_id)
                runs.append(_make_run(rpr, piece_text))
            if runs:
                return runs

    # 降级：整段译文写进一个 run，套用基准样式（或首个 run 样式），并剥离标记
    plain = strip_format_tags(translated_text or "").strip()
    if not plain:
        return None
    base_rpr = tagged.base_rpr if tagged is not None else None
    if base_rpr is None:
        first_run = paragraph.find(f"{_A}r")
        first_rpr = first_run.find(f"{_A}rPr") if first_run is not None else None
        base_rpr = copy.deepcopy(first_rpr) if first_rpr is not None else None
    return [_make_run(base_rpr, plain)]


def append_translation_runs(paragraph: ET.Element, translated_text: str) -> bool:
    """双语导出：保留原段落 run（原文格式不动），追加换行 + 译文 run。

    与 :func:`rebuild_paragraph_runs` 不同——它不清空原有 run，因此原文的 run 级格式
    完整保留；译文另起一行、按标签尽量还原自身格式。成功返回 ``True``。
    """
    new_runs = _build_translated_runs(paragraph, translated_text)
    if not new_runs:
        return False

    end_pr = paragraph.find(f"{_A}endParaRPr")
    children = list(paragraph)
    insert_at = children.index(end_pr) if end_pr is not None else len(children)

    line_break = ET.Element(f"{_A}br")
    paragraph.insert(insert_at, line_break)
    for offset, run in enumerate(new_runs):
        paragraph.insert(insert_at + 1 + offset, run)
    return True
