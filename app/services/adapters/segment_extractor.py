"""
Segment 提取模块 - 从 Document AST 中提取翻译片段

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
"""
import re
from typing import List, Tuple

from app.services.adapters.models import BlockNode, DocumentAST, NodeType, Segment


# 句子结束标点
SENTENCE_ENDINGS = "。？！!?"
# 英文句号需要特殊处理，避免在序号后分割
ENGLISH_PERIOD = "."
TRAILING_CLOSERS = '"\'\"\'》】）)]」』'

# 序号模式：数字+点、字母+点、罗马数字+点
# 例如: 1. 2. 3. | a. b. c. | i. ii. iii. | A. B. C.
NUMBER_PREFIX_PATTERN = re.compile(
    r'^(\d+|[a-zA-Z]|[ivxIVX]+)\.\s*$'
)

# 常见英文缩写（不应在其后分句）
COMMON_ABBREVIATIONS = {
    'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'vs', 'etc', 'inc', 'ltd', 'co',
    'st', 'ave', 'blvd', 'rd', 'apt', 'no', 'vol', 'pp', 'ed', 'eds', 'trans',
    'fig', 'figs', 'approx', 'dept', 'est', 'govt', 'intl', 'natl', 'univ',
    'e.g', 'i.e', 'cf', 'al', 'et',
}


class SegmentExtractor:
    """Segment 提取器
    
    从 Document AST 中提取翻译片段，生成稳定的 Segment ID。
    """

    def __init__(self):
        self._position_counter = 0

    def extract(self, ast: DocumentAST) -> List[Segment]:
        """从 AST 中提取所有 Segment
        
        Args:
            ast: 文档抽象语法树
            
        Returns:
            List[Segment]: 按文档顺序排列的 Segment 列表
        """
        self._position_counter = 0
        segments: List[Segment] = []
        
        for idx, node in enumerate(ast.nodes):
            node_segments = self._extract_from_node(node, str(idx))
            segments.extend(node_segments)
        
        return segments

    def _extract_from_node(self, node: BlockNode, path: str) -> List[Segment]:
        """从单个节点提取 Segment
        
        Args:
            node: 块级节点
            path: 节点在 AST 中的路径
            
        Returns:
            List[Segment]: 从该节点提取的 Segment 列表
        """
        segments: List[Segment] = []
        
        # 如果节点有文本内容，提取句子
        if node.text_content:
            text = node.text_content.strip()
            if text:
                # 检查是否是 CAD 实体
                entity_type = node.metadata.get("entity_type", "")
                is_cad_entity = entity_type in (
                    "TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION", 
                    "MULTILEADER", "ACAD_TABLE", "MERGED_TEXT"
                )
                
                if is_cad_entity:
                    # CAD 实体：整体作为一个句段，不自动分割
                    # 用户可以在工作台手动分割或合并
                    segment = self._create_segment(
                        source_text=self._normalize_text(text),
                        display_text=text,
                        block_path=path,
                        metadata=node.metadata,
                    )
                    segments.append(segment)
                else:
                    # 其他格式：按句子分割
                    raw_sentences = self._split_sentences(text)
                    layout_fragments = self._compute_layout_fragments(node, text, raw_sentences)
                    html_fragments = self._compute_source_html_fragments(node, text, raw_sentences)
                    format_map = (node.metadata or {}).get("source_layout_formats") or {}
                    for idx, (sentence_text, display_text, _start, _end) in enumerate(raw_sentences):
                        if sentence_text:  # 跳过空句子
                            layout = layout_fragments[idx] if layout_fragments else ""
                            # 随句携带格式表（只取本句用到的 id + base），供前端渲染译文样式：
                            # - 统一样式句段：只带 base，让整段译文套用同一 run 样式；
                            # - 多样式句段：带 base + 本句 ⟦n⟧ 用到的 id。
                            seg_format_map: dict = {}
                            if format_map:
                                base_tokens = format_map.get("base")
                                if base_tokens and (base_tokens[0] or base_tokens[1]):
                                    seg_format_map["base"] = base_tokens
                                if "⟦" in layout:
                                    used_ids = set(re.findall(r"⟦\s*/?\s*(\d+)\s*⟧", layout))
                                    for used_id in used_ids:
                                        if used_id in format_map:
                                            seg_format_map[used_id] = format_map[used_id]
                            segment = self._create_segment(
                                source_text=sentence_text,
                                display_text=display_text,
                                block_path=path,
                                metadata=node.metadata,
                                source_layout_text=layout,
                                source_html=(
                                    html_fragments[idx] if html_fragments else ""
                                ),
                                source_format_map=seg_format_map,
                            )
                            segments.append(segment)
        
        # 递归处理子节点
        if node.children:
            for idx, child in enumerate(node.children):
                child_path = f"{path}.children.{idx}"
                child_segments = self._extract_from_node(child, child_path)
                segments.extend(child_segments)
        
        return segments

    def _compute_layout_fragments(
        self,
        node: BlockNode,
        text: str,
        raw_sentences: List[Tuple[str, str, int, int]],
    ) -> List[str] | None:
        """为逐句句段计算带行内格式标签的版式原文。

        仅当块节点携带 ``source_layout_tagged``（PPTX 解析注入的整段带标签文本）且其
        去标签后的纯文本与断句所依据的 ``text`` 完全一致时才对齐；标签跨句或纯文本
        不一致都返回 ``None``，调用方退回无标签路径（零倒退）。
        """
        tagged_text = (node.metadata or {}).get("source_layout_tagged")
        if not tagged_text or not raw_sentences:
            return None

        from app.services.adapters.pptx_inline_tags import (
            slice_tagged_paragraph,
            strip_format_tags,
        )

        if strip_format_tags(tagged_text) != text:
            return None

        bounds = [(start, end) for _source, _display, start, end in raw_sentences]
        return slice_tagged_paragraph(tagged_text, bounds)

    def _compute_source_html_fragments(
        self,
        node: BlockNode,
        text: str,
        raw_sentences: List[Tuple[str, str, int, int]],
    ) -> List[str] | None:
        """把逐句片段渲染为带基础格式的原文 HTML，供前端原文列展示样式。

        与 LLM 版式原文解耦：即使整段统一格式（无“异类” run、无标签）也会渲染，
        因此像“整段加粗/下划线”的标题也能在原文列显示样式。
        """
        tagged_html = (node.metadata or {}).get("source_layout_html_tagged")
        format_map = (node.metadata or {}).get("source_layout_formats")
        if not tagged_html or not format_map or not raw_sentences:
            return None

        from app.services.adapters.pptx_inline_tags import (
            slice_tagged_paragraph,
            strip_format_tags,
            tagged_fragment_to_html,
        )

        if strip_format_tags(tagged_html) != text:
            return None
        bounds = [(start, end) for _source, _display, start, end in raw_sentences]
        fragments = slice_tagged_paragraph(tagged_html, bounds)
        if fragments is None:
            return None
        return [tagged_fragment_to_html(fragment, format_map) for fragment in fragments]

    def _split_sentences(self, text: str) -> List[Tuple[str, str, int, int]]:
        """将文本分割为句子
        
        Args:
            text: 原始文本
            
        Returns:
            List[Tuple[str, str, int, int]]: (规范化文本, 显示文本, 起始偏移, 结束偏移)
            元组列表，按顺序连续覆盖整个 ``text``（含规范化后为空的片段，便于逐句对齐
            版式标签；调用方负责跳过空句子）。
        """
        if not text:
            return []
        
        sentences: List[Tuple[str, str, int, int]] = []
        start = 0
        i = 0
        
        while i < len(text):
            char = text[i]
            
            # 检查是否是句子结束标点
            is_sentence_end = False
            
            if char in SENTENCE_ENDINGS:
                is_sentence_end = True
            elif char == ENGLISH_PERIOD:
                # 英文句号需要特殊处理
                # 检查是否是序号后的点（如 "1." "a." "i."）
                prefix = text[start:i+1].strip()
                if NUMBER_PREFIX_PATTERN.match(prefix):
                    # 这是序号，不分割
                    is_sentence_end = False
                else:
                    # 检查是否是缩写
                    # 找到点号前的单词
                    word_before = ""
                    j = i - 1
                    while j >= start and (text[j].isalpha() or text[j] == '.'):
                        word_before = text[j] + word_before
                        j -= 1
                    word_before = word_before.rstrip('.')
                    
                    if word_before.lower() in COMMON_ABBREVIATIONS:
                        # 这是缩写，不分割
                        is_sentence_end = False
                    elif i + 1 >= len(text):
                        # 文本结束
                        is_sentence_end = True
                    elif i + 1 < len(text) and text[i + 1] in ' \n\r\t':
                        # 点号后有空格
                        # 检查是否是纯数字序号
                        before_dot = text[start:i].strip()
                        if before_dot and before_dot[-1].isdigit():
                            words = before_dot.split()
                            if words and words[-1].isdigit():
                                is_sentence_end = False
                            else:
                                is_sentence_end = True
                        else:
                            is_sentence_end = True
                    else:
                        is_sentence_end = False
            
            if is_sentence_end:
                # 找到句子结束标点
                end = i + 1
                
                # 跳过连续的结束标点
                while end < len(text) and text[end] in SENTENCE_ENDINGS + ENGLISH_PERIOD:
                    end += 1
                
                # 跳过尾随的引号等
                while end < len(text) and text[end] in TRAILING_CLOSERS:
                    end += 1
                
                display_text = text[start:end].strip()
                source_text = self._normalize_text(display_text)

                # 连续覆盖 [start, end)，规范化后为空的片段也保留以维持偏移对齐，
                # 由调用方跳过空句子。
                sentences.append((source_text, display_text, start, end))

                start = end
                i = end
            else:
                i += 1
        
        # 处理最后一个句子（可能没有结束标点）
        if start < len(text):
            display_text = text[start:].strip()
            source_text = self._normalize_text(display_text)
            sentences.append((source_text, display_text, start, len(text)))
        
        return sentences

    def _normalize_text(self, text: str) -> str:
        """规范化文本
        
        - 去除首尾空白
        - 合并连续空白为单个空格
        """
        if not text:
            return ""
        text = text.strip()
        text = re.sub(r'\s+', ' ', text)
        return text

    def _create_segment(
        self,
        source_text: str,
        display_text: str,
        block_path: str,
        metadata: dict = None,
        source_layout_text: str = "",
        source_html: str = "",
        source_format_map: dict = None,
    ) -> Segment:
        """创建 Segment 实例
        
        Args:
            source_text: 规范化后的源文本
            display_text: 原始显示文本
            block_path: 在 AST 中的路径
            metadata: 节点元数据（如 DXF 的 handle, layer, 合并信息等）
            
        Returns:
            Segment: 新创建的 Segment 实例
        """
        position = self._position_counter
        self._position_counter += 1
        
        # 使用补零顺序 ID，保证同一块内多句段按字符串排序时仍保持解析顺序。
        segment_id = f"seg-{position + 1:06d}"
        
        return Segment(
            segment_id=segment_id,
            source_text=source_text,
            display_text=display_text,
            block_path=block_path,
            position=position,
            metadata=metadata or {},
            source_layout_text=source_layout_text,
            source_html=source_html,
            source_format_map=source_format_map or {},
        )


def extract_segments(ast: DocumentAST) -> List[Segment]:
    """便捷函数：从 AST 提取 Segment 列表"""
    extractor = SegmentExtractor()
    return extractor.extract(ast)
