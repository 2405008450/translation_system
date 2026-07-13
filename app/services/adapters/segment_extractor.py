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
                    sentences = self._split_sentences(text)
                    for sentence_text, display_text in sentences:
                        if sentence_text:  # 跳过空句子
                            segment = self._create_segment(
                                source_text=sentence_text,
                                display_text=display_text,
                                block_path=path,
                                metadata=node.metadata,
                            )
                            segments.append(segment)
        
        # 递归处理子节点
        if node.children:
            for idx, child in enumerate(node.children):
                child_path = f"{path}.children.{idx}"
                child_segments = self._extract_from_node(child, child_path)
                segments.extend(child_segments)
        
        return segments

    def _split_sentences(self, text: str) -> List[Tuple[str, str]]:
        """将文本分割为句子
        
        Args:
            text: 原始文本
            
        Returns:
            List[Tuple[str, str]]: (规范化文本, 显示文本) 元组列表
        """
        if not text:
            return []
        
        sentences: List[Tuple[str, str]] = []
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
                
                if source_text:
                    sentences.append((source_text, display_text))
                
                start = end
                i = end
            else:
                i += 1
        
        # 处理最后一个句子（可能没有结束标点）
        if start < len(text):
            display_text = text[start:].strip()
            source_text = self._normalize_text(display_text)
            if source_text:
                sentences.append((source_text, display_text))
        
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
        )


def extract_segments(ast: DocumentAST) -> List[Segment]:
    """便捷函数：从 AST 提取 Segment 列表"""
    extractor = SegmentExtractor()
    return extractor.extract(ast)
