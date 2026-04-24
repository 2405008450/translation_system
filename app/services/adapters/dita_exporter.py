"""
DITA 导出器模块 - 将翻译结果导出为 DITA XML

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5
"""
from typing import Dict, List, Optional
from io import BytesIO

from lxml import etree

from app.services.adapters.exceptions import ExportError
from app.services.adapters.models import DocumentAST, BlockNode, NodeType


# NodeType 到 DITA 元素的反向映射
NODE_TO_DITA_MAP = {
    NodeType.SECTION: "section",
    NodeType.HEADING: "title",
    NodeType.PARAGRAPH: "p",
    NodeType.NOTE: "note",
    NodeType.LIST: "ul",
    NodeType.LIST_ITEM: "li",
    NodeType.TABLE: "table",
    NodeType.TABLE_ROW: "row",
    NodeType.TABLE_CELL: "entry",
    NodeType.CODE_BLOCK: "codeblock",
}


class DitaExporter:
    """DITA XML 导出器
    
    将 DocumentAST 和翻译结果导出为有效的 DITA XML。
    """

    def export(
        self,
        ast: DocumentAST,
        translations: Dict[str, str],
        original_bytes: Optional[bytes] = None,
    ) -> bytes:
        """导出 DITA 文件
        
        Args:
            ast: 文档 AST
            translations: 翻译映射 {segment_id: translated_text}
            original_bytes: 原始文件字节（用于保留结构）
            
        Returns:
            bytes: 导出的 DITA XML 字节
            
        Raises:
            ExportError: 当导出失败时
        """
        try:
            if original_bytes:
                # 基于原始文件替换文本
                return self._export_with_original(original_bytes, translations)
            else:
                # 从 AST 重建
                return self._export_from_ast(ast, translations)
        except Exception as e:
            raise ExportError(
                filename="<unknown>",
                reason=f"DITA 导出失败: {str(e)}"
            )

    def _export_with_original(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """基于原始文件替换文本
        
        Args:
            original_bytes: 原始文件字节
            translations: 翻译映射
            
        Returns:
            bytes: 导出的 DITA XML 字节
        """
        parser = etree.XMLParser(remove_blank_text=False, recover=True)
        root = etree.fromstring(original_bytes, parser=parser)
        
        # 遍历所有文本节点并替换
        segment_index = 0
        self._replace_text_in_element(root, translations, [segment_index])
        
        # 序列化为 XML
        return etree.tostring(
            root,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
        )

    def _replace_text_in_element(
        self,
        element: etree._Element,
        translations: Dict[str, str],
        index: List[int],
    ) -> None:
        """递归替换元素中的文本
        
        Args:
            element: lxml 元素
            translations: 翻译映射
            index: 当前段落索引（使用列表以便在递归中修改）
        """
        # 检查是否是 conref（跳过）
        if element.get("conref"):
            return
        
        # 替换元素的直接文本
        if element.text and element.text.strip():
            segment_id = f"seg_{index[0]}"
            if segment_id in translations:
                element.text = translations[segment_id]
            index[0] += 1
        
        # 递归处理子元素
        for child in element:
            self._replace_text_in_element(child, translations, index)

    def _export_from_ast(
        self,
        ast: DocumentAST,
        translations: Dict[str, str],
    ) -> bytes:
        """从 AST 重建 DITA 文件
        
        Args:
            ast: 文档 AST
            translations: 翻译映射
            
        Returns:
            bytes: 导出的 DITA XML 字节
        """
        # 创建根元素
        root = etree.Element("topic")
        root.set("id", "exported_topic")
        
        # 添加标题
        title = etree.SubElement(root, "title")
        title.text = "Exported Document"
        
        # 创建 body
        body = etree.SubElement(root, "body")
        
        # 转换 AST 节点
        segment_index = [0]
        for node in ast.nodes:
            self._convert_node_to_element(node, body, translations, segment_index)
        
        # 序列化为 XML
        return etree.tostring(
            root,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
        )

    def _convert_node_to_element(
        self,
        node: BlockNode,
        parent: etree._Element,
        translations: Dict[str, str],
        index: List[int],
    ) -> None:
        """将 BlockNode 转换为 DITA 元素
        
        Args:
            node: 块级节点
            parent: 父 XML 元素
            translations: 翻译映射
            index: 当前段落索引
        """
        # 获取原始 DITA 标签或使用映射
        dita_tag = None
        if node.metadata and "dita_tag" in node.metadata:
            dita_tag = node.metadata["dita_tag"]
        else:
            dita_tag = NODE_TO_DITA_MAP.get(node.node_type, "p")
        
        # 跳过某些容器节点
        if dita_tag in ("topic", "concept", "task", "reference", "body", "conbody", "taskbody", "refbody"):
            # 直接处理子节点
            if node.children:
                for child in node.children:
                    self._convert_node_to_element(child, parent, translations, index)
            return
        
        # 创建元素
        element = etree.SubElement(parent, dita_tag)
        
        # 恢复属性
        if node.metadata:
            for key, value in node.metadata.items():
                if key.startswith("attr_"):
                    attr_name = key[5:]  # 移除 "attr_" 前缀
                    element.set(attr_name, str(value))
        
        # 设置文本内容
        if node.text_content:
            segment_id = f"seg_{index[0]}"
            if segment_id in translations:
                element.text = translations[segment_id]
            else:
                element.text = node.text_content
            index[0] += 1
        
        # 处理子节点
        if node.children:
            for child in node.children:
                self._convert_node_to_element(child, element, translations, index)

    def validate(self, xml_bytes: bytes) -> bool:
        """验证 DITA XML 是否有效
        
        Args:
            xml_bytes: XML 字节
            
        Returns:
            bool: 是否有效
        """
        try:
            parser = etree.XMLParser(recover=False)
            etree.fromstring(xml_bytes, parser=parser)
            return True
        except etree.XMLSyntaxError:
            return False

    def export_with_translations(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """基于原始文件和翻译映射导出
        
        Args:
            original_bytes: 原始文件字节
            translations: source_text -> target_text 映射
            
        Returns:
            bytes: 导出的 DITA XML 字节
        """
        parser = etree.XMLParser(remove_blank_text=False, recover=True)
        root = etree.fromstring(original_bytes, parser=parser)
        
        # 遍历所有文本节点并替换
        self._replace_text_content(root, translations)
        
        return etree.tostring(
            root,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
        )

    def _replace_text_content(
        self,
        element: etree._Element,
        translations: Dict[str, str],
    ) -> None:
        """递归替换元素中的文本内容"""
        # 跳过 conref
        if element.get("conref"):
            return
        
        # 替换元素的直接文本
        if element.text and element.text.strip():
            text = element.text.strip()
            if text in translations:
                # 保留原始空白
                original = element.text
                leading = original[:len(original) - len(original.lstrip())]
                trailing = original[len(original.rstrip()):]
                element.text = leading + translations[text] + trailing
        
        # 递归处理子元素
        for child in element:
            self._replace_text_content(child, translations)
