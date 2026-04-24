"""
SDLXLIFF 导出器 - 将翻译后的内容导出为 SDLXLIFF 格式

保留原始文件结构，仅更新 target 元素。
"""
from typing import Dict

from lxml import etree


# SDL XLIFF 命名空间
NAMESPACES = {
    'xliff': 'urn:oasis:names:tc:xliff:document:1.2',
}


class SdlxliffExporter:
    """SDLXLIFF 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 SDLXLIFF 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: source_text -> target_text 或 tu_id -> target_text
            
        Returns:
            bytes: 翻译后的文件字节
        """
        parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
        root = etree.fromstring(original_bytes, parser=parser)
        
        # 检测命名空间
        nsmap = self._detect_namespaces(root)
        xliff_ns = nsmap.get('xliff', NAMESPACES['xliff'])
        
        # 更新翻译
        for tu in root.iter(f'{{{xliff_ns}}}trans-unit'):
            source = tu.find(f'{{{xliff_ns}}}source')
            target = tu.find(f'{{{xliff_ns}}}target')
            
            if source is None:
                continue
            
            source_text = ''.join(source.itertext())
            tu_id = tu.get('id', '')
            
            # 查找翻译
            translation = None
            if source_text in translations:
                translation = translations[source_text]
            elif tu_id in translations:
                translation = translations[tu_id]
            
            if translation:
                if target is None:
                    # 创建 target 元素
                    target = etree.SubElement(tu, f'{{{xliff_ns}}}target')
                    # 插入到 source 后面
                    source_idx = list(tu).index(source)
                    tu.remove(target)
                    tu.insert(source_idx + 1, target)
                
                # 清空并设置新文本
                target.text = translation
                for child in list(target):
                    target.remove(child)
        
        return etree.tostring(root, encoding='utf-8', xml_declaration=True)

    def _detect_namespaces(self, root: etree._Element) -> dict:
        """检测命名空间"""
        nsmap = {}
        for prefix, uri in root.nsmap.items():
            if prefix:
                nsmap[prefix] = uri
            elif 'xliff' in uri.lower():
                nsmap['xliff'] = uri
        if 'xliff' not in nsmap:
            nsmap['xliff'] = NAMESPACES['xliff']
        return nsmap
