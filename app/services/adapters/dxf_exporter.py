"""
DXF 导出器 - 在原 DXF 文档上原地替换文本，最大化保留几何/样式/图层结构。

实现策略：
- 读入原始 DXF（ezdxf DOM）
- 遍历所有 layout 与具名 block 的文本类实体，按 source -> target 映射替换
- MTEXT 的格式控制码先剥离匹配，再把译文重新写回（沿用原 dxf.char_height/style）
- 写回时优先尝试与原文件相同的 dxfversion，导出 UTF-8 文本

可选行为（仅 DWG 链路启用，默认关闭以保持 .dxf 用户行为不变）：
- enable_overflow_shrink：译文超长时按视觉宽度比例缩字宽因子 / MTEXT 字高
- handle_extra_entities：回写 MULTILEADER 的 MTEXT、ACAD_TABLE 的单元格

语义重建文本导出（Spatial Merge Export）：
核心思路：做"逻辑重建"而不是"几何合并"
- 当 translations 包含合并文本信息时，启用 MTEXT 重建模式
- 方案A（推荐）：删除原 fragmented text，用"单一 MTEXT block"重新生成
- 在主实体位置创建新的 MTEXT 实体承载完整译文
- 清空所有被合并的原始 TEXT 实体的文本内容，避免重叠
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.services.adapters.dxf_adapter import (
    DxfAdapter,
    _is_cad_diameter_block,
    _is_dimension_like,
    _mtext_paragraph_heights,
    _TEXT_ENTITY_TYPES,
    _visual_length,
    clean_mtext,
)
from app.services.adapters.text_reconstruction import estimate_text_width


logger = logging.getLogger(__name__)


CAD_MTEXT_HANDLE_TRANSLATION_PREFIX = "__cad_mtext_handle__:"
CAD_MTEXT_HANDLE_BLOCK_PREFIX = "__cad_mtext_incomplete__:"
CAD_MTEXT_SOURCE_BLOCK_PREFIX = "__cad_mtext_source_incomplete__:"
CAD_MTEXT_MAX_HEIGHT_PREFIX = "__cad_mtext_max_height__:"
CAD_TEXT_HANDLE_TRANSLATION_PREFIX = "__cad_text_handle__:"


# 语言/字符类边界正则：仅命中"必然是跨段拼错"的位置：
# - CJK ↔ 拉丁字母 / 数字：中英/中数交界
# - 6+ 位数字 后紧跟大写字母：例如 "A136003664JIANGXI"
# 不动 "iPhone12" / "IPv4" 这类合法字母-数字混排，避免误伤正常英文。
_CROSS_LANG_BREAK_RE = re.compile(
    r"(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])"
    r"|(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])"
    r"|(?<=\d{6})(?=[A-Z])"
)


def _break_over_wide_paragraphs(
    text: str,
    height: float,
    group_width: float,
) -> str:
    """若合并句段某一段视觉宽度显著超出 group_width，或段内混合了 CJK 与
    长英文/数字串，就在跨语种边界处强插 ``\\P``。

    专门用来兜底 INSERT 变换（rotation / mirror）导致的归行错乱：碎片 TEXT
    被空间合并按 world y 错误串接成 "NATIONAL...JIANGXI...江西省..."
    这类首尾无空格、翻译后无自然断点的长串，MTEXT 折行找不到空格会
    整段横铺出去。
    """
    if not text:
        return text

    threshold = group_width * 1.5 if group_width and group_width > 0 else 0.0
    marker = "\x00"
    out_parts: List[str] = []
    for paragraph in text.split(r"\P"):
        if not paragraph:
            out_parts.append(paragraph)
            continue

        needs_break = False
        if height > 0 and threshold > 0:
            width_estimate = estimate_text_width(paragraph, height)
            if width_estimate > threshold:
                needs_break = True
        if not needs_break:
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", paragraph))
            has_long_ascii = bool(re.search(r"[A-Za-z0-9]{16,}", paragraph.replace(" ", "")))
            if has_cjk and has_long_ascii:
                needs_break = True

        if not needs_break:
            out_parts.append(paragraph)
            continue

        replaced = _CROSS_LANG_BREAK_RE.sub(marker, paragraph)
        pieces = [piece for piece in replaced.split(marker) if piece]
        if len(pieces) <= 1:
            out_parts.append(paragraph)
        else:
            out_parts.extend(pieces)
    return r"\P".join(out_parts)


def _wrap_mtext_to_width(
    text: str,
    height: float,
    width: float,
    *,
    first_line_indent: float = 0.0,
) -> str:
    """按实际字宽插入 MTEXT 段落符，并保留首行缩进。"""
    if not text or height <= 0 or width <= 0:
        return text

    wrapped: List[str] = []
    first_visual_line = True
    for paragraph in re.split(r"\\P|\r\n?|\n", text):
        normalized = re.sub(r"[ \t]+", " ", paragraph).strip()
        if not normalized:
            wrapped.append("")
            first_visual_line = False
            continue

        line = ""
        for char in normalized:
            candidate = line + char
            indent = max(first_line_indent, 0.0) if first_visual_line else 0.0
            if (
                not line
                or estimate_text_width(candidate, height) + indent <= width
            ):
                line = candidate
                continue

            break_at = line.rfind(" ")
            if break_at > 0:
                wrapped.append(line[:break_at].rstrip())
                line = line[break_at + 1 :] + char
            else:
                wrapped.append(line.rstrip())
                line = char.lstrip()
            first_visual_line = False
        if line:
            wrapped.append(line.rstrip())
        first_visual_line = False

    result = r"\P".join(wrapped)
    if first_line_indent > 0 and result:
        space_width = max(estimate_text_width(" ", height), height * 0.3, 1e-6)
        indent_spaces = min(max(int(round(first_line_indent / space_width)), 1), 64)
        result = (r"\~" * indent_spaces) + result
    return result


def _split_translation_sentences(text: str) -> List[str]:
    """按自然句边界拆分译文，同时避免把 ``II.`` 等标题序号拆开。"""
    value = re.sub(r"[ \t]+", " ", text or "").strip()
    if not value:
        return []

    sentences: List[str] = []
    current: List[str] = []
    length = len(value)
    for index, char in enumerate(value):
        current.append(char)
        if char not in ".!?。！？":
            continue

        next_index = index + 1
        while next_index < length and value[next_index].isspace():
            next_index += 1
        at_end = next_index >= length
        current_text = "".join(current).strip()

        if char == ".":
            previous = value[index - 1] if index > 0 else ""
            following = value[index + 1] if index + 1 < length else ""
            if previous.isdigit() and following.isdigit():
                continue
            # 标题/列表开头的罗马数字和数字序号属于下一句正文。
            if re.fullmatch(r"(?:[IVXLCDM]+|\d+)\.", current_text, re.IGNORECASE):
                continue
            if not at_end and index + 1 < length and not value[index + 1].isspace():
                continue

        if not at_end:
            next_char = value[next_index]
            if char == "." and not (
                next_char.isupper()
                or next_char.isdigit()
                or "\u4e00" <= next_char <= "\u9fff"
                or next_char in "\"'([{"
            ):
                continue

        if current_text:
            sentences.append(current_text)
        current = []

    remainder = "".join(current).strip()
    if remainder:
        sentences.append(remainder)
    return sentences or [value]


def _split_translated_heading(
    target_text: str,
    source_heading: str,
) -> Optional[Tuple[str, str]]:
    """从未保留换行的译文中恢复“编号标题 + 正文”边界。"""
    source = (source_heading or "").strip()
    if not re.match(
        r"^(?:[一二三四五六七八九十百]+[、．.]|[IVXLCDM]+[．.]|\d+(?:\.\d+)*[、．.])",
        source,
        re.IGNORECASE,
    ):
        return None

    value = re.sub(r"[ \t]+", " ", target_text or "").strip()
    if not value:
        return None
    words = list(re.finditer(r"\S+", value))
    if len(words) < 6:
        return None

    body_starters = {
        "the", "a", "an", "this", "these", "according", "based",
        "after", "in", "for", "该", "本", "根据", "按照",
    }
    candidates: List[int] = []
    for index, match in enumerate(words[4:], start=4):
        normalized = re.sub(r"^[\"'([{]+|[,:;，：；]+$", "", match.group(0)).casefold()
        if normalized in body_starters:
            candidates.append(match.start())
    if not candidates:
        return None

    boundary = candidates[0]
    heading = value[:boundary].strip()
    body = value[boundary:].strip()
    if not heading or not body:
        return None
    return heading, body


def _restore_mtext_paragraphs(target_text: str, source_layout_text: str) -> str:
    """按原 MTEXT 段落长度把整块译文的连续句子恢复为多个段落。"""
    source_paragraphs = [
        part.strip()
        for part in re.split(r"\\P|\r\n?|\n", source_layout_text or "")
        if part.strip()
    ]
    if len(source_paragraphs) <= 1:
        return target_text

    target_paragraphs = [
        part.strip()
        for part in re.split(r"\\P|\r\n?|\n", target_text or "")
        if part.strip()
    ]
    if len(target_paragraphs) > 1:
        return r"\P".join(target_paragraphs)

    heading_split = _split_translated_heading(target_text, source_paragraphs[0])
    if heading_split is not None and len(source_paragraphs) > 1:
        heading, body = heading_split
        restored_body = _restore_mtext_paragraphs(
            body,
            r"\P".join(source_paragraphs[1:]),
        )
        if len([
            part for part in restored_body.split(r"\P") if part.strip()
        ]) == len(source_paragraphs) - 1:
            return heading + r"\P" + restored_body

    sentences = _split_translation_sentences(target_text)
    paragraph_count = len(source_paragraphs)
    if len(sentences) < paragraph_count:
        return target_text

    source_lengths = [max(_visual_length(part), 1.0) for part in source_paragraphs]
    sentence_lengths = [max(_visual_length(part), 1.0) for part in sentences]
    source_total = sum(source_lengths)
    target_total = sum(sentence_lengths)
    expected_lengths = [length * target_total / source_total for length in source_lengths]
    prefix = [0.0]
    for length_value in sentence_lengths:
        prefix.append(prefix[-1] + length_value)

    # 动态规划选择连续句子边界，使每段译文长度与原段落长度比例最接近。
    infinity = float("inf")
    costs = [[infinity] * (len(sentences) + 1) for _ in range(paragraph_count + 1)]
    previous = [[-1] * (len(sentences) + 1) for _ in range(paragraph_count + 1)]
    costs[0][0] = 0.0
    for paragraph_index in range(1, paragraph_count + 1):
        minimum_end = paragraph_index
        maximum_end = len(sentences) - (paragraph_count - paragraph_index)
        for end in range(minimum_end, maximum_end + 1):
            for start in range(paragraph_index - 1, end):
                if costs[paragraph_index - 1][start] == infinity:
                    continue
                actual = prefix[end] - prefix[start]
                expected = max(expected_lengths[paragraph_index - 1], 1.0)
                cost = costs[paragraph_index - 1][start] + ((actual - expected) / expected) ** 2
                if cost < costs[paragraph_index][end]:
                    costs[paragraph_index][end] = cost
                    previous[paragraph_index][end] = start

    boundaries = [len(sentences)]
    end = len(sentences)
    for paragraph_index in range(paragraph_count, 0, -1):
        start = previous[paragraph_index][end]
        if start < 0:
            return target_text
        boundaries.append(start)
        end = start
    boundaries.reverse()

    restored = [
        " ".join(sentences[boundaries[index]:boundaries[index + 1]]).strip()
        for index in range(paragraph_count)
    ]
    if any(not paragraph for paragraph in restored):
        return target_text
    return r"\P".join(restored)


_MTEXT_PARAGRAPH_SPLIT_RE = re.compile(r"\\+P|\r\n?|\n")
_MTEXT_SOURCE_PARAGRAPH_RE = re.compile(
    r"(\{\\H[^;]*;\s*\\+P\}?|\\+P|\r\n?|\n)"
)
_MTEXT_FORMAT_TOKEN_RE = re.compile(
    r"(\\(?:[FfHhWwQqTtCcAaPp][^;]*;|[LlOoKk])|[{}])"
)


def _has_full_outer_mtext_scope(value: str) -> bool:
    """判断首个 ``{`` 是否与末尾 ``}`` 构成覆盖全文的 MTEXT 作用域。"""
    stripped = (value or "").strip()
    if len(stripped) < 2 or not stripped.startswith("{") or not stripped.endswith("}"):
        return False

    depth = 0
    last_index = len(stripped) - 1
    for index, char in enumerate(stripped):
        if char not in "{}":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and stripped[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2:
            continue
        if char == "{":
            depth += 1
            continue
        depth -= 1
        if depth < 0:
            return False
        if depth == 0 and index != last_index:
            return False
    return depth == 0


def _format_mtext_paragraph_like_source(
    source_raw: str,
    target_text: str,
) -> str:
    """把源段落的 MTEXT 格式控制码映射到译文的相近语义位置。"""
    source_value = source_raw or ""
    stripped_source = source_value.strip()
    preserve_outer_scope = _has_full_outer_mtext_scope(stripped_source)
    tokens: List[Tuple[int, str, str]] = []
    visible_parts: List[str] = []
    cursor = 0
    visible_length = 0
    for match in _MTEXT_FORMAT_TOKEN_RE.finditer(source_raw or ""):
        visible = clean_mtext(source_raw[cursor:match.start()])
        visible_parts.append(visible)
        visible_length += len(visible)
        token = match.group(0)
        previous_visible = "".join(visible_parts)[-1:] if visible_parts else ""
        tokens.append((visible_length, token, previous_visible))
        cursor = match.end()
    tail = clean_mtext((source_raw or "")[cursor:])
    visible_parts.append(tail)
    source_visible = "".join(visible_parts)
    if not tokens:
        return target_text

    target = target_text or ""
    target_boundaries = {0, len(target)}
    target_boundaries.update(
        index
        for index in range(1, len(target))
        if target[index - 1].isspace()
        or target[index].isspace()
        or target[index - 1] in ",.;:!?，。；：！？"
    )
    punctuation_equivalents = {
        "，": ",", "。": ".", "；": ";", "：": ":",
        "！": "!", "？": "?",
    }
    punctuation_positions: Dict[str, List[int]] = {}
    for index, char in enumerate(target, start=1):
        normalized_punctuation = punctuation_equivalents.get(char, char)
        if normalized_punctuation in ",.;:!?":
            punctuation_positions.setdefault(normalized_punctuation, []).append(index)

    controls_by_offset: Dict[int, List[str]] = {}
    source_length = max(len(source_visible), 1)
    for source_offset, token, previous_visible in tokens:
        # 段首控制码定义整段字体、字号、颜色和缩进，应完整保留。段中只
        # 迁移颜色切换；局部 \H/\W 和花括号通常只服务于 m² 等单个字符，
        # 按长度比例搬到英文会随机改变单词字号，必须丢弃。
        is_leading = source_offset == 0 and token not in {"{", "}"}
        is_color_switch = bool(re.fullmatch(r"\\[Cc][^;]*;", token))
        if not (is_leading or is_color_switch):
            continue

        desired = round(source_offset * len(target) / source_length)
        candidates = target_boundaries
        source_punctuation = punctuation_equivalents.get(
            previous_visible,
            previous_visible,
        )
        if source_punctuation in punctuation_positions:
            matching = punctuation_positions[source_punctuation]
            if matching:
                candidates = set(matching)
        target_offset = min(candidates, key=lambda value: abs(value - desired))
        controls_by_offset.setdefault(target_offset, []).append(token)

    output: List[str] = []
    for index in range(len(target) + 1):
        output.extend(controls_by_offset.get(index, []))
        if index < len(target):
            output.append(target[index])
    formatted_target = "".join(output)

    # 单位符号等原样保留的短文本需要继续使用源字体覆盖，否则 SHX 字体可能
    # 无法显示 ㎡/²。只按相同可见字面量迁移，不把字体控制按比例塞进英文单词。
    literal_font_formats: Dict[str, str] = {}
    for match in re.finditer(r"\{(\\[Ff][^;]*;)([^{}]+)\}", source_raw or ""):
        literal = clean_mtext(match.group(2)).strip()
        if 0 < len(literal) <= 4:
            literal_font_formats.setdefault(literal, match.group(1))
    for literal, font_control in literal_font_formats.items():
        if literal in formatted_target:
            formatted_target = formatted_target.replace(
                literal,
                "{" + font_control + literal + "}",
            )
    if preserve_outer_scope:
        formatted_target = "{" + formatted_target + "}"
    return formatted_target


def _apply_original_mtext_layout(
    source_raw: str,
    source_cleaned: str,
    target_text: str,
) -> str:
    """恢复原 MTEXT 的段落、缩进、颜色、字号和字体控制码。"""
    source_value = source_raw or ""
    stripped_source = source_value.strip()
    preserve_cross_paragraph_scope = bool(
        _has_full_outer_mtext_scope(stripped_source)
        and _MTEXT_SOURCE_PARAGRAPH_RE.search(source_value)
    )

    def restore_cross_paragraph_scope(value: str) -> str:
        if preserve_cross_paragraph_scope:
            return "{" + value + "}"
        return value

    restored = _restore_mtext_paragraphs(target_text, source_cleaned)
    target_parts = [
        part.strip()
        for part in _MTEXT_PARAGRAPH_SPLIT_RE.split(restored)
        if part.strip()
    ]
    # 捕获并原样保留段落分隔串。ODA 常把段间距编码为
    # ``{\H1.6238x; \P}``；若只保留 \P，会丢失截图中的段前空白。
    source_chunks = _MTEXT_SOURCE_PARAGRAPH_RE.split(source_value)
    raw_parts = source_chunks[::2]
    separators = source_chunks[1::2]
    visible_raw_parts = [
        part for part in raw_parts if clean_mtext(part).strip()
    ]
    if len(target_parts) != len(visible_raw_parts):
        return restore_cross_paragraph_scope(restored)

    formatted: List[str] = []
    target_index = 0
    for index, raw_part in enumerate(raw_parts):
        if clean_mtext(raw_part).strip():
            formatted.append(_format_mtext_paragraph_like_source(
                raw_part,
                target_parts[target_index],
            ))
            target_index += 1
        else:
            formatted.append(raw_part)
        if index < len(separators):
            formatted.append(separators[index])
    return restore_cross_paragraph_scope("".join(formatted))


def _mtext_has_unformatted_non_ascii(formatted_text: str) -> bool:
    """判断富格式 MTEXT 是否仍有未被字体 run 覆盖的非 ASCII 字符。"""
    without_font_runs = re.sub(
        r"\{\\[Ff][^;]*;[^{}]*\}",
        "",
        formatted_text or "",
    )
    return any(ord(char) > 127 for char in clean_mtext(without_font_runs))


@dataclass(frozen=True)
class DxfExportOptions:
    """DXF 导出可选行为开关。

    所有字段默认值都对应"老行为"，新调用方按需打开即可，不影响 .dxf 普通路径。
    """

    enable_overflow_shrink: bool = False
    """译文超长时缩字宽因子 / MTEXT 字高。"""

    min_width_factor: float = 0.55
    """字宽因子最低值，过低会变形。"""

    shrink_threshold: float = 1.02
    """译文/原文视觉长度比超过该阈值才开始缩。"""

    min_char_height_ratio: float = 0.5
    """字高最低缩到原值的比例，过低会看不清。"""

    handle_extra_entities: bool = False
    """处理 MULTILEADER / ACAD_TABLE 等扩展实体。"""

    fix_shx_font_for_unicode: bool = False
    """为非 ASCII 字符创建支持 Unicode 的文本样式。"""

    unicode_font_name: str = "Arial"
    """用于替换 SHX 字体的 TrueType 字体名称。"""

    enable_spatial_merge_export: bool = False
    """启用空间合并文本的 MTEXT 重建导出模式。"""


@dataclass
class MergedTextExportInfo:
    """合并文本导出信息"""
    source_text: str  # 原始合并后的源文本
    target_text: str  # 翻译后的目标文本
    primary_handle: str  # 主实体 handle
    merged_handles: List[str]  # 所有被合并的实体 handle
    source_layout_text: str = ""
    """保留原 MTEXT 段落边界的展示文本。"""
    primary_x: float = 0.0
    primary_y: float = 0.0
    primary_height: float = 2.5
    primary_style: str = ""
    primary_color: int = 256
    primary_true_color: Optional[int] = None
    primary_transparency: Optional[int] = None
    group_x: float = 0.0
    group_y_top: float = 0.0
    group_width: float = 0.0
    source_group_width: float = 0.0
    """解析阶段的原始世界坐标宽度；group_width 后续可能被真实右边界修正。"""
    group_height: float = 0.0
    first_line_indent: float = 0.0
    """首行相对文本框左边界的 CAD 坐标偏移。"""
    cad_table_cell: bool = False
    single_text_block: bool = False
    """由单个 TEXT/ATTRIB/ATTDEF 构成、但仍具有文本框几何的句段。"""
    preserve_mtext_layout: bool = False
    """内容含显式 MTEXT 换行/字号/缩进控制码，创建时不得重新折行。"""
    source_mtext_layout: bool = False
    """矩形边界已从原 MTEXT 和相邻文本块恢复，可用于普通段落重排。"""
    max_height: Optional[float] = None
    """经可靠外框或下一同列文本块推导出的硬高度；未知时不做纵向拟合。"""
    reliable_width: bool = False
    """宽度已由右侧框线或同行下一文本块证明，而非仅由原文字形估算。"""
    scope: str = ""
    layer: str = "0"


class DxfExporter:
    """DXF 导出器（ezdxf 实现）"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        *,
        options: DxfExportOptions | None = None,
        audit_path: str | None = None,
        merged_text_info: List[Dict] | None = None,
        mtext_split_info: List[Dict] | None = None,
    ) -> bytes:
        """导出翻译后的 DXF。
        
        Args:
            original_bytes: 原始 DXF 字节
            translations: 源文本 -> 目标文本的映射
            options: 导出选项
            audit_path: 审计日志输出路径（可选）
            merged_text_info: 空间合并文本信息列表（可选），每项包含：
                - source_text: 原始合并后的源文本
                - target_text: 翻译后的目标文本  
                - primary_handle: 主实体 handle
                - merged_handles: 所有被合并的实体 handle 列表
                - primary_x, primary_y, primary_height: 主实体位置和字高
                - layer: 图层名
        """
        if not original_bytes:
            return original_bytes
        if not translations:
            return original_bytes

        opts = options or DxfExportOptions()

        doc = self._read_doc(original_bytes)
        if doc is None:
            return original_bytes

        # 如果启用 Unicode 字体修复，创建支持 Unicode 的文本样式
        unicode_style_name: Optional[str] = None
        if opts.fix_shx_font_for_unicode:
            unicode_style_name = self._ensure_unicode_style(doc, opts.unicode_font_name)

        # 规范化键，便于宽松匹配（去首尾空白）
        normalized: Dict[str, str] = {}
        no_space_map: Dict[str, str] = {}  # 移除所有空白后的映射
        for src, tgt in translations.items():
            if src is None or tgt is None:
                continue
            normalized[src] = tgt
            stripped = src.strip()
            if stripped and stripped not in normalized:
                normalized[stripped] = tgt
            # 多空白规范化
            multi_space_normalized = re.sub(r"\s+", " ", stripped)
            if multi_space_normalized and multi_space_normalized not in normalized:
                normalized[multi_space_normalized] = tgt
            # 移除所有空白（用于兜底匹配）
            no_space = re.sub(r"\s", "", stripped)
            if no_space and no_space not in no_space_map:
                no_space_map[no_space] = tgt

        # 将无空白映射合并到 normalized（作为最后的兜底）
        for no_space_key, tgt in no_space_map.items():
            if no_space_key not in normalized:
                normalized[no_space_key] = tgt

        # MTEXT 拆段：按原 MTEXT handle 聚合所有拆段的译文；
        # 遍历实体时，遇到该 handle 就清空原 MTEXT 并在原 y 位置创建独立小 MTEXT。
        mtext_split_by_parent: Dict[str, List[Dict]] = {}
        if mtext_split_info:
            for item in mtext_split_info:
                ph = str(item.get("parent_handle") or "")
                if not ph:
                    continue
                mtext_split_by_parent.setdefault(ph, []).append(item)
            for ph, items in mtext_split_by_parent.items():
                items.sort(key=lambda it: -float(it.get("y") or 0))
            logger.info("DXF 导出：MTEXT 拆段回写，共 %d 个父 MTEXT", len(mtext_split_by_parent))

        # 复杂模式导出：优先在原文本区域创建可换行 MTEXT；创建失败时
        # 回退到把译文写入主 TEXT，保证任何情况下都不会丢字。
        merged_handles_to_clear: Set[str] = set()
        merged_primary_translations: Dict[str, Tuple[str, str]] = {}
        merged_export_infos: List[MergedTextExportInfo] = []

        if merged_text_info and opts.enable_spatial_merge_export:
            logger.info("DXF 导出：处理 %d 个合并文本信息", len(merged_text_info))
            for raw_info in merged_text_info:
                handles = raw_info.get("merged_handles", [])
                if isinstance(handles, str):
                    try:
                        parsed_handles = json.loads(handles)
                    except (TypeError, json.JSONDecodeError):
                        parsed_handles = [
                            value.strip()
                            for value in handles.split(",")
                            if value.strip()
                        ]
                    handles = parsed_handles if isinstance(parsed_handles, list) else []
                handles = [str(handle) for handle in handles if str(handle).strip()]
                source_text = raw_info.get("source_text", "")
                target_text = raw_info.get("target_text", "") or self._lookup(source_text, normalized)
                primary_handle = raw_info.get("primary_handle", handles[0] if handles else "")
                if primary_handle and primary_handle in mtext_split_by_parent:
                    logger.info(
                        "跳过闭合框重建：handle=%s 已由 MTEXT 拆段路径处理",
                        primary_handle,
                    )
                    continue
                if not target_text or not primary_handle:
                    logger.warning(
                        "DXF 导出：合并组无译文 handles=%s, source=%s...",
                        handles[:3], source_text[:30] if source_text else "(empty)",
                    )
                    continue
                merged_primary_translations[primary_handle] = (source_text, target_text)
                merged_export_infos.append(MergedTextExportInfo(
                    source_text=source_text,
                    source_layout_text=str(raw_info.get("source_layout_text", "") or source_text),
                    target_text=target_text,
                    primary_handle=primary_handle,
                    merged_handles=list(handles),
                    primary_x=float(raw_info.get("primary_x", 0) or 0),
                    primary_y=float(raw_info.get("primary_y", 0) or 0),
                    primary_height=float(raw_info.get("primary_height", 2.5) or 2.5),
                    primary_style=str(raw_info.get("primary_style", "") or ""),
                    primary_color=int(raw_info.get("primary_color", 256) if raw_info.get("primary_color") is not None else 256),
                    primary_true_color=(
                        int(raw_info["primary_true_color"])
                        if raw_info.get("primary_true_color") is not None else None
                    ),
                    primary_transparency=(
                        int(raw_info["primary_transparency"])
                        if raw_info.get("primary_transparency") is not None else None
                    ),
                    group_x=float(raw_info.get("group_x", 0) or 0),
                    group_y_top=float(raw_info.get("group_y_top", 0) or 0),
                    group_width=float(raw_info.get("group_width", 0) or 0),
                    source_group_width=float(raw_info.get("group_width", 0) or 0),
                    group_height=float(raw_info.get("group_height", 0) or 0),
                    first_line_indent=float(raw_info.get("first_line_indent", 0) or 0),
                    cad_table_cell=bool(raw_info.get("cad_table_cell", False)),
                    single_text_block=bool(raw_info.get("single_text_block", False)),
                    scope=str(raw_info.get("scope", "") or ""),
                    layer=str(raw_info.get("layer", "0") or "0"),
                ))

        merged_export_infos = self._detach_heading_from_rich_mtext_groups(
            doc,
            merged_export_infos,
            normalized,
            merged_primary_translations,
        )

        # 不同重建项即使落在同一 CAD 框内，也可能是标题、字段和值等独立
        # 语义块。只按主实体去重，禁止仅凭相同 frame 几何再次合成一个 MTEXT。
        unique_infos: Dict[Tuple, MergedTextExportInfo] = {}
        for info in merged_export_infos:
            key = ("handle", info.primary_handle)

            existing = unique_infos.get(key)
            if existing is None:
                unique_infos[key] = info
                continue

            existing.merged_handles = list(dict.fromkeys(
                [*existing.merged_handles, *info.merged_handles]
            ))
            # 同一主实体一旦有任一重建项来自多实体组，就按完整 merged 组处理；
            # 只有全部重复项都是单实体文本块时才保留该安全限制。
            existing.single_text_block = (
                existing.single_text_block and info.single_text_block
            )
            existing_source_parts = re.split(r"\\P|\r\n?|\n", existing.source_text)
            if info.source_text and info.source_text not in existing_source_parts:
                existing.source_text = f"{existing.source_text}\n{info.source_text}".strip()
            existing_target_parts = re.split(r"\\P|\r\n?|\n", existing.target_text)
            if info.target_text and info.target_text not in existing_target_parts:
                existing.target_text = f"{existing.target_text}\\P{info.target_text}".strip(r"\P")

        if len(unique_infos) != len(merged_export_infos):
            logger.info(
                "DXF 重建项去重：%d 项归并为 %d 个文本区域",
                len(merged_export_infos),
                len(unique_infos),
            )
        merged_export_infos = list(unique_infos.values())
        self._restore_merged_mtext_layouts(doc, merged_export_infos)
        for info in merged_export_infos:
            if info.primary_handle in merged_primary_translations:
                merged_primary_translations[info.primary_handle] = (
                    info.source_text,
                    info.target_text,
                )

        # 解析阶段会过滤“(mm)”等不可翻译单位，旧句段的 merged_handles 因此可能
        # 只包含中文标签。创建译文 MTEXT 前，按已识别的表格单元格内边界查找单位
        # 兄弟实体：把单位补进译文，并把其 handle 纳入清空集合，避免新旧文字叠加。
        wrapped_unit_re = re.compile(r"^[\(\[][^\(\)\[\]]{1,20}[\)\]]$")
        bare_unit_re = re.compile(
            r"^(?:/?(?:mm|cm|dm|m|km|in|inch|ft|h|min|s|ms|l|ml|kg|g|t|"
            r"pa|kpa|mpa|bar|psi)(?:[²³23])?"
            r"(?:[/·](?:mm|cm|m|km|h|min|s|l|kg|g)(?:[²³23])?)?)$",
            re.IGNORECASE,
        )
        partial_unit_re = re.compile(
            r"^[\(\[]?/?(?:mm|cm|dm|m|km|in|inch|ft|h|min|s|ms|l|ml|kg|g|t|"
            r"pa|kpa|mpa|bar|psi)(?:[²³23])?"
            r"(?:[/·](?:mm|cm|m|km|h|min|s|l|kg|g)(?:[²³23])?)?[\)\]]?$",
            re.IGNORECASE,
        )
        list_number_re = re.compile(r"^\d+(?:\.\d+)+[.)、:]?$")
        table_reference_re = re.compile(r"^\d+(?:-\d+)+$")
        punctuation_fragment_re = re.compile(r"^[,，。；;：:]+$")
        # 旧任务在导入时可能已过滤表格中的编号/设备代码，导致新建 MTEXT 后
        # X1/X2 等旧 TEXT 仍叠在译文上。只接受含数字的短 CAD 代码，避免把
        # 普通英文句子误当成结构片段。
        table_code_fragment_re = re.compile(
            r"^(?=.{1,32}$)(?=.*\d)[A-Za-z0-9_./+:#%°×x()\[\]-]+$"
        )
        diameter_code_re = re.compile(
            r"^(?:DN|DE|OD|ID)\s*\d+(?:\.\d+)?(?:\s*[x×]\s*\d+(?:\.\d+)?)?$",
            re.IGNORECASE,
        )

        def is_unit_fragment(value: str) -> bool:
            cleaned = clean_mtext(value or "").strip()
            if not cleaned or len(cleaned) > 24 or re.search(r"[\u4e00-\u9fff]", cleaned):
                return False
            if wrapped_unit_re.fullmatch(cleaned):
                return bool(re.search(r"[A-Za-z%°²³]", cleaned))
            return bool(
                bare_unit_re.fullmatch(cleaned)
                or partial_unit_re.fullmatch(cleaned)
            )

        # 未变化的纯单位单元格会在坐标辅助函数定义后尝试与同格标签合并；
        # 找不到标签时才跳过独立 MTEXT 重建，保留原单位 TEXT。

        def entity_text(entity) -> str:
            kind = entity.dxftype()
            if kind == "MTEXT":
                return clean_mtext(entity.text or "")
            return str(getattr(entity.dxf, "text", "") or "")

        def entity_anchor_points(entity, transform=None) -> List[Tuple[float, float]]:
            points: List[Tuple[float, float]] = []
            for attr in ("insert", "align_point"):
                try:
                    point = entity.dxf.get(attr)
                    if point is not None:
                        x, y = float(point.x), float(point.y)
                        if transform is not None:
                            x, y, _ = transform.transform((x, y, 0.0))
                        coordinates = (float(x), float(y))
                        if coordinates not in points:
                            points.append(coordinates)
                except Exception:  # noqa: BLE001 - 不同 DXF 类型支持的坐标属性不同
                    continue
            return points

        def insert_transform(insert_entity, parent=None):
            """返回 INSERT 子实体到当前图纸世界坐标的变换，支持嵌套块。"""
            try:
                from ezdxf.math import Matrix44

                transform = insert_entity.matrix44()
                if parent is not None:
                    transform = Matrix44.chain(transform, parent)
                return transform
            except Exception:  # noqa: BLE001 - 非法/不完整 INSERT 退化为父变换
                return parent

        def add_structured_diameter_annotations() -> None:
            """把“标题 + 字段 + 多行管径”重建为一个保留行列的 MTEXT。"""
            if not opts.handle_extra_entities:
                return

            geometry_adapter = DxfAdapter()
            used_handles: Set[str] = set()

            def translated(value: str) -> Optional[str]:
                replacement = self._lookup(value, normalized)
                if replacement is None:
                    replacement = self._merge_sentence_translations(value, normalized)
                if not replacement or replacement == value:
                    return None
                return replacement

            def scan_space(space, scope: str) -> None:
                entities_by_handle: Dict[str, object] = {}
                geometries = []
                for entity in space:
                    if entity.dxftype() not in {"TEXT", "MTEXT"}:
                        continue
                    try:
                        geometry = geometry_adapter._extract_text_entity(entity, scope)
                    except Exception:  # noqa: BLE001 - 非标准实体退化为普通回写
                        geometry = None
                    if geometry is None or not geometry.handle or not geometry.text.strip():
                        continue
                    entities_by_handle[geometry.handle] = entity
                    geometries.append(geometry)

                code_blocks = [
                    geometry for geometry in geometries
                    if len([
                        line for line in clean_mtext(geometry.text).splitlines()
                        if line.strip()
                    ]) >= 2
                    and _is_cad_diameter_block(geometry.text)
                ]
                for codes in code_blocks:
                    if codes.handle in used_handles:
                        continue
                    code_lines = [
                        line.strip()
                        for line in clean_mtext(codes.text).splitlines()
                        if line.strip()
                    ]
                    label_candidates = []
                    for label in geometries:
                        if label.handle == codes.handle or label.handle in used_handles:
                            continue
                        label_target = translated(label.text.strip())
                        if label_target is None or label.layer != codes.layer:
                            continue
                        average_height = max((label.height + codes.height) / 2.0, 1e-6)
                        if abs(label.y - codes.y) > average_height * 1.2:
                            continue
                        if label.x >= codes.x:
                            continue
                        horizontal_gap = codes.x - label.right_edge
                        if not (-average_height * 2 <= horizontal_gap <= average_height * 25):
                            continue
                        label_candidates.append((
                            abs(label.y - codes.y) + abs(horizontal_gap) * 0.05,
                            label,
                            label_target,
                        ))
                    if not label_candidates:
                        continue
                    _, label, label_target = min(label_candidates, key=lambda item: item[0])

                    title_candidates = []
                    for title in geometries:
                        if title.handle in {codes.handle, label.handle} or title.handle in used_handles:
                            continue
                        title_target = translated(title.text.strip())
                        if title_target is None or title.layer != label.layer:
                            continue
                        average_height = max((title.height + label.height) / 2.0, 1e-6)
                        vertical_gap = title.y - label.y
                        if not (average_height * 0.8 < vertical_gap <= average_height * 8):
                            continue
                        if abs(title.x - label.x) > average_height:
                            continue
                        if title.height < label.height * 1.15:
                            continue
                        if title.right_edge < codes.x - average_height:
                            continue
                        title_candidates.append((vertical_gap, title, title_target))
                    if not title_candidates:
                        continue
                    _, title, title_target = min(title_candidates, key=lambda item: item[0])

                    title_entity = entities_by_handle.get(title.handle)
                    codes_entity = entities_by_handle.get(codes.handle)
                    if title_entity is None or codes_entity is None:
                        continue
                    lower_height_ratio = min(max(label.height / max(title.height, 1e-6), 0.2), 1.0)
                    space_width = max(estimate_text_width(" ", label.height), label.height * 0.3)
                    target_label_width = estimate_text_width(label_target, label.height)
                    first_gap_width = max(codes.x - label.x - target_label_width, space_width * 2)
                    first_gap = max(2, int(round(first_gap_width / space_width)))
                    code_indent = max(2, int(round((codes.x - label.x) / space_width)))
                    code_anchor_points = entity_anchor_points(codes_entity)
                    code_anchor_y = code_anchor_points[0][1] if code_anchor_points else codes.y
                    code_line_gap = max(codes.height * (5.0 / 3.0), 1e-6)
                    label_row = min(max(
                        int(round((code_anchor_y - label.y) / code_line_gap)),
                        0,
                    ), len(code_lines) - 1)
                    nonbreaking = r"\~"
                    lower_prefix = rf"\H{lower_height_ratio:.4f}x;"
                    lower_lines = []
                    for index, code_line in enumerate(code_lines):
                        if index == label_row:
                            lower_lines.append(
                                label_target + nonbreaking * first_gap + code_line
                            )
                        else:
                            lower_lines.append(nonbreaking * code_indent + code_line)
                    target_lines = [
                        title_target,
                        lower_prefix + lower_lines[0],
                        *lower_lines[1:],
                    ]
                    target_text = r"\P".join(target_lines)
                    source_text = "\n".join([
                        title.text.strip(),
                        label.text.strip(),
                        *code_lines,
                    ])

                    handles = [title.handle, label.handle, codes.handle]
                    handle_set = set(handles)
                    # 若旧任务已提供了覆盖这些实体的普通重建项，以结构化组为准。
                    merged_export_infos[:] = [
                        info for info in merged_export_infos
                        if not handle_set.intersection(info.merged_handles)
                    ]
                    for handle in handles:
                        merged_primary_translations.pop(handle, None)
                    merged_primary_translations[title.handle] = (source_text, target_text)

                    title_right = title.x + max(title.width, 1e-6)
                    visible_codes_right = codes.x + max(
                        estimate_text_width(line, label.height) for line in code_lines
                    )
                    group_width = max(title_right, visible_codes_right) - title.x
                    line_gap = 5.0 / 3.0
                    group_height = (
                        title.height * line_gap
                        + len(code_lines) * label.height * line_gap
                    )
                    raw_color = getattr(title_entity.dxf, "color", 256)
                    raw_true_color = getattr(title_entity.dxf, "true_color", None)
                    raw_transparency = getattr(title_entity.dxf, "transparency", None)
                    merged_export_infos.append(MergedTextExportInfo(
                        source_text=source_text,
                        target_text=target_text,
                        primary_handle=title.handle,
                        merged_handles=handles,
                        primary_x=title.x,
                        primary_y=title.y,
                        primary_height=title.height,
                        primary_style=title.style,
                        primary_color=int(raw_color if raw_color is not None else 256),
                        primary_true_color=(
                            int(raw_true_color) if raw_true_color is not None else None
                        ),
                        primary_transparency=(
                            int(raw_transparency) if raw_transparency is not None else None
                        ),
                        group_x=title.x,
                        group_y_top=title.y + title.height,
                        group_width=max(group_width, title.height),
                        group_height=max(group_height, title.height),
                        preserve_mtext_layout=True,
                        scope=scope,
                        layer=title.layer,
                    ))
                    used_handles.update(handles)
                    logger.info(
                        "结构化管径标注重建 title=%s label=%s codes=%s lines=%d",
                        title.handle,
                        label.handle,
                        codes.handle,
                        len(code_lines),
                    )

                # 中文原图常把同一标注拆成两行多个 TEXT：第一行说明中夹着
                # DN150/(6in)，第二行是“栓口直径 + DN65/(2.5in)”。翻译后
                # 两行自然语言仍应分别翻译，但导出必须合成一个结构化 MTEXT。
                geometry_by_handle = {
                    geometry.handle: geometry for geometry in geometries
                }
                parameter_pattern = re.compile(
                    r"(?:DN|DE|OD|ID)\s*\d+(?:\.\d+)?\s*/?\s*"
                    r"[（(]?\s*\d+(?:\.\d+)?\s*"
                    r"(?:in(?:ch(?:es)?)?|[\"″])\s*[）)]?",
                    re.IGNORECASE,
                )

                def extract_parameters(value: str) -> List[str]:
                    cleaned = clean_mtext(value or "")
                    parameters = []
                    for match in parameter_pattern.finditer(cleaned):
                        parameter = match.group(0)
                        parameter = parameter.replace("（", "(").replace("）", ")")
                        parameters.append(
                            re.sub(r"[\s()/]", "", parameter).casefold()
                        )
                    return parameters

                def extract_parameter(value: str) -> Optional[str]:
                    cleaned = clean_mtext(value or "")
                    match = parameter_pattern.search(cleaned)
                    if match is None:
                        return None
                    parameter = match.group(0)
                    parameter = parameter.replace("（", "(").replace("）", ")")
                    parameter = re.sub(r"\s+", " ", parameter).strip()
                    if cleaned.rstrip().endswith(("。", ".")):
                        parameter = parameter.rstrip(".") + "."
                    return parameter

                for info in list(merged_export_infos):
                    source_parameters = set(extract_parameters(info.source_text))
                    target_parameters = set(extract_parameters(info.target_text))
                    if (
                        len(source_parameters) >= 2
                        and source_parameters.issubset(target_parameters)
                    ):
                        # 新导入已在翻译前把标题、字段和参数合成完整句；其译文
                        # 已带齐全部参数，应由普通单一 MTEXT 路径直接消费。旧的
                        # 结构化回退若再次补参，会造成 DN 参数重复和文字重叠。
                        continue
                    if (
                        info.scope != scope
                        or info.preserve_mtext_layout
                        or used_handles.intersection(info.merged_handles)
                        or len(info.merged_handles) < 2
                    ):
                        continue
                    title_members = [
                        geometry_by_handle[handle]
                        for handle in info.merged_handles
                        if handle in geometry_by_handle
                    ]
                    if len(title_members) != len(info.merged_handles):
                        continue
                    maximum_height = max(
                        (member.height for member in title_members), default=1e-6
                    )
                    if (
                        max(member.y for member in title_members)
                        - min(member.y for member in title_members)
                        > maximum_height * 0.8
                    ):
                        # 已经覆盖多行的普通说明组不属于该特殊结构。
                        continue
                    first_parameter = extract_parameter(info.source_text)
                    if first_parameter is None:
                        continue
                    title = geometry_by_handle.get(info.primary_handle)
                    if title is None:
                        continue

                    label_candidates = []
                    title_handles = set(info.merged_handles)
                    for label in geometries:
                        if label.handle in title_handles or label.handle in used_handles:
                            continue
                        label_target = translated(label.text.strip())
                        if label_target is None or label.layer != info.layer:
                            continue
                        average_height = max((title.height + label.height) / 2.0, 1e-6)
                        vertical_gap = title.y - label.y
                        if not (average_height * 0.8 < vertical_gap <= average_height * 3.0):
                            continue
                        if abs(label.x - title.x) > average_height:
                            continue

                        row_members = [
                            candidate for candidate in geometries
                            if candidate.layer == label.layer
                            and candidate.handle not in title_handles
                            and abs(candidate.y - label.y)
                            <= max(candidate.height, label.height) * 0.8
                            and label.x - average_height * 2
                            <= candidate.x
                            <= max(title.right_edge, info.group_x + info.group_width)
                            + average_height * 2
                        ]
                        row_members.sort(key=lambda candidate: candidate.x)
                        row_source = "".join(
                            candidate.text.strip() for candidate in row_members
                        )
                        second_parameter = extract_parameter(row_source)
                        if second_parameter is None:
                            continue
                        if not any(
                            _is_cad_diameter_block(candidate.text)
                            for candidate in row_members
                        ):
                            continue
                        label_candidates.append((
                            vertical_gap,
                            label,
                            label_target,
                            row_members,
                            row_source,
                            second_parameter,
                        ))
                    if not label_candidates:
                        continue

                    (
                        _, label, label_target, row_members,
                        row_source, second_parameter,
                    ) = min(label_candidates, key=lambda item: item[0])
                    title_entity = entities_by_handle.get(title.handle)
                    if title_entity is None:
                        continue

                    lower_height_ratio = min(
                        max(label.height / max(title.height, 1e-6), 0.3),
                        0.6,
                    )
                    lower_height = title.height * lower_height_ratio
                    lower_space_width = max(
                        estimate_text_width(" ", lower_height),
                        lower_height * 0.3,
                    )
                    label_width = estimate_text_width(label_target, lower_height)
                    code_indent = max(2, int(math.ceil(
                        label_width / lower_space_width
                    )) + 2)
                    nonbreaking = r"\~"
                    lower_prefix = rf"\H{lower_height_ratio:.4f}x;"
                    target_text = r"\P".join([
                        info.target_text,
                        lower_prefix + nonbreaking * code_indent + first_parameter,
                        label_target + nonbreaking * 2 + second_parameter,
                    ])
                    row_handles = [member.handle for member in row_members]
                    handles = list(dict.fromkeys([
                        *info.merged_handles,
                        *row_handles,
                    ]))
                    handle_set = set(handles)
                    merged_export_infos[:] = [
                        existing for existing in merged_export_infos
                        if not handle_set.intersection(existing.merged_handles)
                    ]
                    for handle in handles:
                        merged_primary_translations.pop(handle, None)
                    source_text = f"{info.source_text}\n{row_source}".strip()
                    merged_primary_translations[title.handle] = (
                        source_text,
                        target_text,
                    )
                    group_width = max(
                        info.group_width,
                        estimate_text_width(info.target_text, title.height),
                        label_width
                        + lower_space_width * 2
                        + estimate_text_width(second_parameter, lower_height),
                    )
                    group_height = title.height * (5.0 / 3.0) * 3
                    raw_color = getattr(title_entity.dxf, "color", 256)
                    raw_true_color = getattr(title_entity.dxf, "true_color", None)
                    raw_transparency = getattr(title_entity.dxf, "transparency", None)
                    merged_export_infos.append(MergedTextExportInfo(
                        source_text=source_text,
                        target_text=target_text,
                        primary_handle=title.handle,
                        merged_handles=handles,
                        primary_x=title.x,
                        primary_y=title.y,
                        primary_height=title.height,
                        primary_style=info.primary_style or title.style,
                        primary_color=int(
                            raw_color if raw_color is not None else 256
                        ),
                        primary_true_color=(
                            int(raw_true_color)
                            if raw_true_color is not None else None
                        ),
                        primary_transparency=(
                            int(raw_transparency)
                            if raw_transparency is not None else None
                        ),
                        group_x=title.x,
                        group_y_top=title.y + title.height,
                        group_width=max(group_width, title.height),
                        group_height=max(group_height, title.height),
                        preserve_mtext_layout=True,
                        scope=scope,
                        layer=title.layer,
                    ))
                    used_handles.update(handles)
                    logger.info(
                        "两行碎片管径标注重建 title=%s label=%s handles=%d",
                        title.handle,
                        label.handle,
                        len(handles),
                    )

            for layout in doc.layouts:
                scan_space(layout, f"layout:{layout.name}")
            for block in doc.blocks:
                if block.name.lower().startswith(("*model_space", "*paper_space")):
                    continue
                scan_space(block, f"block:{block.name}")

        add_structured_diameter_annotations()

        # 旧数据经常只把“(mm)”识别为 cad_table_cell，而左侧中文标签仍是
        # 普通 TEXT。利用单位节点保存的精确单元格框，反向找到同格可翻译标签，
        # 将两者提升为一个 MTEXT 重建项；找不到标签时不重建单位，保留原 TEXT。
        promoted_infos: List[MergedTextExportInfo] = []
        for info in merged_export_infos:
            source_compact = re.sub(r"\s+", "", info.source_text or "").casefold()
            target_compact = re.sub(r"\s+", "", info.target_text or "").casefold()
            is_unchanged_unit_cell = bool(
                info.cad_table_cell
                and is_unit_fragment(info.source_text)
                and source_compact == target_compact
            )
            if not is_unchanged_unit_cell:
                promoted_infos.append(info)
                continue

            old_primary = info.primary_handle
            if info.group_width <= 0 or info.group_height <= 0:
                merged_primary_translations.pop(old_primary, None)
                continue
            try:
                target_space = doc.modelspace()
                if info.scope.startswith("layout:"):
                    layout_name = info.scope.removeprefix("layout:").split(":insert:", 1)[0]
                    target_space = doc.layouts.get(layout_name)
                elif info.scope.startswith("block:"):
                    block_name = info.scope.removeprefix("block:").split(":insert:", 1)[0]
                    target_space = doc.blocks.get(block_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "纯单位单元格标签定位失败 scope=%s: %s", info.scope, exc,
                )
                merged_primary_translations.pop(old_primary, None)
                continue

            left = info.group_x
            right = info.group_x + info.group_width
            top = info.group_y_top
            bottom = info.group_y_top - info.group_height
            tolerance = max(info.primary_height * 0.35, 1e-6)
            pending = list(target_space)
            unit_handles: List[str] = []
            label_candidates: List[Tuple[float, object, Tuple[float, float], str, str]] = []

            while pending:
                entity = pending.pop()
                if entity.dxftype() == "INSERT":
                    pending.extend(list(entity.attribs))
                    continue
                if entity.dxftype() not in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                    continue
                handle = str(getattr(entity.dxf, "handle", "") or "")
                text = entity_text(entity).strip()
                if not handle or not text:
                    continue
                matching_point = next((
                    (x, y) for x, y in entity_anchor_points(entity)
                    if left - tolerance <= x <= right + tolerance
                    and bottom - tolerance <= y <= top + tolerance
                ), None)
                if matching_point is None:
                    continue
                if is_unit_fragment(text):
                    unit_handles.append(handle)
                    continue
                if str(getattr(entity.dxf, "layer", "") or "") != info.layer:
                    continue

                translated = self._lookup(text, normalized)
                if translated is None:
                    translated = self._merge_sentence_translations(text, normalized)
                if not translated or translated == text:
                    continue

                x, y = matching_point
                entity_height = max(float(
                    getattr(entity.dxf, "height", 0)
                    or getattr(entity.dxf, "char_height", 0)
                    or info.primary_height
                ), 1e-6)
                if abs(y - info.primary_y) > max(entity_height, info.primary_height) * 0.9:
                    continue
                if x > info.primary_x + tolerance:
                    continue
                score = abs(y - info.primary_y) * 10.0 + abs(x - info.primary_x)
                label_candidates.append((score, entity, matching_point, text, translated))

            if not label_candidates:
                merged_primary_translations.pop(old_primary, None)
                logger.info(
                    "跳过未变化纯单位的独立 MTEXT 重建 primary=%s text=%r",
                    old_primary,
                    info.source_text,
                )
                continue

            _, label_entity, label_point, label_text, translated_label = min(
                label_candidates, key=lambda item: item[0]
            )
            label_handle = str(getattr(label_entity.dxf, "handle", "") or "")
            unit_text = info.source_text.strip()

            def join_unit_text(value: str, fragment: str) -> str:
                value = value.rstrip()
                if fragment.startswith(("/", ")", "]")) or value.endswith(("(", "[", "/", "·")):
                    return value + fragment
                return f"{value} {fragment}"

            info.source_text = join_unit_text(label_text, unit_text)
            info.target_text = join_unit_text(translated_label, unit_text)
            info.primary_handle = label_handle
            info.merged_handles = list(dict.fromkeys([
                label_handle,
                *unit_handles,
                *info.merged_handles,
            ]))
            info.primary_x, info.primary_y = label_point
            info.primary_height = max(float(
                getattr(label_entity.dxf, "height", 0)
                or getattr(label_entity.dxf, "char_height", 0)
                or info.primary_height
            ), 1e-6)
            info.primary_style = str(getattr(label_entity.dxf, "style", "") or "")
            info.primary_color = int(getattr(label_entity.dxf, "color", 256) or 256)
            raw_true_color = getattr(label_entity.dxf, "true_color", None)
            info.primary_true_color = int(raw_true_color) if raw_true_color is not None else None
            raw_transparency = getattr(label_entity.dxf, "transparency", None)
            info.primary_transparency = (
                int(raw_transparency) if raw_transparency is not None else None
            )
            info.layer = str(getattr(label_entity.dxf, "layer", info.layer) or info.layer)

            merged_primary_translations.pop(old_primary, None)
            merged_primary_translations[label_handle] = (
                info.source_text,
                info.target_text,
            )
            promoted_infos.append(info)
            logger.info(
                "表格标签单位提升为单一 MTEXT label=%s units=%s target=%r",
                label_handle,
                unit_handles,
                info.target_text,
            )

        merged_export_infos = promoted_infos

        # 旧任务可能已经把图例格中的“平面：/系统：”保存成一个合并句段。
        # 发现多个独立短冒号标签时，按原 handle 分别回写，避免新建一个跨符号 MTEXT。
        independent_label_translations: Dict[str, Tuple[str, str]] = {}
        independent_info_ids: Set[int] = set()

        # 块定义中的普通 TEXT handle 会被所有 INSERT 实例共享。仅允许递归
        # 发现只插入一次的块；复用块必须走原块定义回写，不能为单个实例把共享
        # 子实体加入清空集合。
        block_insert_counts: Dict[str, int] = {}
        for container in [*list(doc.layouts), *list(doc.blocks)]:
            for candidate in container:
                if candidate.dxftype() != "INSERT":
                    continue
                block_name = str(getattr(candidate.dxf, "name", "") or "")
                if block_name:
                    block_insert_counts[block_name] = block_insert_counts.get(block_name, 0) + 1

        for info in merged_export_infos:
            if not (info.group_width > 0 and info.group_height > 0):
                continue

            try:
                target_space = doc.modelspace()
                if info.scope.startswith("layout:"):
                    layout_name = info.scope.removeprefix("layout:").split(":insert:", 1)[0]
                    target_space = doc.layouts.get(layout_name)
                elif info.scope.startswith("block:"):
                    block_name = info.scope.removeprefix("block:").split(":insert:", 1)[0]
                    target_space = doc.blocks.get(block_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "文本框实体重定位失败 scope=%s: %s", info.scope, exc,
                )
                continue

            left = info.group_x
            right = info.group_x + info.group_width
            top = info.group_y_top
            bottom = info.group_y_top - info.group_height
            tolerance = max(info.primary_height * 0.25, 1e-6)
            source_compact = re.sub(r"\s+", "", clean_mtext(info.source_text or ""))
            pending = [(entity, None, 0) for entity in target_space]
            discovered_members: List[Tuple[float, float, str, str]] = []
            discovered_source_members: List[Tuple[float, float, str, str]] = []
            discovered_legend_members: List[Tuple[float, float, str, str]] = []
            discovered_units: List[Tuple[float, str, str]] = []
            discovered_prefixes: List[Tuple[float, float, str, str]] = []
            discovered_references: List[Tuple[float, float, str, str]] = []
            discovered_punctuation: List[Tuple[float, float, str, str]] = []
            discovered_table_codes: List[Tuple[float, float, str, str]] = []

            while pending:
                entity, transform, depth = pending.pop()
                if entity.dxftype() == "INSERT":
                    # ATTRIB 的坐标已经包含当前 INSERT 变换；嵌套 INSERT 中只需
                    # 再应用父块变换。普通块内 TEXT 则要应用完整 INSERT 变换。
                    pending.extend((attrib, transform, depth + 1) for attrib in entity.attribs)
                    if depth < 10:
                        try:
                            block = doc.blocks.get(str(getattr(entity.dxf, "name", "") or ""))
                        except Exception:  # noqa: BLE001
                            block = None
                        if (
                            block is not None
                            and block_insert_counts.get(block.name, 0) <= 1
                        ):
                            child_transform = insert_transform(entity, transform)
                            pending.extend(
                                (child, child_transform, depth + 1) for child in block
                            )
                        elif block is not None:
                            logger.debug(
                                "跳过复用块共享子实体发现 block=%s refs=%d",
                                block.name,
                                block_insert_counts.get(block.name, 0),
                            )
                    continue
                if entity.dxftype() not in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                    continue

                handle = str(getattr(entity.dxf, "handle", "") or "")
                text = entity_text(entity).strip()
                if not handle or not text:
                    continue

                anchors = entity_anchor_points(entity, transform)
                matching_point = next((
                    (x, y) for x, y in anchors
                    if left - tolerance <= x <= right + tolerance
                    and bottom - tolerance <= y <= top + tolerance
                ), None)
                if matching_point is None:
                    continue

                text_compact = re.sub(r"\s+", "", clean_mtext(text))
                legend_label = text_compact.rstrip(":：")
                if (
                    handle in info.merged_handles
                    and legend_label in {"平面", "系统"}
                ):
                    discovered_legend_members.append((
                        matching_point[0],
                        matching_point[1],
                        text,
                        handle,
                    ))
                source_member = bool(text_compact and text_compact in source_compact)
                unit_member = bool(info.cad_table_cell and is_unit_fragment(text))
                prefix_member = bool(
                    not info.cad_table_cell and list_number_re.fullmatch(text)
                )
                reference_member = bool(
                    not info.cad_table_cell and table_reference_re.fullmatch(text)
                )
                punctuation_member = bool(
                    not info.cad_table_cell and punctuation_fragment_re.fullmatch(text)
                )
                table_code_member = bool(
                    info.cad_table_cell
                    and table_code_fragment_re.fullmatch(clean_mtext(text).strip())
                )
                if not (
                    source_member
                    or unit_member
                    or prefix_member
                    or reference_member
                    or punctuation_member
                    or table_code_member
                ):
                    continue

                if handle not in info.merged_handles:
                    info.merged_handles.append(handle)
                matching_x, matching_y = matching_point
                discovered_members.append((matching_x, matching_y, text, handle))
                if source_member:
                    discovered_source_members.append((matching_x, matching_y, text, handle))
                if unit_member:
                    discovered_units.append((matching_x, text, handle))
                if prefix_member:
                    discovered_prefixes.append((matching_x, matching_y, text, handle))
                if reference_member:
                    discovered_references.append((matching_x, matching_y, text, handle))
                if punctuation_member:
                    discovered_punctuation.append((matching_x, matching_y, text, handle))
                if table_code_member:
                    discovered_table_codes.append((matching_x, matching_y, text, handle))

            colon_members = sorted(
                [
                    item for item in discovered_source_members
                    if len(clean_mtext(item[2]).strip()) <= 32
                    and clean_mtext(item[2]).strip().endswith((":", "："))
                ],
                key=lambda item: (-item[1], item[0]),
            )
            other_source_members = [
                item for item in discovered_source_members
                if item not in colon_members
                and not diameter_code_re.fullmatch(clean_mtext(item[2]).strip())
                and not table_code_fragment_re.fullmatch(clean_mtext(item[2]).strip())
            ]
            label_target_text = re.sub(
                r"^\s*(?:DN|DE|OD|ID)\s*\d+(?:\.\d+)?\s*",
                "",
                clean_mtext(info.target_text or ""),
                flags=re.IGNORECASE,
            )
            target_label_parts = [
                part.strip()
                for part in re.findall(r"[^:：\r\n]+[:：]", label_target_text)
                if part.strip()
            ]

            # 早期任务把无冒号的“平面/系统”跨过中间图形符号合成了一个
            # 句段。只对这个已知图例对及其明显大间距形态做兼容拆分，避免
            # 把普通的相邻短词误判为两个标签。
            legend_members = sorted(
                discovered_legend_members,
                key=lambda item: (-item[1], item[0]),
            )
            legend_labels = [
                re.sub(r"\s+", "", clean_mtext(item[2])).rstrip(":：")
                for item in legend_members
            ]
            is_independent_plan_system_pair = bool(
                len(legend_members) == 2
                and legend_labels == ["平面", "系统"]
                and abs(legend_members[0][1] - legend_members[1][1])
                <= max(info.primary_height * 0.9, 1e-6)
                and legend_members[1][0] - legend_members[0][0]
                >= max(info.primary_height * 4.0, 1e-6)
            )
            if is_independent_plan_system_pair:
                translated_legend_members: List[Tuple[str, str, str]] = []
                for _, _, source_label, handle in legend_members:
                    translated_label = self._lookup(source_label, normalized)
                    if translated_label is None:
                        translated_label = self._lookup(
                            clean_mtext(source_label).strip().rstrip(":："),
                            normalized,
                        )
                    if not translated_label or translated_label == source_label:
                        translated_legend_members = []
                        break
                    translated_legend_members.append((
                        handle,
                        source_label,
                        translated_label,
                    ))

                if len(translated_legend_members) == len(legend_members):
                    for handle, source_label, translated_label in translated_legend_members:
                        independent_label_translations[handle] = (
                            source_label,
                            translated_label,
                        )
                    independent_info_ids.add(id(info))
                    merged_primary_translations.pop(info.primary_handle, None)
                    logger.info(
                        "图例平面/系统标签拆分 primary=%s handles=%s targets=%s",
                        info.primary_handle,
                        [item[0] for item in translated_legend_members],
                        [item[2] for item in translated_legend_members],
                    )
                    continue

            if (
                info.cad_table_cell
                and len(colon_members) >= 2
                and not other_source_members
                and len(target_label_parts) >= len(colon_members)
            ):
                for member, translated_label in zip(colon_members, target_label_parts):
                    _, _, source_label, handle = member
                    independent_label_translations[handle] = (
                        source_label,
                        translated_label,
                    )
                independent_info_ids.add(id(info))
                merged_primary_translations.pop(info.primary_handle, None)
                logger.info(
                    "图例格拆分独立标签 primary=%s handles=%s targets=%s",
                    info.primary_handle,
                    [item[3] for item in colon_members],
                    target_label_parts[:len(colon_members)],
                )
                continue

            if discovered_table_codes:
                def table_code_tokens(value: str) -> set[str]:
                    return {
                        token.casefold()
                        for token in re.findall(
                            r"[A-Za-z0-9]+(?:[._:/+#%°×x()-][A-Za-z0-9]+)*",
                            clean_mtext(value or ""),
                        )
                    }

                def missing_row_codes(
                    row: List[Tuple[float, float, str, str]],
                    target_line: str,
                ) -> List[str]:
                    codes = list(dict.fromkeys(
                        clean_mtext(item[2]).strip() for item in row if item[2].strip()
                    ))
                    # 新导入有时会按原 CAD 间距保存成“1X1X3”；整行紧凑串存在时
                    # 说明代码已由翻译保留，不再重复补。单个纯数字必须按完整 token
                    # 判断，避免把代码 1 误判成小数 1.5 的一部分。
                    row_compact = "".join(
                        re.sub(r"[^A-Za-z0-9]", "", code).casefold()
                        for code in codes
                    )
                    line_compact = re.sub(
                        r"[^A-Za-z0-9]", "", clean_mtext(target_line or "")
                    ).casefold()
                    if len(codes) > 1 and row_compact and row_compact in line_compact:
                        return []
                    tokens = table_code_tokens(target_line)
                    return [code for code in codes if code.casefold() not in tokens]

                # 必须先固定全部视觉行，再逐行判断缺失项；不能先过滤再分行，
                # 否则中间某行已包含代码时，后续代码会错配到前一段译文。
                code_rows: List[List[Tuple[float, float, str, str]]] = []
                row_tolerance = max(info.primary_height * 0.8, 1e-6)
                for item in sorted(
                    discovered_table_codes, key=lambda value: (-value[1], value[0])
                ):
                    if not code_rows or abs(item[1] - code_rows[-1][0][1]) > row_tolerance:
                        code_rows.append([item])
                    else:
                        code_rows[-1].append(item)

                target_parts = [
                    part.strip()
                    for part in re.split(r"\\P|\r\n?|\n", info.target_text)
                ]
                added_rows: List[str] = []
                if len(target_parts) >= len(code_rows):
                    for index, row in enumerate(code_rows):
                        missing = missing_row_codes(row, target_parts[index])
                        prefix = " ".join(missing)
                        added_rows.append(prefix)
                        if prefix:
                            target_parts[index] = f"{prefix} {target_parts[index]}".strip()
                    info.target_text = r"\P".join(target_parts)
                else:
                    # 译文把多段压成一段时无法可靠恢复逐段对应；保留原视觉行顺序
                    # 作为独立前缀块，至少确保代码不丢失且不再以旧 TEXT 叠加。
                    added_rows = [
                        " ".join(dict.fromkeys(item[2] for item in row))
                        for row in code_rows
                    ]
                    prefix_block = r"\P".join(prefix for prefix in added_rows if prefix)
                    if prefix_block:
                        info.target_text = f"{prefix_block}\\P{info.target_text}".strip()

                if any(added_rows):
                    logger.info(
                        "表格旧任务补回结构代码 primary=%s rows=%s",
                        info.primary_handle,
                        added_rows,
                    )

            if discovered_prefixes:
                prefixes = list(dict.fromkeys(
                    item[2] for item in sorted(
                        discovered_prefixes, key=lambda item: (-item[1], item[0])
                    )
                ))
                prefix_text = " ".join(prefixes)
                if not info.source_text.lstrip().startswith(prefix_text):
                    info.source_text = f"{prefix_text} {info.source_text}".strip()
                if not info.target_text.lstrip().startswith(prefix_text):
                    info.target_text = f"{prefix_text} {info.target_text}".strip()

            if discovered_references:
                suffix_parts: List[str] = []
                for ref_x, ref_y, ref_text, _ in sorted(
                    discovered_references, key=lambda item: (-item[1], item[0])
                ):
                    suffix = ref_text
                    nearby_punctuation = [
                        item for item in discovered_punctuation
                        if item[0] >= ref_x
                        and abs(item[1] - ref_y) <= max(info.primary_height * 0.8, 1e-6)
                        and item[0] - ref_x <= max(info.primary_height * 4.0, 1e-6)
                    ]
                    if nearby_punctuation:
                        punctuation = min(
                            nearby_punctuation, key=lambda item: item[0] - ref_x
                        )[2]
                        suffix += ":" if punctuation in {"：", ":"} else punctuation
                    suffix_parts.append(suffix)
                suffix_text = " ".join(dict.fromkeys(suffix_parts))
                if suffix_text and suffix_text not in info.source_text:
                    info.source_text = f"{info.source_text} {suffix_text}".strip()
                if suffix_text and suffix_text not in info.target_text:
                    info.target_text = f"{info.target_text} {suffix_text}".strip()

            # 数据库存的是导入时 DXF handle；再次从 DWG 转换时 handle 可能变化。
            # 用框内源文片段的真实 handle 重定位主实体，避免“新 MTEXT 已创建，旧
            # TEXT 又被逐实体翻译”形成截图中的双层重叠。
            discovered_handles = {item[3] for item in discovered_members}
            if discovered_members and info.primary_handle not in discovered_handles:
                old_primary = info.primary_handle
                nearest = min(
                    discovered_members,
                    key=lambda item: (
                        (item[0] - info.primary_x) ** 2
                        + (item[1] - info.primary_y) ** 2
                    ),
                )
                info.primary_handle = nearest[3]
                merged_primary_translations.pop(old_primary, None)
                merged_primary_translations[info.primary_handle] = (
                    info.source_text,
                    info.target_text,
                )
                logger.info(
                    "DXF 合并主实体重定位：%s -> %s text=%r",
                    old_primary,
                    info.primary_handle,
                    nearest[2][:40],
                )

            if discovered_units:
                def contains_fragment(value: str, fragment: str) -> bool:
                    compact_value = re.sub(r"\s+", "", value or "").casefold()
                    compact_fragment = re.sub(r"\s+", "", fragment).casefold()
                    return compact_fragment in compact_value

                def append_fragment(value: str, fragment: str) -> str:
                    value = (value or "").rstrip()
                    if not value:
                        return fragment
                    if fragment.startswith(("/", ")", "]")) or value.endswith(("(", "[", "/", "·")):
                        return value + fragment
                    return f"{value} {fragment}"

                for _, unit_text, _ in sorted(discovered_units, key=lambda item: item[0]):
                    if not contains_fragment(info.source_text, unit_text):
                        info.source_text = append_fragment(info.source_text, unit_text)
                    if not contains_fragment(info.target_text, unit_text):
                        info.target_text = append_fragment(info.target_text, unit_text)

                # MTEXT 创建失败时会回退到主 TEXT 写入，同样必须使用补齐单位后的译文。
                if info.primary_handle in merged_primary_translations:
                    merged_primary_translations[info.primary_handle] = (
                        info.source_text,
                        info.target_text,
                    )

            if info.primary_handle in merged_primary_translations:
                merged_primary_translations[info.primary_handle] = (
                    info.source_text,
                    info.target_text,
                )

            if discovered_members:
                logger.info(
                    "文本框实体重定位 primary=%s members=%s units=%s",
                    info.primary_handle,
                    [item[3] for item in discovered_members],
                    [item[1] for item in discovered_units],
                )

        if independent_info_ids:
            merged_export_infos = [
                info for info in merged_export_infos if id(info) not in independent_info_ids
            ]

        # 旧任务或未识别出闭合框的表头不会进入 merged_text_info。为这类普通
        # TEXT 增加几何兜底：同一行、同一图层且紧邻的“标签 + 工程单位”只
        # 输出一次，把单位并入标签译文并清空原单位实体。
        direct_unit_translations: Dict[str, Tuple[str, str]] = {}
        spatial_handles = {
            str(handle)
            for info in merged_export_infos
            if not info.single_text_block
            for handle in info.merged_handles
        }
        used_unit_handles: Set[str] = set()

        def preferred_text_anchor(entity) -> Optional[Tuple[float, float]]:
            try:
                halign = int(getattr(entity.dxf, "halign", 0) or 0)
                valign = int(getattr(entity.dxf, "valign", 0) or 0)
                if halign != 0 or valign != 0:
                    point = getattr(entity.dxf, "align_point", None)
                    if point is not None:
                        return float(point[0]), float(point[1])
                point = getattr(entity.dxf, "insert", None)
                if point is not None:
                    return float(point[0]), float(point[1])
            except Exception:  # noqa: BLE001
                return None
            return None

        def scan_direct_unit_pairs(space) -> None:
            pending = list(space)
            entries: List[Dict[str, object]] = []
            while pending:
                entity = pending.pop()
                if entity.dxftype() == "INSERT":
                    pending.extend(list(entity.attribs))
                    continue
                if entity.dxftype() not in {"TEXT", "ATTRIB", "ATTDEF"}:
                    continue
                handle = str(getattr(entity.dxf, "handle", "") or "")
                text = entity_text(entity).strip()
                anchor = preferred_text_anchor(entity)
                if not handle or not text or anchor is None:
                    continue
                height = max(float(getattr(entity.dxf, "height", 0) or 0), 1e-6)
                width_factor = float(getattr(entity.dxf, "width", 1.0) or 1.0)
                entries.append({
                    "entity": entity,
                    "handle": handle,
                    "text": text,
                    "x": anchor[0],
                    "y": anchor[1],
                    "height": height,
                    "width": estimate_text_width(text, height, 0.6 * width_factor),
                    "layer": str(getattr(entity.dxf, "layer", "") or ""),
                    "rotation": float(getattr(entity.dxf, "rotation", 0) or 0),
                })

            for primary in entries:
                primary_handle = str(primary["handle"])
                if primary_handle in spatial_handles:
                    continue
                source_text = str(primary["text"])
                target_text = self._lookup(source_text, normalized)
                if target_text is None:
                    target_text = self._merge_sentence_translations(source_text, normalized)
                if not target_text or target_text == source_text:
                    continue

                primary_x = float(primary["x"])
                primary_y = float(primary["y"])
                primary_height = float(primary["height"])
                candidates: List[Tuple[float, Dict[str, object]]] = []
                for candidate in entries:
                    candidate_handle = str(candidate["handle"])
                    if (
                        candidate is primary
                        or candidate_handle in spatial_handles
                        or candidate_handle in used_unit_handles
                        or not is_unit_fragment(str(candidate["text"]))
                        or str(candidate["layer"]) != str(primary["layer"])
                    ):
                        continue
                    average_height = max(
                        (primary_height + float(candidate["height"])) / 2.0,
                        1e-6,
                    )
                    if abs(float(candidate["y"]) - primary_y) > average_height * 0.75:
                        continue
                    rotation_delta = abs(float(candidate["rotation"]) - float(primary["rotation"])) % 360
                    rotation_delta = min(rotation_delta, 360 - rotation_delta)
                    if rotation_delta > 2.0:
                        continue
                    x_delta = float(candidate["x"]) - primary_x
                    if x_delta <= average_height * 0.1:
                        continue
                    max_distance = max(
                        float(primary["width"]) + average_height * 2.0,
                        average_height * 8.0,
                    )
                    if x_delta <= max_distance:
                        candidates.append((x_delta, candidate))

                if not candidates:
                    continue
                _, unit = min(candidates, key=lambda item: item[0])
                unit_handle = str(unit["handle"])
                unit_text = str(unit["text"])

                def join_unit(value: str, fragment: str) -> str:
                    value = value.rstrip()
                    if fragment.startswith(("/", ")", "]")) or value.endswith(("(", "[", "/", "·")):
                        return value + fragment
                    return f"{value} {fragment}"

                compact_target = re.sub(r"\s+", "", target_text).casefold()
                compact_unit = re.sub(r"\s+", "", unit_text).casefold()
                combined_target = (
                    target_text
                    if compact_unit in compact_target
                    else join_unit(target_text, unit_text)
                )
                direct_unit_translations[primary_handle] = (
                    join_unit(source_text, unit_text),
                    combined_target,
                )
                used_unit_handles.add(unit_handle)
                merged_handles_to_clear.add(unit_handle)
                logger.info(
                    "普通 TEXT 标签单位合并 primary=%s unit=%s target=%r",
                    primary_handle,
                    unit_handle,
                    combined_target,
                )

        for layout in doc.layouts:
            scan_direct_unit_pairs(layout)
        for block in doc.blocks:
            if block.name.lower().startswith(("*model_space", "*paper_space")):
                continue
            scan_direct_unit_pairs(block)

        # 单实体文本块若同时命中既有“标签 + 单位”兜底，应由该路径统一写回；
        # 否则后续单独重建 MTEXT 会先消费标签，再把单位清空，造成单位丢失。
        if direct_unit_translations:
            direct_unit_handles = set(direct_unit_translations)
            removed_single_blocks = {
                info.primary_handle
                for info in merged_export_infos
                if info.single_text_block
                and info.primary_handle in direct_unit_handles
            }
            if removed_single_blocks:
                merged_export_infos = [
                    info
                    for info in merged_export_infos
                    if info.primary_handle not in removed_single_blocks
                ]
                for handle in removed_single_blocks:
                    merged_primary_translations.pop(handle, None)
                logger.info(
                    "单文本块让位于标签单位兜底 handles=%s",
                    sorted(removed_single_blocks),
                )

        # 旧任务或外部分组可能错误地把实例 ATTRIB 与块定义 TEXT/ATTDEF
        # 混在一个句段。此时既不能把译文写入共享块，也不能在失败回退时
        # 清空共享成员；整组退出空间重建，让各实体保持原状是唯一安全选择。
        safe_export_infos: List[MergedTextExportInfo] = []
        for info in merged_export_infos:
            member_types = {
                entity.dxftype()
                for handle in info.merged_handles
                if (entity := doc.entitydb.get(handle)) is not None
            }
            has_mixed_instance_attrib = bool(
                "ATTRIB" in member_types
                and any(entity_type != "ATTRIB" for entity_type in member_types)
            )
            if has_mixed_instance_attrib:
                merged_primary_translations.pop(info.primary_handle, None)
                logger.warning(
                    "跳过 ATTRIB/块定义混合重建 primary=%s types=%s handles=%s",
                    info.primary_handle,
                    sorted(member_types),
                    info.merged_handles,
                )
                continue
            safe_export_infos.append(info)
        merged_export_infos = safe_export_infos

        # 所有实体仍保持原图几何时统一建立硬矩形；后续创建/清空实体后再扫描会
        # 丢失“下一独立文本块”参照。普通 MTEXT 和重建 MTEXT 分别保存约束。
        self._inject_mtext_max_heights(doc, normalized)
        self._assign_merged_mtext_max_heights(doc, merged_export_infos)

        # 先创建新实体；只有创建成功的组才清空主实体，失败组沿用旧写回方式。
        recreated_primary_handles = self._create_merged_mtext_entities(
            doc,
            merged_export_infos,
            opts,
            unicode_style_name=unicode_style_name,
        )
        for info in merged_export_infos:
            if info.primary_handle in recreated_primary_handles:
                merged_handles_to_clear.update(info.merged_handles)
                merged_primary_translations.pop(info.primary_handle, None)
            else:
                merged_handles_to_clear.update(
                    handle for handle in info.merged_handles if handle != info.primary_handle
                )

        logger.info(
            "DXF 导出：MTEXT 重建=%d，回退主实体写入=%d，清空实体=%d",
            len(recreated_primary_handles),
            len(merged_primary_translations),
            len(merged_handles_to_clear),
        )

        # 启用 audit 时收集每条实体的命中明细；默认关闭，零开销保留老行为
        audit_records: Optional[list[dict]] = [] if audit_path else None

        seen_handles: set[str] = set()
        stats = {"total": 0, "hit": 0, "miss": 0, "merged_cleared": 0, "merged_written": 0}

        def visit(entities: Iterable) -> None:
            for entity in entities:
                handle = getattr(entity.dxf, "handle", None)
                if handle and handle in seen_handles:
                    continue
                if handle:
                    seen_handles.add(handle)

                # INSERT 的 ATTRIB 不会作为 layout 顶层实体出现。必须递归走同一套
                # 拆段/清空/重建判断，不能交给 _replace_in_entity 直接翻译，否则
                # 新建 MTEXT 与未清空的属性文字会叠在同一单元格里。
                if entity.dxftype() == "INSERT":
                    visit(entity.attribs)
                    continue

                # MTEXT 拆段：旧项目保存的是错误上移后的坐标，先依据原 MTEXT
                # 的 insert、宽度和 \P 索引重建布局，再清空原实体并创建独立段落。
                if handle and handle in mtext_split_by_parent:
                    splits = self._normalize_split_mtext_layout(
                        entity, mtext_split_by_parent[handle]
                    )
                    scope = str(splits[0].get("scope") or "") if splits else ""
                    requires_in_place = self._mtext_requires_in_place_preservation(
                        entity, scope
                    )
                    if requires_in_place:
                        if self._write_split_mtext_in_place(
                            entity,
                            splits,
                            opts,
                            stats,
                            audit_records,
                            unicode_style_name=unicode_style_name,
                        ):
                            logger.info(
                                "DXF 导出：拆段 MTEXT 原位回写 handle=%s，"
                                "保留旋转/对齐/块变换",
                                handle,
                            )
                        else:
                            # 方向敏感实体必须 fail closed：索引损坏时宁可保留
                            # 原文，也不能清空后创建默认方向的 MTEXT。
                            logger.warning(
                                "DXF 导出：拆段 MTEXT 原位回写失败，保留原实体 "
                                "handle=%s scope=%s",
                                handle,
                                scope,
                            )
                        continue
                    logger.info(
                        "DXF 导出：拆段 MTEXT handle=%s，拆为 %d 个独立段",
                        handle, len(splits),
                    )
                    self._clear_entity_text(entity, stats, audit_records)
                    self._create_split_mtext_entities(
                        doc,
                        splits,
                        opts,
                        stats,
                        unicode_style_name=unicode_style_name,
                    )
                    continue

                # 旧任务中的图例短标签按原 handle 分别回写，保留符号两侧位置。
                if handle and handle in independent_label_translations:
                    source_text, target_text = independent_label_translations[handle]
                    self._write_merged_translation(
                        entity,
                        source_text,
                        target_text,
                        opts,
                        stats,
                        audit_records,
                        unicode_style_name=unicode_style_name,
                    )
                    continue

                # 检查是否是被合并的实体，如果是则清空文本
                if handle and handle in merged_handles_to_clear:
                    logger.info("DXF 导出：发现需要清空的实体 handle=%s", handle)
                    self._clear_entity_text(entity, stats, audit_records)
                    continue

                # 检查是否是主实体（需要写入合并后的译文）
                if handle and handle in merged_primary_translations:
                    logger.info("DXF 导出：发现主实体 handle=%s，写入合并译文", handle)
                    source_text, target_text = merged_primary_translations[handle]
                    self._write_merged_translation(
                        entity, 
                        source_text,
                        target_text,
                        opts,
                        stats, 
                        audit_records,
                        unicode_style_name=unicode_style_name,
                    )
                    continue

                # 未进入空间重建的旧表头：把标签和单位写入同一个 TEXT，单位实体
                # 已在上面的几何扫描中加入 merged_handles_to_clear。
                if handle and handle in direct_unit_translations:
                    source_text, target_text = direct_unit_translations[handle]
                    self._write_merged_translation(
                        entity,
                        source_text,
                        target_text,
                        opts,
                        stats,
                        audit_records,
                        unicode_style_name=unicode_style_name,
                    )
                    continue

                self._replace_in_entity(
                    entity, normalized, opts, stats, audit_records,
                    unicode_style_name=unicode_style_name,
                )

        for layout in doc.layouts:
            visit(layout)
        for block in doc.blocks:
            name = block.name
            if name.lower().startswith(("*model_space", "*paper_space")):
                continue
            visit(block)

        # 翻译后的短表号（如“表8-3”）通常仍沿用英文/纯数字实体的锚点。
        # CJK 字形的实际下沿更低，可能穿过紧邻的表格顶边。所有回写完成后再按
        # 最终字号和文本内容做一次窄范围碰撞校正：只处理明确带“表/Table”等
        # 前缀的短表号，且仅在它与水平线真实相交时向上平移。
        table_reference_label_re = re.compile(
            r"^(?:表|table|tabla|tabelle|tableau|tabella|tabela|таблица)\s*"
            r"\d+(?:-\d+)+\s*[:：]?$",
            re.IGNORECASE,
        )
        table_reference_token_re = re.compile(
            r"(?:表|table|tabla|tabelle|tableau|tabella|tabela|таблица)\s*"
            r"\d+(?:-\d+)+\s*[:：]?",
            re.IGNORECASE,
        )
        # 只移动本次翻译/重建实际生成的表号；原图中已有但未参与翻译的短表号
        # 不应因一次无关导出而改变位置。
        translated_table_reference_labels: Set[str] = set()
        translated_values = [str(value) for value in normalized.values()]
        translated_values.extend(info.target_text for info in merged_export_infos)
        translated_values.extend(
            str(item.get("target_text") or "")
            for items in mtext_split_by_parent.values()
            for item in items
        )
        for translated_value in translated_values:
            cleaned_value = clean_mtext(translated_value or "")
            translated_table_reference_labels.update(
                match.group(0).strip()
                for match in table_reference_token_re.finditer(cleaned_value)
            )

        def horizontal_segments(
            entities: Iterable,
        ) -> List[Tuple[float, float, float, float]]:
            segments: List[Tuple[float, float, float, float]] = []
            for candidate in entities:
                kind = candidate.dxftype()
                try:
                    if kind == "LINE":
                        start = candidate.dxf.start
                        end = candidate.dxf.end
                        points = [
                            (float(start[0]), float(start[1])),
                            (float(end[0]), float(end[1])),
                        ]
                    elif kind == "LWPOLYLINE":
                        points = [
                            (float(point[0]), float(point[1]))
                            for point in candidate.get_points("xy")
                        ]
                        if getattr(candidate, "closed", False) and len(points) > 2:
                            points.append(points[0])
                    elif kind == "POLYLINE":
                        points = [
                            (
                                float(vertex.dxf.location[0]),
                                float(vertex.dxf.location[1]),
                            )
                            for vertex in candidate.vertices
                        ]
                        if getattr(candidate, "is_closed", False) and len(points) > 2:
                            points.append(points[0])
                    else:
                        continue
                except Exception:  # noqa: BLE001 - 容忍损坏或代理几何
                    continue

                for (x1, y1), (x2, y2) in zip(points, points[1:]):
                    if abs(x2 - x1) <= 1e-6:
                        continue
                    if abs(y2 - y1) > max(abs(x2 - x1) * 0.02, 1e-6):
                        continue
                    if x1 <= x2:
                        segments.append((x1, y1, x2, y2))
                    else:
                        segments.append((x2, y2, x1, y1))
            return segments

        def table_reference_bounds(entity) -> Optional[Tuple[float, float, float, float, float]]:
            kind = entity.dxftype()
            if kind not in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                return None
            text = entity_text(entity).strip()
            if (
                not table_reference_label_re.fullmatch(text)
                or text not in translated_table_reference_labels
            ):
                return None

            try:
                rotation = float(getattr(entity.dxf, "rotation", 0) or 0) % 180.0
                if min(rotation, 180.0 - rotation) > 5.0:
                    return None

                if kind == "MTEXT":
                    height = max(float(getattr(entity.dxf, "char_height", 0) or 0), 1e-6)
                    direction = getattr(entity.dxf, "text_direction", None)
                    if direction is not None:
                        direction_x = float(direction[0])
                        direction_y = float(direction[1])
                        if abs(direction_y) > max(abs(direction_x) * 0.1, 1e-6):
                            return None
                    insert = entity.dxf.insert
                    anchor_x, anchor_y = float(insert[0]), float(insert[1])
                    attachment = int(getattr(entity.dxf, "attachment_point", 1) or 1)
                    # 表号 MTEXT 已完成最终字体与字号拟合，优先使用真实字形 bbox；
                    # 手工按一个字高估计会低估 SHX/CJK 下沿，导致表号仍贴入表头。
                    try:
                        from ezdxf import bbox as ezdxf_bbox

                        extents = ezdxf_bbox.extents([entity], fast=True)
                        if extents.has_data and float(extents.size.y) > 0:
                            return (
                                float(extents.extmin.x),
                                float(extents.extmax.x),
                                float(extents.extmin.y),
                                float(extents.extmax.y),
                                height,
                            )
                    except Exception:  # noqa: BLE001
                        pass
                    lines = [
                        line for line in re.split(r"\\P|\r\n?|\n", clean_mtext(entity.text or ""))
                        if line.strip()
                    ] or [text]
                    visible_width = max(
                        estimate_text_width(line, height) for line in lines
                    )
                    # 单行 MTEXT 的字形占高约为一个字高；5/3 只用于相邻
                    # 基线间距，不能把它算进单行下沿，否则会误判已有安全间距。
                    occupied_height = height + max(len(lines) - 1, 0) * height * (5.0 / 3.0)
                    column = (attachment - 1) % 3
                    if column == 1:
                        left = anchor_x - visible_width / 2.0
                    elif column == 2:
                        left = anchor_x - visible_width
                    else:
                        left = anchor_x
                    row = (attachment - 1) // 3
                    if row == 0:
                        bottom = anchor_y - occupied_height
                    elif row == 1:
                        bottom = anchor_y - occupied_height / 2.0
                    else:
                        bottom = anchor_y
                    return left, left + visible_width, bottom, bottom + occupied_height, height

                height = max(float(getattr(entity.dxf, "height", 0) or 0), 1e-6)
                halign = int(getattr(entity.dxf, "halign", 0) or 0)
                valign = int(getattr(entity.dxf, "valign", 0) or 0)
                # ALIGNED/FIT TEXT 由 insert 与 align_point 两点共同决定缩放和方向，
                # 不能按单锚点估算矩形；保守跳过，避免错误平移。
                if halign in {3, 5}:
                    return None
                point = (
                    getattr(entity.dxf, "align_point", None)
                    if halign != 0 or valign != 0
                    else None
                ) or getattr(entity.dxf, "insert", None)
                if point is None:
                    return None
                anchor_x, anchor_y = float(point[0]), float(point[1])
                width_factor = max(float(getattr(entity.dxf, "width", 1.0) or 1.0), 0.05)
                visible_width = estimate_text_width(text, height, 0.6 * width_factor)
                if halign in {1, 4}:
                    left = anchor_x - visible_width / 2.0
                elif halign == 2:
                    left = anchor_x - visible_width
                else:
                    left = anchor_x
                if valign == 3:
                    bottom = anchor_y - height
                elif valign == 2:
                    bottom = anchor_y - height / 2.0
                else:
                    bottom = anchor_y
                return left, left + visible_width, bottom, bottom + height, height
            except Exception:  # noqa: BLE001 - 非标准对齐属性直接跳过
                return None

        def move_text_up(entity, offset_y: float) -> None:
            for attribute in ("insert", "align_point"):
                try:
                    point = getattr(entity.dxf, attribute, None)
                    if point is None:
                        continue
                    z = float(point[2]) if len(point) > 2 else 0.0
                    setattr(
                        entity.dxf,
                        attribute,
                        (float(point[0]), float(point[1]) + offset_y, z),
                    )
                except Exception:  # noqa: BLE001 - 部分实体不支持 align_point
                    continue

        adjusted_table_references = 0

        def adjust_table_references(space: Iterable) -> None:
            nonlocal adjusted_table_references
            entities = list(space)
            segments = horizontal_segments(entities)
            if not segments:
                return

            text_entities = []
            for entity in entities:
                if entity.dxftype() == "INSERT":
                    text_entities.extend(list(entity.attribs))
                elif entity.dxftype() in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                    text_entities.append(entity)

            for entity in text_entities:
                bounds = table_reference_bounds(entity)
                if bounds is None:
                    continue
                left, right, bottom, top, height = bounds
                margin = max(height * 0.25, 1e-6)
                blocking_lines: List[float] = []
                for line_left, left_y, line_right, right_y in segments:
                    overlap_left = max(left, line_left)
                    overlap_right = min(right, line_right)
                    if overlap_right - overlap_left <= 1e-6:
                        continue
                    sample_x = (overlap_left + overlap_right) / 2.0
                    ratio = (sample_x - line_left) / max(line_right - line_left, 1e-6)
                    line_y = left_y + (right_y - left_y) * ratio
                    if bottom - margin <= line_y <= top:
                        blocking_lines.append(line_y)
                if not blocking_lines:
                    continue
                blocking_y = max(blocking_lines)
                offset_y = blocking_y + margin - bottom
                if offset_y <= 1e-6:
                    continue
                move_text_up(entity, offset_y)
                adjusted_table_references += 1
                logger.info(
                    "表号避让水平线 handle=%s text=%r dy=%.4f",
                    getattr(entity.dxf, "handle", ""),
                    entity_text(entity).strip(),
                    offset_y,
                )

        for layout in doc.layouts:
            adjust_table_references(layout)
        for block in doc.blocks:
            if block.name.lower().startswith(("*model_space", "*paper_space")):
                continue
            adjust_table_references(block)

        if adjusted_table_references:
            logger.info("DXF 表号碰撞校正：上移 %d 个短表号", adjusted_table_references)

        logger.info(
            "DXF 回写：实体 %d，命中 %d，漏匹配 %d，合并主实体写入 %d，合并清空 %d",
            stats["total"],
            stats["hit"],
            stats["miss"],
            stats.get("merged_written", 0),
            stats.get("merged_cleared", 0),
        )

        if audit_path and audit_records is not None:
            self._dump_export_audit(audit_path, audit_records, normalized, stats)

        return self._write_doc(doc)

    @staticmethod
    def _dump_export_audit(
        path: str,
        records: list[dict],
        translations: Dict[str, str],
        stats: dict,
    ) -> None:
        """把 export 阶段的命中明细写成 JSON，便于离线分析。"""
        import json
        import re as _re

        # 哪些 translations 的 source 一次也没在 DXF 里出现过
        seen_sources = {r["source"] for r in records if r.get("source")}
        unused_translations = [
            {"source": s, "target": t}
            for s, t in translations.items()
            if s not in seen_sources and _re.search(r"[\u4e00-\u9fff]", s or "")
        ]

        # 哪些含中文文本在 DXF 里没找到译文（最关键的"漏译"列表）
        zh_missing = [
            r for r in records
            if r["status"] == "miss" and _re.search(r"[\u4e00-\u9fff]", r.get("source", ""))
        ]

        summary = {
            "entities_total": stats["total"],
            "entities_hit": stats["hit"],
            "entities_miss": stats["miss"],
            "translations_size": len(translations),
            "translations_unused": len(unused_translations),
            "zh_source_missing_translation": len(zh_missing),
        }

        try:
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(
                    {
                        "summary": summary,
                        "zh_missing_translation": zh_missing,
                        "unused_translations": unused_translations,
                        "records": records,
                    },
                    fp,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.info("DXF export 审计写出：%s", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DXF export 审计写出失败 %s: %s", path, exc)

    @staticmethod
    def _read_doc(raw_bytes: bytes):
        import ezdxf
        from ezdxf import recover
        from ezdxf.lldxf.const import DXFError

        for encoding in ("utf-8", "utf-8-sig", "cp1252", "gb18030", "iso-8859-1"):
            try:
                text = raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
            stream = io.BytesIO(text.encode("utf-8"))
            try:
                doc, _ = recover.read(stream)
                return doc
            except DXFError:
                pass
            except Exception:  # noqa: BLE001
                pass
            try:
                return ezdxf.read(io.StringIO(text))
            except DXFError:
                continue
        return None

    @staticmethod
    def _write_doc(doc) -> bytes:
        buffer = io.StringIO()
        doc.write(buffer)
        return buffer.getvalue().encode("utf-8")

    def _clear_entity_text(
        self,
        entity,
        stats: Optional[dict] = None,
        audit: Optional[list[dict]] = None,
    ) -> None:
        """清空被合并实体的文本内容，防止与主实体译文重叠。"""
        dxftype = entity.dxftype()
        handle = getattr(entity.dxf, "handle", "")
        layer = getattr(entity.dxf, "layer", "")
        
        original_text = ""
        try:
            if dxftype == "TEXT":
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = ""
                logger.info("清空 TEXT 实体 [handle=%s]: '%s' -> ''", handle, original_text[:50])
            elif dxftype == "MTEXT":
                original_text = entity.text or ""
                entity.text = ""
                logger.info("清空 MTEXT 实体 [handle=%s]: '%s' -> ''", handle, original_text[:50])
            elif dxftype in {"ATTRIB", "ATTDEF"}:
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = ""
                logger.info("清空 %s 实体 [handle=%s]: '%s' -> ''", dxftype, handle, original_text[:50])
            else:
                logger.warning("无法清空实体：不支持的类型 [%s|%s]", dxftype, handle)
                return
                
            if stats is not None:
                stats["merged_cleared"] = stats.get("merged_cleared", 0) + 1
                
            if audit is not None:
                audit.append({
                    "handle": handle,
                    "entity_type": dxftype,
                    "layer": layer,
                    "source": original_text,
                    "target": "",
                    "status": "merged_cleared",
                    "reason": "part_of_merged_group",
                })
        except Exception as exc:
            logger.warning("清空实体文本失败 [%s|%s]: %s", dxftype, handle, exc)

    def _write_merged_translation(
        self,
        entity,
        source_text: str,
        target_text: str,
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        audit: Optional[list[dict]] = None,
        *,
        unicode_style_name: Optional[str] = None,
    ) -> None:
        """将合并后的译文写入主实体（第一个实体）。
        
        手动合并导出时，把合并后的译文写到第一个实体，而不是创建新的 MTEXT。
        这样可以保持原始实体的位置、样式等属性。
        同时支持溢出检测，当译文比原文长时自动缩小字体。
        """
        dxftype = entity.dxftype()
        handle = getattr(entity.dxf, "handle", "")
        layer = getattr(entity.dxf, "layer", "")
        
        original_text = ""
        try:
            if dxftype == "TEXT":
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = target_text
                # 溢出检测和缩小
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, source_text, target_text, opts)
            elif dxftype == "MTEXT":
                original_text = entity.text or ""
                # 回退到原实体写入时仍保留其段落/字号/缩进控制码；更重要的是
                # 不替换实体本身，从而完整保留 rotation、text_direction、
                # attachment_point、extrusion 以及父 INSERT 的组合变换。
                entity.text = _apply_original_mtext_layout(
                    original_text,
                    clean_mtext(original_text),
                    target_text,
                )
                # 溢出检测和缩小
                if opts.enable_overflow_shrink:
                    self._shrink_mtext_entity(entity, source_text, target_text, opts)
            elif dxftype in {"ATTRIB", "ATTDEF"}:
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = target_text
                # 溢出检测和缩小
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, source_text, target_text, opts)
            else:
                logger.warning("无法写入合并译文：不支持的实体类型 [%s|%s]", dxftype, handle)
                return
            
            # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
            if unicode_style_name and self._has_non_ascii(target_text):
                self._apply_unicode_style(entity, unicode_style_name)
                
            if stats is not None:
                stats["merged_written"] = stats.get("merged_written", 0) + 1
                
            if audit is not None:
                audit.append({
                    "handle": handle,
                    "entity_type": dxftype,
                    "layer": layer,
                    "source": original_text,
                    "target": target_text,
                    "status": "merged_written",
                    "reason": "primary_of_merged_group",
                })
                
            logger.debug(
                "写入合并译文 [%s|%s]: %s -> %s", 
                dxftype, handle, original_text[:30], target_text[:30]
            )
        except Exception as exc:
            logger.warning("写入合并译文失败 [%s|%s]: %s", dxftype, handle, exc)

    @staticmethod
    def _mtext_requires_in_place_preservation(entity, scope: str = "") -> bool:
        """判断文本实体是否必须保留原几何后原位回写。

        这里保留旧方法名以兼容拆段 MTEXT 调用，但判断范围必须覆盖 TEXT、
        ATTRIB 和 ATTDEF。块内文字常用“子实体局部 -90° + 父 INSERT +90°”
        抵消成画面上的水平文字；若只保护 MTEXT，TEXT 合并组被重建成默认
        MTEXT 后就会直接继承父块旋转，导致整组译文旋转 90°。
        """
        try:
            entity_type = entity.dxftype()
        except Exception:  # noqa: BLE001
            return True
        if entity_type not in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
            return False

        # 块定义或 INSERT 实例中的任意文本都保留原实体。新建 MTEXT 使用的是
        # 解析阶段的世界坐标，而目标空间是块局部坐标；除了丢失局部旋转外，
        # 坐标本身也可能再次经过父 INSERT 变换。
        if scope.startswith("block:") or ":insert:" in scope:
            return True

        try:
            get_rotation = getattr(entity, "get_rotation", None)
            rotation = float(
                get_rotation()
                if callable(get_rotation)
                else (getattr(entity.dxf, "rotation", 0.0) or 0.0)
            )
        except Exception:  # noqa: BLE001 - 方向不明时禁止破坏性重建
            return True
        normalized_rotation = (rotation + 180.0) % 360.0 - 180.0
        # ODA DWG→DXF 会给原本水平的文字引入约 0.000002° 的浮点噪声。
        # 旧阈值 1e-6° 会把这种普通 TEXT 误判为旋转文字，继而因为同组 Logo
        # 字母角度不同而放弃 MTEXT 重建，最终把整段译文塞回一个单行 TEXT。
        if abs(normalized_rotation) > 1e-3:
            return True

        # 非默认 OCS 法向量会改变文字的实际绘制平面，所有文本类型都适用。
        try:
            extrusion = getattr(entity.dxf, "extrusion", None)
            if extrusion is not None and (
                abs(float(extrusion[0])) > 1e-9
                or abs(float(extrusion[1])) > 1e-9
                or abs(float(extrusion[2]) - 1.0) > 1e-9
            ):
                return True
        except Exception:  # noqa: BLE001
            return True

        # 竖排文字样式同样不能安全地替换成默认 MTEXT。
        try:
            doc = getattr(entity, "doc", None)
            style_name = str(getattr(entity.dxf, "style", "Standard") or "Standard")
            style = doc.styles.get(style_name) if doc is not None else None
            style_flags = int(getattr(style.dxf, "flags", 0) or 0) if style else 0
            if style_flags & 4:
                return True
        except Exception:  # noqa: BLE001 - 样式无法解析时由实体方向继续判断
            pass

        if entity_type != "MTEXT":
            # TEXT 的反向/倒置标志，以及 ALIGNED/FIT 双点方向，在默认 MTEXT
            # 中均无法等价表达；继续使用原实体最安全。
            try:
                if int(getattr(entity.dxf, "text_generation_flag", 0) or 0) != 0:
                    return True
                if int(getattr(entity.dxf, "halign", 0) or 0) in {3, 5}:
                    return True
            except (TypeError, ValueError):
                return True
            return False

        try:
            flow_direction = int(
                getattr(entity.dxf, "flow_direction", 1) or 1
            )
        except (TypeError, ValueError):
            return True
        # DXF 规范中 5 表示 ByStyle，并不等于竖排；上面已经检查实际旋转
        # 和竖排样式，因此普通水平 ByStyle MTEXT 仍可安全重建。
        if flow_direction not in {1, 5}:
            return True

        try:
            if int(getattr(entity.dxf, "attachment_point", 1) or 1) != 1:
                return True
        except (TypeError, ValueError):
            return True

        try:
            direction = getattr(entity.dxf, "text_direction", None)
            if direction is not None and (
                abs(float(direction[0]) - 1.0) > 1e-9
                or abs(float(direction[1])) > 1e-9
                or abs(float(direction[2])) > 1e-9
            ):
                return True
        except Exception:  # noqa: BLE001
            return True
        return False

    def _write_split_mtext_in_place(
        self,
        entity,
        splits: List[Dict],
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        audit: Optional[list[dict]] = None,
        *,
        unicode_style_name: Optional[str] = None,
    ) -> bool:
        """将拆段译文写回原 MTEXT，同时保留其全部方向和块变换属性。"""
        raw = entity.text or ""
        cleaned = clean_mtext(raw)
        source_parts = cleaned.split("\n")
        if not raw or not source_parts:
            return False

        target_parts = list(source_parts)
        updated_indices: Set[int] = set()
        for item in splits:
            target_text = str(item.get("target_text") or "").strip()
            if not target_text:
                continue

            raw_indices = item.get("indices") or []
            if not isinstance(raw_indices, (list, tuple, set)):
                raw_indices = [raw_indices]
            indices: List[int] = []
            for value in raw_indices:
                try:
                    index = int(value)
                except (TypeError, ValueError):
                    continue
                if 0 <= index < len(source_parts):
                    indices.append(index)
            indices = sorted(set(indices))

            if not indices:
                source_text = clean_mtext(str(item.get("source_text") or "")).strip()
                indices = [
                    index
                    for index, part in enumerate(source_parts)
                    if index not in updated_indices and part.strip() == source_text
                ][:1]
            if not indices:
                logger.warning(
                    "MTEXT 拆段原位回写缺少段索引 handle=%s source=%r",
                    getattr(entity.dxf, "handle", ""),
                    str(item.get("source_text") or "")[:40],
                )
                return False

            if len(indices) == 1:
                target_parts[indices[0]] = target_text
            else:
                source_layout = r"\P".join(source_parts[index] for index in indices)
                restored_target = _restore_mtext_paragraphs(target_text, source_layout)
                restored_parts = [
                    part.strip()
                    for part in _MTEXT_PARAGRAPH_SPLIT_RE.split(restored_target)
                    if part.strip()
                ]
                if len(restored_parts) != len(indices):
                    logger.warning(
                        "MTEXT 拆段原位回写段数不匹配 handle=%s indices=%s target_parts=%d",
                        getattr(entity.dxf, "handle", ""),
                        indices,
                        len(restored_parts),
                    )
                    return False
                for index, restored_part in zip(indices, restored_parts):
                    target_parts[index] = restored_part
            updated_indices.update(indices)

        if not updated_indices:
            return False

        target_layout = r"\P".join(target_parts)
        formatted_value = _apply_original_mtext_layout(raw, cleaned, target_layout)
        entity.text = formatted_value

        # 原拆段路径会分别按 y_budget 拟合每段。原位写回虽然必须保留同一
        # MTEXT 实体，也要继续遵守这些槽位预算；统一缩小基础字高可避免
        # 前段自动换行增多后把后段推入图表或其它预留区域。
        base_char_height = max(
            float(getattr(entity.dxf, "char_height", 0) or 0),
            1e-6,
        )
        line_spacing_factor = max(
            float(getattr(entity.dxf, "line_spacing_factor", 1.0) or 1.0),
            0.25,
        )

        def fits_split_budgets(candidate_height: float) -> Tuple[bool, bool]:
            scale = candidate_height / base_char_height
            has_budget = False
            for item in splits:
                try:
                    budget = float(item.get("y_budget") or 0)
                except (TypeError, ValueError):
                    budget = 0.0
                if budget <= 0:
                    continue
                has_budget = True

                item_height = max(
                    float(item.get("height") or base_char_height) * scale,
                    1e-6,
                )
                try:
                    box_width = float(item.get("width") or 0)
                except (TypeError, ValueError):
                    box_width = 0.0
                if box_width <= 0:
                    box_width = float(getattr(entity.dxf, "width", 0) or 0)
                if box_width <= 0:
                    box_width = item_height * 30.0

                target = clean_mtext(str(item.get("target_text") or ""))
                target_paragraphs = target.split("\n") or [target]
                visual_lines = sum(
                    max(
                        1,
                        math.ceil(
                            estimate_text_width(paragraph or " ", item_height)
                            / max(box_width, 1e-6)
                        ),
                    )
                    for paragraph in target_paragraphs
                )
                required_height = (
                    visual_lines
                    * item_height
                    * (5.0 / 3.0)
                    * line_spacing_factor
                )
                if required_height > budget + max(item_height * 0.02, 1e-6):
                    return False, has_budget
            return True, has_budget

        fits_current, has_budget = fits_split_budgets(base_char_height)
        if opts.enable_overflow_shrink and has_budget and not fits_current:
            minimum_height = max(
                base_char_height * opts.min_char_height_ratio,
                1e-6,
            )
            fits_minimum, _ = fits_split_budgets(minimum_height)
            fitted_height = minimum_height
            if fits_minimum:
                low = minimum_height
                high = base_char_height
                for _ in range(24):
                    middle = (low + high) / 2.0
                    fits_middle, _ = fits_split_budgets(middle)
                    if fits_middle:
                        low = middle
                    else:
                        high = middle
                fitted_height = low
            else:
                logger.warning(
                    "MTEXT 拆段原位回写达到最小字号仍超预算 handle=%s "
                    "height=%.4f",
                    getattr(entity.dxf, "handle", ""),
                    minimum_height,
                )
            entity.dxf.char_height = round(fitted_height, 6)
            logger.info(
                "MTEXT 拆段原位回写按预算缩字 handle=%s height=%.4f->%.4f",
                getattr(entity.dxf, "handle", ""),
                base_char_height,
                fitted_height,
            )

        if (
            unicode_style_name
            and self._has_non_ascii(target_layout)
            and _mtext_has_unformatted_non_ascii(formatted_value)
        ):
            self._apply_unicode_style(entity, unicode_style_name)

        handle = str(getattr(entity.dxf, "handle", "") or "")
        if stats is not None:
            stats["mtext_split_in_place"] = stats.get("mtext_split_in_place", 0) + 1
        if audit is not None:
            audit.append({
                "handle": handle,
                "entity_type": "MTEXT",
                "layer": str(getattr(entity.dxf, "layer", "") or ""),
                "source": cleaned,
                "target": clean_mtext(target_layout),
                "status": "mtext_split_in_place",
                "reason": "preserve_rotation_attachment_and_insert_transform",
            })
        return True

    @staticmethod
    def _normalize_split_mtext_layout(entity, splits: List[Dict]) -> List[Dict]:
        """用原 MTEXT 修正旧版拆段元数据，无需重新导入文档。"""
        normalized = [dict(item) for item in splits]
        if not normalized or all(
            int(item.get("layout_version") or 1) >= 3 for item in normalized
        ):
            return normalized

        parts = [part.strip() for part in clean_mtext(entity.text or "").split("\n")]
        indexed_items: list[tuple[int, Dict]] = []
        for item in normalized:
            indices = item.get("indices") or []
            try:
                index = min(int(value) for value in indices)
            except (TypeError, ValueError):
                continue
            indexed_items.append((index, item))
        if not parts or not indexed_items:
            return normalized

        first_index, first_item = min(indexed_items, key=lambda pair: pair[0])
        local_nominal_height = max(
            float(getattr(entity.dxf, "char_height", 0) or 0),
            1e-6,
        )
        saved_nominal_height = max(
            float(first_item.get("height") or local_nominal_height),
            1e-6,
        )
        height_scale = saved_nominal_height / local_nominal_height
        paragraph_heights = [
            height * height_scale
            for height in _mtext_paragraph_heights(
                entity.text or "", local_nominal_height
            )
        ]
        first_height = (
            paragraph_heights[first_index]
            if first_index < len(paragraph_heights)
            else saved_nominal_height
        )
        # 顶层 MTEXT 直接使用原图实体锚点。旧数据库里的 y 正是本次错位来源，
        # 不能再用它反推。INSERT 内实体保存的是世界坐标，沿用旧公式兜底。
        scope = str(first_item.get("scope") or "")
        if ":insert:" not in scope:
            insert = getattr(entity.dxf, "insert", (0, 0, 0))
            insert_y = float(insert[1])
            attachment = int(getattr(entity.dxf, "attachment_point", 1) or 1)
            if attachment in (1, 2, 3):
                base_y = insert_y - first_height
            elif attachment in (4, 5, 6):
                base_y = insert_y - first_height / 2.0
            else:
                base_y = insert_y
        else:
            base_y = float(first_item.get("y") or 0) - first_height * 1.3 * (
                len(parts) - 1 - first_index
            )
        spacing_factor = float(
            getattr(entity.dxf, "line_spacing_factor", 1.0) or 1.0
        )
        width = float(first_item.get("width") or 0)
        if width <= 0:
            width = float(getattr(entity.dxf, "width", 0) or 0)
        if width <= 0:
            width = first_height * 30

        y_by_index: dict[int, float] = {}
        height_by_index: dict[int, float] = {}
        budget_by_index: dict[int, float] = {}
        cursor_y = base_y
        for index, part in enumerate(parts):
            effective_height = (
                paragraph_heights[index]
                if index < len(paragraph_heights)
                else first_height
            )
            line_height = effective_height * (5.0 / 3.0) * spacing_factor
            if not part:
                cursor_y -= line_height
                continue
            y_by_index[index] = cursor_y
            height_by_index[index] = effective_height
            source_width = estimate_text_width(part, effective_height)
            source_lines = max(1, math.ceil(source_width / max(width, 1e-6)))
            budget = source_lines * line_height
            budget_by_index[index] = budget
            cursor_y -= budget

        for index, item in indexed_items:
            if index not in y_by_index:
                continue
            item["y"] = y_by_index[index]
            item["height"] = height_by_index[index]
            item["width"] = width
            item["y_budget"] = budget_by_index[index]
            item["layout_version"] = 3

        logger.info(
            "DXF 导出：旧版 MTEXT 拆段坐标已迁移 handle=%s base_y=%.2f",
            getattr(entity.dxf, "handle", ""),
            base_y,
        )
        return normalized

    def _find_text_box_available_height(
        self,
        doc,
        *,
        scope: str,
        layer: str,
        box_left: float,
        box_top: float,
        box_width: float,
        original_height: float,
        excluded_handles: Set[str],
        source_bbox_height: Optional[float] = None,
    ) -> Optional[float]:
        """按下一文本块或横跨整列的水平框线推导可靠矩形高度。"""
        try:
            target_space = doc.modelspace()
            if scope.startswith("layout:"):
                layout_name = scope.removeprefix("layout:").split(":insert:", 1)[0]
                target_space = doc.layouts.get(layout_name)
            elif scope.startswith("block:"):
                block_name = scope.removeprefix("block:").split(":insert:", 1)[0]
                target_space = doc.blocks.get(block_name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MTEXT 矩形空间定位失败 scope=%s: %s", scope, exc)
            return None

        original_height = max(float(original_height), 1e-6)
        box_width = max(float(box_width), original_height)
        box_right = box_left + box_width
        geometry_adapter = DxfAdapter()
        same_layer_candidates: List[Tuple[float, float, str, str]] = []
        fallback_candidates: List[Tuple[float, float, str, str]] = []
        line_candidates: List[Tuple[float, str, str, str]] = []

        def collect_horizontal_boundaries(candidate, transform=None) -> None:
            kind = candidate.dxftype()
            handle = str(getattr(candidate.dxf, "handle", "") or "")
            candidate_layer = str(getattr(candidate.dxf, "layer", "") or "")
            normalized_layer = candidate_layer.casefold()
            is_boundary_layer = bool(
                candidate_layer == layer
                or normalized_layer in {"0", "defpoints"}
                or any(token in normalized_layer for token in (
                    "title", "border", "frame", "table", "tab", "框", "表格",
                ))
            )
            if (
                kind not in {"LINE", "LWPOLYLINE", "POLYLINE"}
                or not is_boundary_layer
            ):
                return
            try:
                if kind == "LINE":
                    points = [candidate.dxf.start, candidate.dxf.end]
                elif kind == "LWPOLYLINE":
                    points = [
                        (float(point[0]), float(point[1]), 0.0)
                        for point in candidate.get_points("xy")
                    ]
                    if getattr(candidate, "closed", False) and len(points) > 2:
                        points.append(points[0])
                else:
                    points = [vertex.dxf.location for vertex in candidate.vertices]
                    if getattr(candidate, "is_closed", False) and len(points) > 2:
                        points.append(points[0])
                if transform is not None:
                    points = [transform.transform(point) for point in points]
            except Exception:  # noqa: BLE001
                return

            for start, end in zip(points, points[1:]):
                x1, y1 = float(start[0]), float(start[1])
                x2, y2 = float(end[0]), float(end[1])
                segment_width = abs(x2 - x1)
                if segment_width <= 1e-6:
                    continue
                if abs(y2 - y1) > max(segment_width * 0.02, 1e-6):
                    continue
                line_left, line_right = sorted((x1, x2))
                overlap = min(box_right, line_right) - max(box_left, line_left)
                # 只有横跨至少 75% 文本列宽的线才是外框/分区边界；墙线、
                # 引线和局部表格线不能把整个说明块错误截断。
                if overlap < box_width * 0.75:
                    continue
                line_y = (y1 + y2) / 2.0
                if line_y >= box_top - original_height * 0.5:
                    continue
                line_candidates.append((
                    line_y,
                    handle,
                    str(getattr(candidate.dxf, "layer", "") or ""),
                    kind,
                ))

        for candidate in target_space:
            kind = candidate.dxftype()
            handle = str(getattr(candidate.dxf, "handle", "") or "")

            if kind in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                if handle in excluded_handles:
                    continue
                try:
                    geometry = geometry_adapter._extract_text_entity(candidate, scope)
                except Exception:  # noqa: BLE001
                    geometry = None
                if geometry is None or not geometry.text.strip():
                    continue

                candidate_top = geometry.y + geometry.height
                if candidate_top >= box_top - original_height * 0.5:
                    continue
                overlap = min(box_right, geometry.right_edge) - max(box_left, geometry.x)
                candidate_width = max(geometry.width, original_height)
                if overlap < min(box_width, candidate_width) * 0.35:
                    continue
                item = (candidate_top, geometry.height, handle, geometry.layer)
                if geometry.layer == layer:
                    same_layer_candidates.append(item)
                else:
                    fallback_candidates.append(item)
                continue

            collect_horizontal_boundaries(candidate)

        # 图框/分区线经常位于 INSERT 块定义内。顶层 layout 只能看到 INSERT，
        # 必须把块内线段递归变换到当前空间坐标后再参与硬边界判断。
        try:
            from ezdxf.math import Matrix44

            pending_inserts = [
                (candidate, None, 0)
                for candidate in target_space
                if candidate.dxftype() == "INSERT"
            ]
            while pending_inserts:
                insert_entity, parent_transform, depth = pending_inserts.pop()
                if depth >= 10:
                    continue
                try:
                    block = doc.blocks.get(str(insert_entity.dxf.name or ""))
                    child_transform = insert_entity.matrix44()
                    if parent_transform is not None:
                        child_transform = Matrix44.chain(
                            child_transform,
                            parent_transform,
                        )
                except Exception:  # noqa: BLE001
                    continue
                for child in block:
                    if child.dxftype() == "INSERT":
                        pending_inserts.append((child, child_transform, depth + 1))
                    else:
                        collect_horizontal_boundaries(child, child_transform)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MTEXT 块内框线扫描失败 scope=%s: %s", scope, exc)

        boundary_candidates: List[Tuple[float, str, str, str]] = []
        text_candidates = [*same_layer_candidates, *fallback_candidates]
        if text_candidates:
            next_top, next_height, next_handle, next_layer = max(
                text_candidates, key=lambda item: item[0]
            )
            text_gap = max(original_height * 0.75, next_height * 0.25)
            boundary_candidates.append((
                next_top + text_gap,
                "text",
                next_handle,
                next_layer,
            ))

        # 没有同层下一文本块时，跨层文字或远处图框只能证明“不能再往下”，
        # 不能证明中间全部空白都属于当前段落。此时以原 MTEXT 的真实字形 bbox
        # 作为保守原始矩形，防止英文译文借用大片空白后仍保持原字号。
        if (
            not same_layer_candidates
            and not line_candidates
            and source_bbox_height is not None
            and source_bbox_height > original_height * 0.2
        ):
            boundary_candidates.append((
                box_top - source_bbox_height,
                "source_bbox",
                "",
                layer,
            ))

        if line_candidates:
            line_y, line_handle, line_layer, line_kind = max(
                line_candidates, key=lambda item: item[0]
            )
            boundary_candidates.append((
                line_y + original_height * 0.5,
                line_kind,
                line_handle,
                line_layer,
            ))

        if not boundary_candidates:
            return None

        box_bottom, boundary_kind, boundary_handle, boundary_layer = max(
            boundary_candidates, key=lambda item: item[0]
        )
        available_height = box_top - box_bottom
        if available_height <= original_height * 0.2:
            return None

        logger.info(
            "MTEXT 硬矩形 boundary=%s handle=%s layer=%s "
            "box=(%.2f, %.2f, %.2f, %.2f)",
            boundary_kind,
            boundary_handle,
            boundary_layer,
            box_left,
            box_bottom,
            box_width,
            available_height,
        )
        return available_height

    def _find_mtext_available_height(
        self,
        doc,
        entity,
        scope: str,
        excluded_handles: Set[str],
    ) -> Optional[float]:
        """返回 MTEXT 顶部到下一独立文本块之间的可用矩形高度。"""
        geometry_adapter = DxfAdapter()
        try:
            primary_geometry = geometry_adapter._extract_text_entity(entity, scope)
        except Exception:  # noqa: BLE001
            primary_geometry = None
        if primary_geometry is None:
            return None

        original_height = max(
            float(getattr(entity.dxf, "char_height", 0) or primary_geometry.height),
            1e-6,
        )
        box_width = float(getattr(entity.dxf, "width", 0) or 0)
        if box_width <= 0:
            return None
        try:
            insert_y = float(entity.dxf.insert[1])
            attachment = int(getattr(entity.dxf, "attachment_point", 1) or 1)
        except Exception:  # noqa: BLE001
            insert_y = primary_geometry.y + primary_geometry.height
            attachment = 1
        box_top = (
            insert_y
            if attachment in (1, 2, 3)
            else primary_geometry.y + primary_geometry.height
        )
        source_bbox_height: Optional[float] = None
        try:
            from ezdxf import bbox as ezdxf_bbox

            source_extents = ezdxf_bbox.extents([entity], fast=True)
            if source_extents.has_data and float(source_extents.size.y) > 0:
                source_bbox_height = float(source_extents.size.y)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "MTEXT 原文 bbox 高度读取失败 handle=%s: %s",
                getattr(entity.dxf, "handle", ""),
                exc,
            )
        return self._find_text_box_available_height(
            doc,
            scope=scope,
            layer=primary_geometry.layer,
            box_left=primary_geometry.x,
            box_top=box_top,
            box_width=box_width,
            original_height=original_height,
            excluded_handles=excluded_handles,
            source_bbox_height=source_bbox_height,
        )

    def _inject_mtext_max_heights(
        self,
        doc,
        translations: Dict[str, str],
    ) -> None:
        """在原图未修改前，为所有有可靠下边界的普通 MTEXT 注入硬高度。"""
        constrained = 0

        def scan_space(space, scope: str) -> None:
            nonlocal constrained
            for entity in space:
                if entity.dxftype() != "MTEXT":
                    continue
                handle = str(getattr(entity.dxf, "handle", "") or "")
                if not handle or CAD_MTEXT_MAX_HEIGHT_PREFIX + handle in translations:
                    continue
                raw = entity.text or ""
                cleaned = clean_mtext(raw)
                if not cleaned.strip() or float(getattr(entity.dxf, "width", 0) or 0) <= 0:
                    continue
                if CAD_MTEXT_HANDLE_BLOCK_PREFIX + handle in translations:
                    continue

                target = translations.get(CAD_MTEXT_HANDLE_TRANSLATION_PREFIX + handle)
                if target is None:
                    target = self._lookup(cleaned, translations)
                if target is None:
                    target = self._lookup(raw, translations)
                if target is None:
                    target = self._merge_mtext_paragraph_translations(cleaned, translations)
                if target is None or target == cleaned:
                    continue

                available_height = self._find_mtext_available_height(
                    doc,
                    entity,
                    scope,
                    {handle},
                )
                if available_height is None:
                    continue
                translations[CAD_MTEXT_MAX_HEIGHT_PREFIX + handle] = repr(available_height)
                constrained += 1

        for layout in doc.layouts:
            scan_space(layout, f"layout:{layout.name}")
        for block in doc.blocks:
            if block.name.lower().startswith(("*model_space", "*paper_space")):
                continue
            scan_space(block, f"block:{block.name}")
        logger.info("DXF 导出：普通 MTEXT 硬矩形约束=%d", constrained)

    def _clip_merged_mtext_width_to_next_block(
        self,
        doc,
        info: MergedTextExportInfo,
    ) -> None:
        """把文本框宽度截到同行下一文本块或最近竖向框线之前。"""
        if (
            info.cad_table_cell
            or info.source_mtext_layout
            or info.group_width <= 0
        ):
            return
        try:
            target_space = doc.modelspace()
            if info.scope.startswith("layout:"):
                layout_name = info.scope.removeprefix("layout:").split(":insert:", 1)[0]
                target_space = doc.layouts.get(layout_name)
            elif info.scope.startswith("block:"):
                block_name = info.scope.removeprefix("block:").split(":insert:", 1)[0]
                target_space = doc.blocks.get(block_name)
        except Exception:  # noqa: BLE001
            return

        original_height = max(float(info.primary_height), 1e-6)
        box_left = float(info.group_x)
        box_right = box_left + float(info.group_width)
        # 普通 merged 组只允许收窄现有包络；单实体的 group_width 只是源字形
        # 宽度，真实右框可能略在其外侧，因此必须向右寻找最近可证明的边界。
        search_right = (
            math.inf
            if info.single_text_block
            else box_right + original_height
        )
        box_top = float(info.group_y_top or (info.primary_y + original_height))
        excluded_handles = set(map(str, info.merged_handles))
        geometry_adapter = DxfAdapter()
        right_boundaries: List[Tuple[float, str, str]] = []

        def collect_vertical_boundaries(candidate, transform=None) -> None:
            kind = candidate.dxftype()
            if kind not in {"LINE", "LWPOLYLINE", "POLYLINE"}:
                return
            handle = str(getattr(candidate.dxf, "handle", "") or "")
            # 任意足够长且跨过当前文字行的竖线都是实际几何屏障；不能依赖
            # 标题栏图层名，因为很多 DWG 转 DXF 后只保留颜色而图层名不稳定。
            try:
                if kind == "LINE":
                    points = [candidate.dxf.start, candidate.dxf.end]
                elif kind == "LWPOLYLINE":
                    points = [
                        (float(point[0]), float(point[1]), 0.0)
                        for point in candidate.get_points("xy")
                    ]
                    if getattr(candidate, "closed", False) and len(points) > 2:
                        points.append(points[0])
                else:
                    points = [vertex.dxf.location for vertex in candidate.vertices]
                    if getattr(candidate, "is_closed", False) and len(points) > 2:
                        points.append(points[0])
                if transform is not None:
                    points = [transform.transform(point) for point in points]
            except Exception:  # noqa: BLE001
                return

            row_middle = box_top - original_height * 0.5
            minimum_span = max(
                original_height * 2.0,
                float(info.group_height or 0) * 1.5,
            )
            for start, end in zip(points, points[1:]):
                x1, y1 = float(start[0]), float(start[1])
                x2, y2 = float(end[0]), float(end[1])
                segment_height = abs(y2 - y1)
                if segment_height <= 1e-6:
                    continue
                if abs(x2 - x1) > max(segment_height * 0.02, 1e-6):
                    continue
                line_x = (x1 + x2) / 2.0
                if not (box_left + original_height * 2.0 < line_x < search_right):
                    continue
                line_bottom, line_top = sorted((y1, y2))
                if not (
                    line_bottom - original_height * 0.25
                    <= row_middle
                    <= line_top + original_height * 0.25
                ):
                    continue
                if segment_height < minimum_span:
                    continue
                right_boundaries.append((line_x, "frame", handle))

        for candidate in target_space:
            if candidate.dxftype() in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                handle = str(getattr(candidate.dxf, "handle", "") or "")
                if handle in excluded_handles:
                    continue
                try:
                    geometry = geometry_adapter._extract_text_entity(candidate, info.scope)
                except Exception:  # noqa: BLE001
                    geometry = None
                if geometry is None or not geometry.text.strip() or geometry.layer != info.layer:
                    continue
                candidate_top = geometry.y + geometry.height
                candidate_bottom = geometry.y
                if candidate.dxftype() == "MTEXT":
                    try:
                        from ezdxf import bbox as ezdxf_bbox

                        extents = ezdxf_bbox.extents([candidate], fast=True)
                        if extents.has_data:
                            candidate_top = float(extents.extmax.y)
                            candidate_bottom = float(extents.extmin.y)
                    except Exception:  # noqa: BLE001
                        pass
                row_bottom = box_top - original_height * 1.5
                row_top = box_top + original_height * 0.5
                if candidate_top < row_bottom or candidate_bottom > row_top:
                    continue
                if not (
                    box_left + original_height * 2.0
                    < geometry.x
                    < search_right
                ):
                    continue
                right_boundaries.append((geometry.x, "text", handle))
                continue
            collect_vertical_boundaries(candidate)

        # 标题栏框线通常位于 INSERT 的块定义中，递归转换到当前空间后再裁宽。
        try:
            from ezdxf.math import Matrix44

            pending_inserts = [
                (candidate, None, 0)
                for candidate in target_space
                if candidate.dxftype() == "INSERT"
            ]
            while pending_inserts:
                insert_entity, parent_transform, depth = pending_inserts.pop()
                if depth >= 10:
                    continue
                try:
                    block = doc.blocks.get(str(insert_entity.dxf.name or ""))
                    child_transform = insert_entity.matrix44()
                    if parent_transform is not None:
                        child_transform = Matrix44.chain(
                            child_transform,
                            parent_transform,
                        )
                except Exception:  # noqa: BLE001
                    continue
                for child in block:
                    if child.dxftype() == "INSERT":
                        pending_inserts.append((child, child_transform, depth + 1))
                    else:
                        collect_vertical_boundaries(child, child_transform)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MTEXT 块内竖框扫描失败 scope=%s: %s", info.scope, exc)

        if not right_boundaries:
            return
        next_left, boundary_kind, boundary_handle = min(
            right_boundaries,
            key=lambda item: item[0],
        )
        horizontal_gap = original_height * (0.25 if boundary_kind == "frame" else 0.5)
        available_width = next_left - box_left - horizontal_gap
        if available_width < original_height * 2.0:
            return

        if info.single_text_block:
            previous_width = info.group_width
            info.group_width = available_width
            info.reliable_width = True
            logger.info(
                "单文本块确认右边界 primary=%s boundary=%s:%s width=%.2f->%.2f",
                info.primary_handle,
                boundary_kind,
                boundary_handle,
                previous_width,
                available_width,
            )
            return

        if available_width >= info.group_width - 1e-6:
            return
        logger.info(
            "合并 MTEXT 截宽 primary=%s boundary=%s:%s width=%.2f->%.2f",
            info.primary_handle,
            boundary_kind,
            boundary_handle,
            info.group_width,
            available_width,
        )
        info.group_width = available_width

    def _assign_merged_mtext_max_heights(
        self,
        doc,
        merged_infos: List[MergedTextExportInfo],
    ) -> None:
        """为重建 MTEXT 标记可证明可靠的纵向矩形，拒绝把一行 group_height 当外框。"""
        for info in merged_infos:
            self._clip_merged_mtext_width_to_next_block(doc, info)
            if info.group_width <= 0:
                continue
            if (
                (info.cad_table_cell or info.source_mtext_layout)
                and info.group_height > 0
            ):
                info.max_height = float(info.group_height)
                continue

            original_height = max(float(info.primary_height), 1e-6)
            if info.group_height > original_height * 1.5:
                # 多行 merged 的 group_height 是所有源成员的真实纵向包络；与常见
                # 的“一行字高”元数据不同，可作为保守硬框，尤其适用于 INSERT
                # 子实体无法从顶层 layout 枚举相邻块的场景。
                info.max_height = float(info.group_height)
                continue

            box_top = float(info.group_y_top or (info.primary_y + original_height))
            box_left = float(info.group_x if info.group_width > 0 else info.primary_x)
            available_height = self._find_text_box_available_height(
                doc,
                scope=info.scope,
                layer=info.layer,
                box_left=box_left,
                box_top=box_top,
                box_width=float(info.group_width),
                original_height=original_height,
                excluded_handles=set(map(str, info.merged_handles)),
            )
            if available_height is not None:
                info.max_height = available_height
                logger.info(
                    "合并 MTEXT 硬矩形 primary=%s height=%.2f",
                    info.primary_handle,
                    available_height,
                )

    def _detach_heading_from_rich_mtext_groups(
        self,
        doc,
        merged_infos: List[MergedTextExportInfo],
        translations: Dict[str, str],
        primary_translations: Dict[str, Tuple[str, str]],
    ) -> List[MergedTextExportInfo]:
        """把“独立标题 TEXT + 富格式正文 MTEXT”恢复为两个原位实体。

        旧句段会把标题和正文合成一个 MERGED_TEXT。若继续空间重建，就会
        清空两者并用主 MTEXT 的插入点、颜色创建一个纯文本框，导致标题并入
        首行、正文整体左移且局部颜色丢失。这里为旧任务提供导出兼容：标题和
        正文分别注入 handle 级译文，交给普通原位替换路径处理。
        """
        remaining: List[MergedTextExportInfo] = []
        for info in merged_infos:
            if info.cad_table_cell or len(info.merged_handles) < 2:
                remaining.append(info)
                continue

            try:
                primary = doc.entitydb.get(info.primary_handle)
            except Exception:  # noqa: BLE001
                primary = None
            if primary is None or primary.dxftype() != "MTEXT":
                remaining.append(info)
                continue

            title_candidates = []
            for handle in info.merged_handles:
                if handle == info.primary_handle:
                    continue
                try:
                    entity = doc.entitydb.get(handle)
                except Exception:  # noqa: BLE001
                    entity = None
                if entity is None or entity.dxftype() not in {"TEXT", "ATTRIB", "ATTDEF"}:
                    continue
                title_source = str(getattr(entity.dxf, "text", "") or "").strip()
                if not title_source or len(title_source) > 64:
                    continue
                if not re.match(
                    r"^(?:[一二三四五六七八九十百]+[、．.]|"
                    r"[IVXLCDM]+[．.]|\d+(?:\.\d+)*[、．.])",
                    title_source,
                    re.IGNORECASE,
                ):
                    continue
                title_candidates.append((handle, title_source))

            if len(title_candidates) != 1:
                remaining.append(info)
                continue

            title_handle, title_source = title_candidates[0]
            heading_split = _split_translated_heading(info.target_text, title_source)
            if heading_split is None:
                remaining.append(info)
                continue
            title_target, body_target = heading_split
            body_source = clean_mtext(primary.text or "").strip()
            if not body_source or not body_target.strip():
                remaining.append(info)
                continue

            combined_compact = re.sub(r"\s+", "", info.source_text or "")
            expected_compact = re.sub(r"\s+", "", title_source + body_source)
            if expected_compact not in combined_compact:
                remaining.append(info)
                continue

            translations[CAD_TEXT_HANDLE_TRANSLATION_PREFIX + title_handle] = title_target
            translations[
                CAD_MTEXT_HANDLE_TRANSLATION_PREFIX + info.primary_handle
            ] = body_target
            available_height = self._find_mtext_available_height(
                doc,
                primary,
                info.scope,
                set(map(str, info.merged_handles)),
            )
            if available_height is not None:
                translations[
                    CAD_MTEXT_MAX_HEIGHT_PREFIX + info.primary_handle
                ] = repr(available_height)
            primary_translations.pop(info.primary_handle, None)
            logger.info(
                "拆分标题与富格式正文：title=%s body=%s paragraphs=%d max_height=%s",
                title_handle,
                info.primary_handle,
                len([part for part in re.split(r"\\P|\r\n?|\n", body_source) if part.strip()]),
                f"{available_height:.2f}" if available_height is not None else "unbounded",
            )

        return remaining

    def _restore_merged_mtext_layouts(
        self,
        doc,
        merged_infos: List[MergedTextExportInfo],
    ) -> None:
        """从原图 MTEXT 与下一文本块恢复长文本的排版矩形和段落。"""
        geometry_adapter = DxfAdapter()

        for info in merged_infos:
            # 表格单元格已有闭合框硬边界；结构化布局也有自己的控制码，
            # 均不得被普通长文本的“下一块”矩形覆盖。
            if info.cad_table_cell or info.preserve_mtext_layout:
                continue
            try:
                target_space = doc.modelspace()
                if info.scope.startswith("layout:"):
                    layout_name = info.scope.removeprefix("layout:").split(":insert:", 1)[0]
                    target_space = doc.layouts.get(layout_name)
                elif info.scope.startswith("block:"):
                    block_name = info.scope.removeprefix("block:").split(":insert:", 1)[0]
                    target_space = doc.blocks.get(block_name)
            except Exception as exc:  # noqa: BLE001
                logger.debug("MTEXT 排版矩形定位失败 scope=%s: %s", info.scope, exc)
                continue

            entries: List[Tuple[object, object]] = []
            for entity in target_space:
                if entity.dxftype() not in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                    continue
                try:
                    geometry = geometry_adapter._extract_text_entity(entity, info.scope)
                except Exception:  # noqa: BLE001
                    geometry = None
                if geometry is not None and geometry.text.strip():
                    entries.append((entity, geometry))

            primary_entry = next((
                entry for entry in entries
                if entry[0].dxftype() == "MTEXT"
                and str(getattr(entry[0].dxf, "handle", "") or "") == info.primary_handle
            ), None)
            if primary_entry is None:
                source_compact = re.sub(
                    r"\s+", "", clean_mtext(info.source_layout_text or info.source_text)
                )
                matching_entries = [
                    entry for entry in entries
                    if entry[0].dxftype() == "MTEXT"
                    and len([
                        part for part in re.split(
                            r"\\P|\r\n?|\n",
                            clean_mtext(entry[0].text or ""),
                        )
                        if part.strip()
                    ]) > 1
                    and re.sub(r"\s+", "", clean_mtext(entry[0].text or ""))
                    and re.sub(r"\s+", "", clean_mtext(entry[0].text or "")) in source_compact
                ]
                if matching_entries:
                    primary_entry = min(
                        matching_entries,
                        key=lambda entry: (
                            (entry[1].x - info.primary_x) ** 2
                            + (entry[1].y - info.primary_y) ** 2
                        ),
                    )
            if primary_entry is None:
                continue

            primary_entity, primary_geometry = primary_entry
            raw_source = clean_mtext(primary_entity.text or "")
            source_layout = info.source_layout_text or raw_source or info.source_text
            source_paragraphs = [
                part for part in re.split(r"\\P|\r\n?|\n", source_layout) if part.strip()
            ]
            raw_paragraphs = [
                part for part in re.split(r"\\P|\r\n?|\n", raw_source) if part.strip()
            ]
            if len(source_paragraphs) <= 1 and len(raw_paragraphs) > 1:
                source_layout = raw_source
                source_paragraphs = raw_paragraphs
            if len(source_paragraphs) <= 1:
                continue
            info.target_text = _restore_mtext_paragraphs(
                info.target_text,
                source_layout,
            )

            original_height = max(float(
                getattr(primary_entity.dxf, "char_height", 0)
                or info.primary_height
            ), 1e-6)
            original_width = float(getattr(primary_entity.dxf, "width", 0) or 0)
            if original_width <= 0:
                original_width = max(info.group_width, primary_geometry.width)
            original_width = max(original_width, original_height)

            member_handles = set(map(str, info.merged_handles))
            member_handles.add(str(getattr(primary_entity.dxf, "handle", "") or ""))
            member_entries = [
                entry for entry in entries
                if str(getattr(entry[0].dxf, "handle", "") or "") in member_handles
            ]
            member_top = max(
                (geometry.y + geometry.height for _, geometry in member_entries),
                default=primary_geometry.y + primary_geometry.height,
            )
            try:
                insert_y = float(primary_entity.dxf.insert[1])
            except Exception:  # noqa: BLE001
                insert_y = primary_geometry.y + primary_geometry.height
            box_top = max(float(info.group_y_top or 0), member_top, insert_y)
            box_left = primary_geometry.x
            box_right = box_left + original_width

            visual_bottom = primary_geometry.y
            try:
                from ezdxf import bbox as ezdxf_bbox

                extents = ezdxf_bbox.extents([primary_entity], fast=True)
                if extents.has_data:
                    visual_bottom = min(visual_bottom, float(extents.extmin.y))
                    box_top = max(box_top, float(extents.extmax.y))
            except Exception as exc:  # noqa: BLE001
                logger.debug("MTEXT 原始 bbox 读取失败 handle=%s: %s", info.primary_handle, exc)

            # 多段说明块的下边界取同列下一文本块顶部，并留出半个字高间隔。
            # 这样英文扩张可使用原图预留空白，但不会覆盖下一章节。
            next_candidates: List[Tuple[float, float, str]] = []
            if len(source_paragraphs) > 1:
                for entity, geometry in entries:
                    handle = str(getattr(entity.dxf, "handle", "") or "")
                    if handle in member_handles:
                        continue
                    candidate_top = geometry.y + geometry.height
                    if candidate_top >= visual_bottom - original_height * 0.25:
                        continue
                    if geometry.layer != primary_geometry.layer:
                        continue
                    overlap = min(box_right, geometry.right_edge) - max(box_left, geometry.x)
                    candidate_width = max(geometry.width, original_height)
                    minimum_overlap = min(original_width, candidate_width) * 0.35
                    if overlap < minimum_overlap:
                        continue
                    next_candidates.append((candidate_top, geometry.height, handle))

            box_bottom = visual_bottom
            if next_candidates:
                next_top, next_height, next_handle = max(next_candidates, key=lambda item: item[0])
                reserved_bottom = next_top + max(original_height * 0.75, next_height * 0.25)
                if reserved_bottom < box_bottom:
                    box_bottom = reserved_bottom
                    logger.info(
                        "MTEXT 排版矩形扩展 primary=%s next=%s top=%.2f bottom=%.2f",
                        info.primary_handle,
                        next_handle,
                        box_top,
                        box_bottom,
                    )

            # 防止孤立文本把矩形无限扩展；40 行字高足以容纳常规说明块。
            box_bottom = max(box_bottom, box_top - original_height * 40.0)
            info.primary_x = primary_geometry.x
            info.primary_y = primary_geometry.y
            info.primary_height = original_height
            info.primary_style = str(
                getattr(primary_entity.dxf, "style", info.primary_style) or info.primary_style
            )
            info.group_x = box_left
            info.group_y_top = box_top
            info.group_width = original_width
            info.group_height = max(box_top - box_bottom, original_height)
            info.source_mtext_layout = True
            logger.info(
                "恢复原 MTEXT 排版 primary=%s paragraphs=%d box=%.2fx%.2f",
                info.primary_handle,
                len(source_paragraphs),
                info.group_width,
                info.group_height,
            )

    def _create_split_mtext_entities(
        self,
        doc,
        splits: List[Dict],
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        unicode_style_name: Optional[str] = None,
    ) -> None:
        """把拆段译文锚定到原段顶部，并按 y_budget 实测最大安全字号。"""
        for item in splits:
            scope = str(item.get("scope") or "")
            layer = str(item.get("layer") or "0")
            x = float(item.get("x") or 0)
            y = float(item.get("y") or 0)
            original_height = max(float(item.get("height") or 2.5), 1e-6)
            top_y = y + original_height
            width = float(item.get("width") or 0)
            target_text = str(item.get("target_text") or "")
            if not target_text:
                continue

            try:
                target_space = doc.modelspace()
                if scope.startswith("layout:"):
                    layout_name = scope.removeprefix("layout:").split(":insert:", 1)[0]
                    target_space = doc.layouts.get(layout_name)
                elif scope.startswith("block:"):
                    block_name = scope.removeprefix("block:").split(":insert:", 1)[0]
                    target_space = doc.blocks.get(block_name)

                if width <= 0:
                    width = original_height * 30

                dxfattribs = {
                    "insert": (x, top_y, 0),
                    "char_height": round(original_height, 4),
                    "layer": layer,
                    "attachment_point": 1,
                    "width": round(width, 4),
                    "color": int(item.get("color") or 256),
                }
                tc = item.get("true_color")
                if tc is not None:
                    dxfattribs["true_color"] = int(tc)
                tr = item.get("transparency")
                if tr is not None:
                    dxfattribs["transparency"] = int(tr)

                mtext = target_space.add_mtext(target_text, dxfattribs=dxfattribs)
                style_name = str(item.get("style") or "")
                if style_name:
                    try:
                        mtext.dxf.style = style_name
                    except Exception:  # noqa: BLE001
                        pass
                if unicode_style_name and self._has_non_ascii(target_text):
                    self._apply_unicode_style(mtext, unicode_style_name)

                budget_raw = item.get("y_budget")
                try:
                    budget = float(budget_raw) if budget_raw is not None else 0.0
                except (TypeError, ValueError):
                    budget = 0.0
                if budget > 0 and opts.enable_overflow_shrink:
                    self._fit_mtext_to_max_height(mtext, budget)

                if stats is not None:
                    stats["mtext_split_created"] = stats.get("mtext_split_created", 0) + 1
                logger.info(
                    "MTEXT 拆段创建 y=%.2f box=%.2fx%.2f h=%.4f text=%r",
                    y,
                    width,
                    budget,
                    float(mtext.dxf.char_height),
                    target_text[:50],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("MTEXT 拆段创建失败：%s", exc)

    def _create_oriented_merged_mtext(
        self,
        doc,
        info: MergedTextExportInfo,
        opts: DxfExportOptions,
        *,
        unicode_style_name: Optional[str] = None,
    ) -> bool:
        """在原文字方向坐标系的矩形内重建 MTEXT。

        解析元数据中的 group_width/group_height 是世界坐标轴对齐矩形；对
        90°/270° 块内文字，它们会交换甚至被父 INSERT 再次变换。这里直接
        使用原块中的各个 TEXT，在文字基向量 (u, v) 上恢复原始占用矩形，
        再在同一块内创建同角度 MTEXT。这样既保留方向，也能按原框宽换行并
        按原框高缩字号。
        """
        target_space = None
        mtext = None
        try:
            if ":insert:" in info.scope:
                block_name = info.scope.rsplit(":insert:", 1)[1]
                target_space = doc.blocks.get(block_name)
            elif info.scope.startswith("block:"):
                block_name = info.scope.removeprefix("block:").split(":insert:", 1)[0]
                target_space = doc.blocks.get(block_name)
            else:
                return False

            handles = list(dict.fromkeys(
                [info.primary_handle, *info.merged_handles]
            ))
            entities = []
            for handle in handles:
                entity = doc.entitydb.get(handle)
                if entity is None or entity.dxftype() not in {"TEXT", "ATTRIB", "ATTDEF"}:
                    return False
                entities.append(entity)
            if not entities:
                return False

            primary_entity = doc.entitydb.get(info.primary_handle)
            if primary_entity is None:
                return False
            rotation = float(getattr(primary_entity.dxf, "rotation", 0.0) or 0.0)

            # 只有同方向、左对齐、默认 OCS 的块定义文字才能安全合成 MTEXT。
            # INSERT 实例 ATTRIB 的坐标属于具体实例，绝不能把译文写进共享块定义；
            # 非左对齐锚点和局部旋转在非均匀 INSERT 下也无法仅凭当前元数据
            # 可靠反投影，统一 fail closed 回退原位写入。
            for entity in entities:
                if entity.dxftype() == "ATTRIB":
                    return False
                halign = int(getattr(entity.dxf, "halign", 0) or 0)
                valign = int(getattr(entity.dxf, "valign", 0) or 0)
                if halign != 0 or valign != 0:
                    return False
                entity_rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
                normalized_entity_rotation = (
                    entity_rotation + 180.0
                ) % 360.0 - 180.0
                if abs(normalized_entity_rotation) > 1e-3:
                    return False
                rotation_delta = abs(entity_rotation - rotation) % 360.0
                rotation_delta = min(rotation_delta, 360.0 - rotation_delta)
                if rotation_delta > 1e-3:
                    return False
                extrusion = getattr(entity.dxf, "extrusion", None)
                if extrusion is not None and (
                    abs(float(extrusion[0])) > 1e-9
                    or abs(float(extrusion[1])) > 1e-9
                    or abs(float(extrusion[2]) - 1.0) > 1e-9
                ):
                    return False
                if int(getattr(entity.dxf, "text_generation_flag", 0) or 0) != 0:
                    return False

            angle = math.radians(rotation)
            u_x, u_y = math.cos(angle), math.sin(angle)
            v_x, v_y = -u_y, u_x
            min_u = math.inf
            max_u = -math.inf
            min_v = math.inf
            max_v = -math.inf

            for entity in entities:
                halign = int(getattr(entity.dxf, "halign", 0) or 0)
                valign = int(getattr(entity.dxf, "valign", 0) or 0)
                point = None
                if halign != 0 or valign != 0:
                    point = getattr(entity.dxf, "align_point", None)
                if point is None:
                    point = entity.dxf.insert
                x, y = float(point[0]), float(point[1])
                height = max(float(getattr(entity.dxf, "height", 0.0) or 0.0), 1e-6)
                width_factor = max(
                    float(getattr(entity.dxf, "width", 1.0) or 1.0),
                    0.05,
                )
                text_width = max(
                    estimate_text_width(
                        str(getattr(entity.dxf, "text", "") or ""),
                        height,
                        0.6 * width_factor,
                    ),
                    height,
                )
                projected_u = x * u_x + y * u_y
                projected_v = x * v_x + y * v_y
                min_u = min(min_u, projected_u)
                max_u = max(max_u, projected_u + text_width)
                min_v = min(min_v, projected_v)
                max_v = max(max_v, projected_v + height)

            source_box_width = max(max_u - min_u, 1e-6)
            box_width = source_box_width
            box_height = max(max_v - min_v, 1e-6)
            if info.single_text_block and info.max_height is not None:
                # 单个 TEXT 以前会被当作“非合并项”直接塞回单行实体，最终只能
                # 把字号压到约 5%。当外框扫描已经证明下方有可用空间时，把世界
                # 坐标宽高按原实体的世界/局部比例还原，让它与其他文本块一样换行。
                local_primary_height = max(
                    float(getattr(primary_entity.dxf, "height", 0.0) or 0.0),
                    1e-6,
                )
                world_primary_height = max(float(info.primary_height), 1e-6)
                local_height_budget = (
                    float(info.max_height)
                    * local_primary_height
                    / world_primary_height
                )
                box_height = max(box_height, local_height_budget)
                if info.reliable_width and info.source_group_width > 0:
                    local_width_budget = (
                        float(info.group_width)
                        * source_box_width
                        / float(info.source_group_width)
                    )
                    box_width = max(
                        local_width_budget,
                        local_primary_height * 2.0,
                    )
            # attachment_point=1 是左上角：沿 u 从 min_u 开始，换行沿 -v 推进。
            insert_x = min_u * u_x + max_v * v_x
            insert_y = min_u * u_y + max_v * v_y
            # 这里在块定义的局部坐标中创建实体，必须使用原实体局部字高；
            # info.primary_height 已包含 INSERT 缩放，直接使用会被父块再次放大。
            original_height = max(
                float(
                    getattr(primary_entity.dxf, "height", 0.0)
                    or getattr(primary_entity.dxf, "char_height", 0.0)
                    or 0.0
                ),
                1e-6,
            )
            mtext_text = (
                info.target_text
                .replace("\r\n", r"\P")
                .replace("\r", r"\P")
                .replace("\n", r"\P")
            )
            # 兜底：合并组由 INSERT 变换后的碎片串接而成、单段严重超宽或跨语种
            # 拼接时，在语言边界强插 \P 避免整行溢出（见 _break_over_wide_paragraphs）
            mtext_text = _break_over_wide_paragraphs(
                mtext_text,
                info.primary_height,
                info.group_width,
            )
            dxfattribs = {
                "insert": (insert_x, insert_y, 0),
                "char_height": original_height,
                "rotation": rotation,
                "layer": str(getattr(primary_entity.dxf, "layer", info.layer) or info.layer),
                "attachment_point": 1,
                "width": box_width,
                "line_spacing_factor": 1.0,
                "line_spacing_style": 2,
                "color": info.primary_color,
            }
            if info.primary_true_color is not None:
                dxfattribs["true_color"] = info.primary_true_color
            if info.primary_transparency is not None:
                dxfattribs["transparency"] = info.primary_transparency
            mtext = target_space.add_mtext(mtext_text, dxfattribs=dxfattribs)

            style_name = str(
                getattr(primary_entity.dxf, "style", info.primary_style)
                or info.primary_style
            )
            if style_name:
                mtext.dxf.style = style_name
            if unicode_style_name and self._has_non_ascii(info.target_text):
                self._apply_unicode_style(mtext, unicode_style_name)

            # 现有拟合器测量世界 Y 高度。临时转成 0° 后，Y 正好对应文字
            # 坐标系的 v 高度；拟合结束再恢复原角度，不改变最终方向。
            mtext.dxf.rotation = 0.0
            self._fit_mtext_to_max_height(mtext, box_height)
            mtext.dxf.rotation = rotation

            logger.info(
                "创建方向保持 MTEXT primary=%s scope=%s rotation=%.3f "
                "box=%.2fx%.2f h=%.4f",
                info.primary_handle,
                info.scope,
                rotation,
                box_width,
                box_height,
                float(mtext.dxf.char_height),
            )
            return True
        except Exception as exc:  # noqa: BLE001 - 失败时必须回退到原实体
            if mtext is not None and target_space is not None:
                try:
                    target_space.delete_entity(mtext)
                except Exception:  # noqa: BLE001
                    pass
            logger.warning(
                "方向保持 MTEXT 创建失败 primary=%s scope=%s: %s",
                info.primary_handle,
                info.scope,
                exc,
            )
            return False

    def _create_merged_mtext_entities(
        self,
        doc,
        merged_infos: List[MergedTextExportInfo],
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        unicode_style_name: Optional[str] = None,
    ) -> Set[str]:
        """创建最终 MTEXT，并仅在可靠矩形内按真实 bbox 求最大字号。"""
        created: Set[str] = set()

        for info in merged_infos:
            try:
                # 方向敏感的块内 TEXT 不能使用世界坐标创建默认 MTEXT；优先在
                # 原块局部文字矩形中创建同角度 MTEXT。无法安全恢复矩形时才
                # fail closed，回退到原主实体写入。
                try:
                    primary_entity = doc.entitydb.get(info.primary_handle)
                except Exception:  # noqa: BLE001
                    primary_entity = None

                if info.single_text_block:
                    original_height = max(float(info.primary_height), 1e-6)
                    source_width = estimate_text_width(
                        info.source_text,
                        original_height,
                    )
                    box_width = max(
                        float(info.group_width or 0),
                        original_height,
                    )
                    target_width = estimate_text_width(
                        clean_mtext(info.target_text or ""),
                        original_height,
                    )
                    has_wrapping_room = bool(
                        info.reliable_width
                        and info.max_height is not None
                        and info.max_height > original_height * 1.5
                        and info.group_width > original_height * 2.0
                    )
                    entity_type = (
                        primary_entity.dxftype()
                        if primary_entity is not None
                        else ""
                    )
                    overflows_width = bool(
                        target_width > box_width * opts.shrink_threshold
                    )
                    if (
                        entity_type != "TEXT"
                        or not has_wrapping_room
                        or not overflows_width
                    ):
                        logger.debug(
                            "单文本块保留原位 primary=%s type=%s "
                            "overflow=%s room=%s source_w=%.2f target_w=%.2f",
                            info.primary_handle,
                            entity_type,
                            overflows_width,
                            has_wrapping_room,
                            source_width,
                            target_width,
                        )
                        continue

                if (
                    primary_entity is not None
                    and self._mtext_requires_in_place_preservation(
                        primary_entity, info.scope
                    )
                ):
                    if self._create_oriented_merged_mtext(
                        doc,
                        info,
                        opts,
                        unicode_style_name=unicode_style_name,
                    ):
                        created.add(info.primary_handle)
                        if stats is not None:
                            stats["oriented_mtext_created"] = (
                                stats.get("oriented_mtext_created", 0) + 1
                            )
                    else:
                        if stats is not None:
                            stats["mtext_preserved_in_place"] = (
                                stats.get("mtext_preserved_in_place", 0) + 1
                            )
                        logger.info(
                            "跳过 MTEXT 重建并保留原实体 primary=%s scope=%s",
                            info.primary_handle,
                            info.scope,
                        )
                    continue

                original_height = max(float(info.primary_height), 1e-6)
                width_ratio = (float(info.group_width) or 0) / original_height
                height_ratio = (float(info.group_height) or 0) / original_height
                has_reliable_bounds = bool(
                    info.max_height is not None
                    and info.max_height > 0
                    and info.group_width > 0
                )
                if not has_reliable_bounds and (width_ratio > 60 or height_ratio > 8):
                    logger.warning(
                        "跳过 MTEXT 重建(几何异常) primary=%s width/h=%.1f height/h=%.1f "
                        "count=%d text=%r",
                        info.primary_handle,
                        width_ratio,
                        height_ratio,
                        len(info.merged_handles),
                        info.target_text[:50],
                    )
                    if stats is not None:
                        stats["mtext_skipped_geometry"] = stats.get("mtext_skipped_geometry", 0) + 1
                    continue

                target_space = doc.modelspace()
                if info.scope.startswith("layout:"):
                    layout_name = info.scope.removeprefix("layout:").split(":insert:", 1)[0]
                    target_space = doc.layouts.get(layout_name)
                elif info.scope.startswith("block:"):
                    block_name = info.scope.removeprefix("block:").split(":insert:", 1)[0]
                    target_space = doc.blocks.get(block_name)

                source_width = estimate_text_width(info.source_text, original_height)
                box_width = max(
                    float(info.group_width) if info.group_width > 0 else source_width,
                    1e-6,
                )
                display_height = max(
                    float(info.max_height or 0),
                    float(info.group_height or 0),
                    original_height,
                )

                if info.cad_table_cell and info.group_width > 0 and info.group_height > 0:
                    insert_x = info.group_x + info.group_width / 2.0
                    insert_y = info.group_y_top - info.group_height / 2.0
                    attachment_point = 5
                else:
                    insert_x = info.group_x if info.group_width > 0 else info.primary_x
                    insert_y = info.group_y_top if info.group_height > 0 else info.primary_y
                    attachment_point = 1

                mtext_text = (
                    info.target_text
                    .replace("\r\n", r"\P")
                    .replace("\r", r"\P")
                    .replace("\n", r"\P")
                )
                # 兜底：合并组由 INSERT 变换后的碎片串接而成、单段严重超宽或
                # 跨语种拼接时，在语言边界强插 \P 避免整行溢出。
                mtext_text = _break_over_wide_paragraphs(
                    mtext_text,
                    info.primary_height,
                    info.group_width,
                )
                if (
                    not info.cad_table_cell
                    and not info.preserve_mtext_layout
                    and info.first_line_indent > 0
                    and mtext_text
                ):
                    space_width = max(
                        estimate_text_width(" ", original_height),
                        original_height * 0.3,
                        1e-6,
                    )
                    indent_spaces = min(
                        max(int(round(info.first_line_indent / space_width)), 1),
                        64,
                    )
                    mtext_text = (r"\~" * indent_spaces) + mtext_text

                dxfattribs = {
                    "insert": (insert_x, insert_y, 0),
                    "char_height": original_height,
                    "layer": info.layer,
                    "attachment_point": attachment_point,
                    "width": box_width,
                    "line_spacing_factor": 1.0,
                    "line_spacing_style": 2,
                    "color": info.primary_color,
                }
                if info.primary_true_color is not None:
                    dxfattribs["true_color"] = info.primary_true_color
                if info.primary_transparency is not None:
                    dxfattribs["transparency"] = info.primary_transparency
                mtext = target_space.add_mtext(mtext_text, dxfattribs=dxfattribs)
                if info.primary_style:
                    try:
                        mtext.dxf.style = info.primary_style
                    except Exception:  # noqa: BLE001
                        pass
                if unicode_style_name and self._has_non_ascii(info.target_text):
                    self._apply_unicode_style(mtext, unicode_style_name)
                if (
                    info.max_height is not None
                    and info.max_height > 0
                    and opts.enable_overflow_shrink
                ):
                    self._fit_mtext_to_max_height(mtext, info.max_height)

                final_height = float(mtext.dxf.char_height)
                created.add(info.primary_handle)
                if stats is not None:
                    stats["mtext_created"] = stats.get("mtext_created", 0) + 1
                logger.info(
                    "创建合并 MTEXT [primary=%s, layer=%s, box=%.2fx%.2f, height=%.4f]: %s",
                    info.primary_handle,
                    info.layer,
                    box_width,
                    display_height,
                    final_height,
                    info.target_text[:50],
                )
            except Exception as exc:
                logger.error("创建合并 MTEXT 失败 [%s]: %s", info.primary_handle, exc)

        return created



    def _replace_in_entity(
        self,
        entity,
        translations: Dict[str, str],
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        audit: Optional[list[dict]] = None,
        *,
        unicode_style_name: Optional[str] = None,
    ) -> None:
        dxftype = entity.dxftype()

        if dxftype == "INSERT":
            for attrib in entity.attribs:
                self._replace_in_entity(
                    attrib, translations, opts, stats, audit,
                    unicode_style_name=unicode_style_name,
                )
            return

        def _record(hit: bool, original: str, *, replaced: str = "", reason: str = "") -> None:
            if stats is not None:
                stats["total"] += 1
                if hit:
                    stats["hit"] += 1
                else:
                    stats["miss"] += 1
                    if original.strip():
                        snippet = original.strip().replace("\n", " ")[:60]
                        logger.debug("DXF 漏匹配 [%s]: %s", dxftype, snippet)
            if audit is not None:
                audit.append(
                    {
                        "handle": getattr(entity.dxf, "handle", ""),
                        "entity_type": dxftype,
                        "layer": getattr(entity.dxf, "layer", ""),
                        "source": original,
                        "target": replaced,
                        "status": "hit" if hit else "miss",
                        "reason": reason,
                    }
                )

        if dxftype == "TEXT":
            current = getattr(entity.dxf, "text", "") or ""
            if not current.strip():
                return
            handle = str(getattr(entity.dxf, "handle", "") or "")
            handle_key = CAD_TEXT_HANDLE_TRANSLATION_PREFIX + handle
            preserve_original_height = handle_key in translations
            new_value = translations.get(handle_key)
            if new_value is None:
                new_value = self._lookup(current, translations)
            # 整段找不到时，尝试按句子拆分后逐句翻译再拼接
            if new_value is None:
                new_value = self._merge_sentence_translations(current, translations)
            if new_value is not None and new_value != current:
                entity.dxf.text = new_value
                if opts.enable_overflow_shrink and not preserve_original_height:
                    self._shrink_text_entity(entity, current, new_value, opts)
                # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
                if unicode_style_name and self._has_non_ascii(new_value):
                    self._apply_unicode_style(entity, unicode_style_name)
                _record(True, current, replaced=new_value)
            else:
                _record(False, current, reason="not_in_translations" if new_value is None else "same")
            return

        if dxftype == "MTEXT":
            raw = entity.text or ""
            cleaned = clean_mtext(raw)
            if not cleaned.strip():
                return
            # 优先按实体 handle 使用完整 MTEXT 映射；相同原文在不同实体中
            # 可以有不同译文，不能先退化为 source_text 全局匹配。
            handle = str(getattr(entity.dxf, "handle", "") or "")
            block_key = CAD_MTEXT_HANDLE_BLOCK_PREFIX + handle
            if block_key in translations:
                _record(False, cleaned, reason="incomplete_mtext_translation")
                return

            handle_key = CAD_MTEXT_HANDLE_TRANSLATION_PREFIX + handle
            new_value = translations.get(handle_key)
            if new_value is None:
                source_block_key = CAD_MTEXT_SOURCE_BLOCK_PREFIX + cleaned
                if source_block_key in translations:
                    _record(False, cleaned, reason="incomplete_mtext_translation")
                    return
                new_value = self._lookup(cleaned, translations)
            if new_value is None:
                new_value = self._lookup(raw, translations)
            if new_value is None:
                new_value = self._merge_mtext_paragraph_translations(cleaned, translations)
            if new_value is None:
                _record(False, cleaned, reason="not_in_translations")
                return
            # 保留原 MTEXT 的富格式骨架。直接赋纯译文会删除 \p 首行缩进、
            # \C 颜色、\H 字高和 \P 段落边界，正是译文整体左移且挤成一块的原因。
            formatted_value = _apply_original_mtext_layout(raw, cleaned, new_value)
            entity.text = formatted_value
            # 字体会改变字宽、自动换行和 bbox；必须先切换到最终样式再测量。
            if (
                unicode_style_name
                and self._has_non_ascii(new_value)
                and _mtext_has_unformatted_non_ascii(formatted_value)
            ):
                self._apply_unicode_style(entity, unicode_style_name)
            max_height_raw = translations.get(
                CAD_MTEXT_MAX_HEIGHT_PREFIX + handle
            )
            if max_height_raw is not None and opts.enable_overflow_shrink:
                try:
                    max_height = float(max_height_raw)
                except (TypeError, ValueError):
                    max_height = 0.0
                if max_height > 0:
                    self._fit_mtext_to_max_height(entity, max_height)
            # MTEXT 已有固定 width，会按原框宽自动换行；没有可靠高度矩形时
            # 不能再按中英文字符数比例缩字。有矩形约束时由上面的二分搜索
            # 直接求出“不越界的最大字号”。
            _record(True, cleaned, replaced=new_value)
            return

        if dxftype in {"ATTRIB", "ATTDEF"}:
            current = getattr(entity.dxf, "text", "") or ""
            if not current.strip():
                return
            new_value = self._lookup(current, translations)
            if new_value is not None and new_value != current:
                entity.dxf.text = new_value
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, current, new_value, opts)
                # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
                if unicode_style_name and self._has_non_ascii(new_value):
                    self._apply_unicode_style(entity, unicode_style_name)
                _record(True, current, replaced=new_value)
            else:
                _record(False, current, reason="not_in_translations" if new_value is None else "same")
            return

        if dxftype == "DIMENSION":
            current = getattr(entity.dxf, "text", "") or ""
            if not current or current.strip() in {"", "<>"}:
                return
            new_value = self._lookup(current, translations)
            if new_value is not None and new_value != current:
                entity.dxf.text = new_value
                _record(True, current, replaced=new_value)
            else:
                _record(False, current, reason="not_in_translations" if new_value is None else "same")
            return

        if opts.handle_extra_entities:
            if dxftype == "MULTILEADER":
                self._replace_multileader(entity, translations, opts)
                return
            if dxftype == "ACAD_TABLE":
                self._replace_acad_table(entity, translations, opts)
                return

        # 其它 _TEXT_ENTITY_TYPES 中的类型在不开 handle_extra_entities 时跳过
        if dxftype not in _TEXT_ENTITY_TYPES:
            return

    # ---------------------------------------------------------------------
    # 缩放 / 溢出处理
    # ---------------------------------------------------------------------

    @staticmethod
    def _shrink_text_entity(
        entity,
        original: str,
        translated: str,
        opts: DxfExportOptions,
    ) -> None:
        """单行 TEXT/ATTRIB 只按比例缩字高，保留原字宽因子。

        SHX 字体把 width factor 压到 0.55 左右时，字形会像截图一样互相覆盖。
        使用 CJK/ASCII 视觉宽度比直接缩字高，可保持字形完整，同时让译文占用
        与原文近似的水平空间。
        """
        try:
            orig_len = _visual_length(original)
            new_len = _visual_length(translated)
            if orig_len <= 0 or new_len <= 0:
                return

            ratio = new_len / orig_len
            if ratio <= opts.shrink_threshold:
                return

            current_height = float(getattr(entity.dxf, "height", 0) or 0)
            if current_height <= 0:
                return

            # 预留少量右侧安全间距。下限允许低于全局默认 0.5，避免超长英文
            # 在表格碎片中继续压到相邻实体；空间重建路径仍会优先使用 MTEXT。
            minimum_ratio = min(max(opts.min_char_height_ratio, 0.05), 0.25)
            height_ratio = max(minimum_ratio, 0.96 / ratio)
            if height_ratio < 1.0:
                entity.dxf.height = round(current_height * height_ratio, 4)
        except Exception as exc:  # noqa: BLE001
            logger.debug("shrink TEXT 失败: %s", exc)

    @staticmethod
    def _fit_mtext_to_max_height(
        entity,
        max_height: float,
    ) -> None:
        """二分搜索不超过矩形高度的最大 MTEXT 字高。"""
        original_height = float(getattr(entity.dxf, "char_height", 0) or 0)
        box_width = float(getattr(entity.dxf, "width", 0) or 0)
        if original_height <= 0 or box_width <= 0 or max_height <= 0:
            return

        line_spacing = float(
            getattr(entity.dxf, "line_spacing_factor", 1.0) or 1.0
        )

        def estimated_height(height: float) -> float:
            entity.dxf.char_height = max(height, 1e-6)
            try:
                from ezdxf import bbox as ezdxf_bbox

                extents = ezdxf_bbox.extents([entity], fast=True)
                if extents.has_data and float(extents.size.y) > 0:
                    return float(extents.size.y)
            except Exception as exc:  # noqa: BLE001
                logger.debug("MTEXT bbox 高度测量失败: %s", exc)

            # 字体资源缺失时的保守回退：按框宽显式估算每段行数。
            cleaned = clean_mtext(entity.text or "")
            line_count = 0
            for paragraph in cleaned.splitlines() or [cleaned]:
                paragraph_width = estimate_text_width(paragraph, height)
                line_count += max(1, math.ceil(paragraph_width / box_width))
            return max(line_count, 1) * height * (5.0 / 3.0) * line_spacing

        # 给不同 CAD 字体渲染器预留 2% 余量，避免数学上刚好等于边界时压线。
        height_budget = max_height * 0.98
        rendered_at_original = estimated_height(original_height)
        if rendered_at_original <= height_budget:
            entity.dxf.char_height = original_height
            logger.info(
                "MTEXT 矩形内无需缩放 handle=%s rendered=%.2f budget=%.2f h=%.4f",
                getattr(entity.dxf, "handle", ""),
                rendered_at_original,
                height_budget,
                original_height,
            )
            return

        low = original_height * 0.05
        while low > 1e-6 and estimated_height(low) > height_budget:
            low *= 0.5
        high = original_height
        for _ in range(40):
            middle = (low + high) / 2.0
            if estimated_height(middle) <= height_budget:
                low = middle
            else:
                high = middle

        fitted_height = max(low * 0.998, 1e-6)
        rendered_height = estimated_height(fitted_height)
        if rendered_height > height_budget:
            fitted_height *= height_budget / rendered_height * 0.995
            rendered_height = estimated_height(fitted_height)
        entity.dxf.char_height = round(fitted_height, 4)
        logger.info(
            "MTEXT 矩形最大字号 handle=%s box=%.2fx%.2f rendered=%.2f h=%.4f->%.4f",
            getattr(entity.dxf, "handle", ""),
            box_width,
            max_height,
            rendered_height,
            original_height,
            fitted_height,
        )

    @staticmethod
    def _shrink_mtext_entity(
        entity,
        original: str,
        translated: str,
        opts: DxfExportOptions,
    ) -> None:
        """MTEXT：给定 box 宽度让 ezdxf 自动换行，必要时缩字宽因子和字高。"""
        try:
            orig_len = _visual_length(original)
            new_len = _visual_length(translated)
            if orig_len <= 0 or new_len <= 0:
                return
            
            # 计算字符数比例（不考虑视觉宽度，纯字符数）
            char_ratio = len(translated) / max(len(original), 1)
            # 取视觉比例和字符比例中较大的
            ratio = max(new_len / orig_len, char_ratio)
            
            if ratio <= opts.shrink_threshold:
                return

            char_height = float(getattr(entity.dxf, "char_height", 0) or 0)
            current_box_width = float(getattr(entity.dxf, "width", 0) or 0)

            # 没有 box 宽度时给一个估算值，按原文视觉宽度 × 0.6 × 字高
            if char_height > 0 and current_box_width <= 0:
                estimated = orig_len * char_height * 0.6
                if estimated > 0:
                    entity.dxf.width = round(estimated, 4)

            # 译文长就直接按比例缩字高（曲线更陡，溢出越多缩越多）
            if char_height > 0 and ratio > 1.05:
                shrink = max(opts.min_char_height_ratio, 1.0 / ratio)
                if shrink < 1.0:
                    entity.dxf.char_height = round(char_height * shrink, 4)
        except Exception as exc:  # noqa: BLE001
            logger.debug("shrink MTEXT 失败: %s", exc)

    # ---------------------------------------------------------------------
    # 扩展实体
    # ---------------------------------------------------------------------

    def _replace_multileader(
        self,
        entity,
        translations: Dict[str, str],
        opts: DxfExportOptions,
    ) -> None:
        """MULTILEADER (MLEADER)：替换 context.mtext 里的文本。"""
        try:
            mtext = getattr(getattr(entity, "context", None), "mtext", None)
            current_attr: Optional[str] = None
            current_value = ""
            if mtext is not None:
                for attr in ("default_content", "text"):
                    value = getattr(mtext, attr, None)
                    if value:
                        current_attr = attr
                        current_value = str(value)
                        break

            if not current_value:
                # 兜底：dxf.default_content
                current_value = getattr(entity.dxf, "default_content", "") or ""

            if not current_value:
                return

            cleaned = clean_mtext(current_value)
            new_value = self._lookup(cleaned, translations) or self._lookup(current_value, translations)
            if not new_value or new_value == current_value:
                return

            if mtext is not None and current_attr is not None:
                setattr(mtext, current_attr, new_value)
            try:
                entity.dxf.default_content = new_value
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("替换 MULTILEADER 失败: %s", exc)

    def _replace_acad_table(
        self,
        entity,
        translations: Dict[str, str],
        opts: DxfExportOptions,
    ) -> None:
        """ACAD_TABLE：逐单元格替换。ezdxf 对 ACAD_TABLE 的写支持较弱，失败就跳过。"""
        n_rows = getattr(entity.dxf, "n_rows", 0) or 0
        n_cols = getattr(entity.dxf, "n_cols", 0) or 0
        for row in range(n_rows):
            for col in range(n_cols):
                try:
                    raw = entity.get_text(row, col) or ""
                except Exception:  # noqa: BLE001
                    continue
                if not raw:
                    continue
                cleaned = clean_mtext(raw)
                new_value = self._lookup(cleaned, translations) or self._lookup(raw, translations)
                if not new_value or new_value == raw:
                    continue
                try:
                    entity.set_text(row, col, new_value)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("替换 ACAD_TABLE 单元格失败 (%d,%d): %s", row, col, exc)

    def _merge_mtext_paragraph_translations(
            self,
            cleaned: str,
            translations: Dict[str, str],
        ) -> Optional[str]:
            """按段落、句子及跨段组合回写 MTEXT 译文。"""
            parts = cleaned.split("\n")
            translated_parts: list[str] = []
            any_hit = False
            missing_translatable_part = False
            index = 0

            while index < len(parts):
                part = parts[index]
                stripped = part.strip()
                if not stripped:
                    translated_parts.append(part)
                    index += 1
                    continue

                replacement = self._lookup(stripped, translations)
                if replacement is None:
                    replacement = self._merge_sentence_translations(stripped, translations)

                consumed = 1
                if replacement is None:
                    # 一个数据库句段可能跨多个 MTEXT \P 段。优先匹配最长连续范围，
                    # 例如标题段加后续多条说明共同组成一个翻译单元。
                    for end in range(len(parts), index + 1, -1):
                        candidate = "\n".join(parts[index:end]).strip()
                        replacement = self._lookup(candidate, translations)
                        if replacement is not None:
                            consumed = end - index
                            break

                if replacement is None:
                    translated_parts.append(part)
                    # 允许纯数字/管径代码原样保留，但可翻译文字一旦漏匹配，
                    # 就不能把其余段落先写回，否则会生成同一标注框内的中英混排。
                    if any(char.isalpha() for char in stripped) and not _is_dimension_like(stripped):
                        missing_translatable_part = True
                else:
                    translated_parts.append(replacement)
                    any_hit = True
                index += consumed

            if not any_hit or missing_translatable_part:
                return None
            return "\\P".join(translated_parts)


    def _merge_sentence_translations(
        self,
        text: str,
        translations: Dict[str, str],
    ) -> Optional[str]:
        """按句子拆分后逐句翻译再拼接。

        用于 TEXT 实体整段找不到翻译时的兜底处理。
        segment_extractor 会按句号分割，导致原始长文本被拆成多个句子，
        但 DXF 实体中仍是整段，导出时需要按句子逐一查找翻译再拼接。
        """
        if not text or not text.strip():
            return None
        
        # 中文和英文句子结束标点
        sentence_endings = "。？！!?."
        
        # 按句子结束标点拆分，但保留标点
        sentences: list[str] = []
        current = ""
        for i, char in enumerate(text):
            current += char
            if char in sentence_endings:
                # 英文句号后面需要有空格才算句子结束，或者是文本结尾
                if char == ".":
                    next_idx = i + 1
                    if next_idx < len(text) and text[next_idx] not in " \t\n":
                        # 句号后面紧跟非空白字符，不分句
                        continue
                sentences.append(current)
                current = ""
        if current.strip():
            sentences.append(current)
        
        if len(sentences) <= 1:
            # 只有一个句子，无需拆分
            return None
        
        translated_parts: list[str] = []
        any_hit = False
        missing_translatable_sentence = False
        
        for sentence in sentences:
            stripped = sentence.strip()
            if not stripped:
                translated_parts.append(sentence)
                continue
            
            replacement = self._lookup(stripped, translations)
            if replacement is None:
                # 尝试不带结尾标点的匹配
                without_ending = stripped.rstrip(sentence_endings)
                if without_ending and without_ending != stripped:
                    replacement = self._lookup(without_ending, translations)
                    if replacement:
                        # 补回标点
                        ending = stripped[len(without_ending):]
                        replacement = replacement + ending
            
            if replacement is None:
                translated_parts.append(sentence)
                if any(char.isalpha() for char in stripped) and not _is_dimension_like(stripped):
                    missing_translatable_sentence = True
            else:
                translated_parts.append(replacement)
                any_hit = True
        
        if not any_hit or missing_translatable_sentence:
            return None
        
        return "".join(translated_parts)

    @staticmethod
    def _lookup(value: str, translations: Dict[str, str]) -> Optional[str]:
        if value is None:
            return None
        if value in translations:
            return translations[value]
        stripped = value.strip()
        if stripped and stripped in translations:
            return translations[stripped]
        # 段内多空白的兜底匹配
        normalized = re.sub(r"\s+", " ", stripped)
        if normalized and normalized in translations:
            return translations[normalized]
        # 移除所有空白后的兜底匹配（处理 ODA 转换可能产生的空白差异）
        no_space = re.sub(r"\s", "", stripped)
        if no_space and no_space in translations:
            return translations[no_space]
        return None

    @staticmethod
    def _has_non_ascii(text: str) -> bool:
        """检查文本是否包含非 ASCII 字符（如西班牙语重音字母）。"""
        if not text:
            return False
        for ch in text:
            if ord(ch) > 127:
                return True
        return False

    @staticmethod
    def _ensure_unicode_style(doc, font_name: str) -> str:
        """确保文档中存在支持 Unicode 的文本样式，返回样式名称。"""
        style_name = "_UNICODE_EXPORT"
        try:
            # 检查样式是否已存在
            if style_name in doc.styles:
                return style_name
            # 创建新的文本样式，使用 TrueType 字体
            style = doc.styles.new(style_name)
            style.dxf.font = font_name
            # 不设置 bigfont，让 CAD 软件自动处理
            style.dxf.bigfont = ""
            logger.debug("创建 Unicode 文本样式: %s, 字体: %s", style_name, font_name)
            return style_name
        except Exception as exc:
            logger.warning("创建 Unicode 文本样式失败: %s", exc)
            return ""

    @staticmethod
    def _apply_unicode_style(entity, style_name: str) -> None:
        """将实体的文本样式切换为支持 Unicode 的样式。"""
        if not style_name:
            return
        try:
            entity.dxf.style = style_name
        except Exception as exc:
            logger.debug("应用 Unicode 样式失败: %s", exc)
