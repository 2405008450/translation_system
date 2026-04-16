"""
Segment 提取模块 - 从 Document AST 中提取翻译片段

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
"""
import hashlib
import re
from typing import List, Tuple

from app.services.adapters.models import BlockNode, DocumentAST, NodeType, Segment


# 句子结束标点
SENTENCE_ENDINGS = "。？！!?."
TRAILING_CLOSERS = '"\'\"\'》】）)]」』'


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
                sentences = self._split_sentences(text)
                for sentence_text, display_text in sentences:
                    if sentence_text:  # 跳过空句子
                        segment = self._create_segment(
                            source_text=sentence_text,
                            display_text=display_text,
                            block_path=path,
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
            
            if char in SENTENCE_ENDINGS:
                # 找到句子结束标点
                end = i + 1
                
                # 跳过连续的结束标点
                while end < len(text) and text[end] in SENTENCE_ENDINGS:
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
    ) -> Segment:
        """创建 Segment 实例
        
        Args:
            source_text: 规范化后的源文本
            display_text: 原始显示文本
            block_path: 在 AST 中的路径
            
        Returns:
            Segment: 新创建的 Segment 实例
        """
        position = self._position_counter
        self._position_counter += 1
        
        # 生成内容哈希
        content_hash = hashlib.md5(source_text.encode()).hexdigest()[:8]
        
        # 生成稳定 ID
        segment_id = Segment.generate_id(block_path, position, content_hash)
        
        return Segment(
            segment_id=segment_id,
            source_text=source_text,
            display_text=display_text,
            block_path=block_path,
            position=position,
        )


def extract_segments(ast: DocumentAST) -> List[Segment]:
    """便捷函数：从 AST 提取 Segment 列表"""
    extractor = SegmentExtractor()
    return extractor.extract(ast)
