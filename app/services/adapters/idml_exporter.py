"""
IDML 导出器 - 将翻译后的内容导出为 IDML 格式

保留原始文件结构，仅替换 Story XML 中的文本内容。
"""
import zipfile
from io import BytesIO
from typing import Dict

from lxml import etree


class IdmlExporter:
    """IDML 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 IDML 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: source_text -> target_text
            
        Returns:
            bytes: 翻译后的文件字节
        """
        input_zip = zipfile.ZipFile(BytesIO(original_bytes), 'r')
        output_buffer = BytesIO()
        output_zip = zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED)
        
        for name in input_zip.namelist():
            content = input_zip.read(name)
            
            # 只处理 Stories 目录中的 XML 文件
            if name.startswith('Stories/') and name.endswith('.xml'):
                try:
                    content = self._translate_story(content, translations)
                except Exception:
                    pass
            
            output_zip.writestr(name, content)
        
        input_zip.close()
        output_zip.close()
        
        return output_buffer.getvalue()

    def _translate_story(self, content: bytes, translations: Dict[str, str]) -> bytes:
        """翻译 Story XML 内容"""
        parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
        root = etree.fromstring(content, parser=parser)
        
        # 遍历所有 Content 元素
        for content_elem in root.iter('Content'):
            if content_elem.text and content_elem.text.strip():
                text = content_elem.text.strip()
                if text in translations:
                    # 保留原始空白
                    original = content_elem.text
                    leading = original[:len(original) - len(original.lstrip())]
                    trailing = original[len(original.rstrip()):]
                    content_elem.text = leading + translations[text] + trailing
        
        return etree.tostring(root, encoding='utf-8', xml_declaration=True)
