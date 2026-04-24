"""
TXML 导出器 - 将翻译后的内容导出为 TXML 格式

保留原始文件结构，仅更新 target 元素。
"""
from typing import Dict

from lxml import etree


class TxmlExporter:
    """TXML 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 TXML 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: source_text -> target_text 或 segment_id -> target_text
            
        Returns:
            bytes: 翻译后的文件字节
        """
        parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
        root = etree.fromstring(original_bytes, parser=parser)
        
        # 处理 segment 格式
        for segment in root.iter('segment'):
            source_elem = segment.find('source')
            target_elem = segment.find('target')
            
            if source_elem is None:
                continue
            
            source_text = ''.join(source_elem.itertext())
            seg_id = segment.get('segmentId', segment.get('id', ''))
            
            # 查找翻译
            translation = None
            if source_text in translations:
                translation = translations[source_text]
            elif seg_id in translations:
                translation = translations[seg_id]
            
            if translation:
                if target_elem is None:
                    target_elem = etree.SubElement(segment, 'target')
                target_elem.text = translation
                for child in list(target_elem):
                    target_elem.remove(child)
        
        # 处理 tu 格式
        for tu in root.iter('tu'):
            tuv_list = list(tu.iter('tuv'))
            if len(tuv_list) < 1:
                continue
            
            source_tuv = tuv_list[0]
            source_seg = source_tuv.find('seg')
            
            if source_seg is None:
                continue
            
            source_text = ''.join(source_seg.itertext())
            tu_id = tu.get('tuid', '')
            
            # 查找翻译
            translation = None
            if source_text in translations:
                translation = translations[source_text]
            elif tu_id in translations:
                translation = translations[tu_id]
            
            if translation:
                if len(tuv_list) < 2:
                    # 创建目标 tuv
                    target_tuv = etree.SubElement(tu, 'tuv')
                    target_seg = etree.SubElement(target_tuv, 'seg')
                else:
                    target_tuv = tuv_list[1]
                    target_seg = target_tuv.find('seg')
                    if target_seg is None:
                        target_seg = etree.SubElement(target_tuv, 'seg')
                
                target_seg.text = translation
                for child in list(target_seg):
                    target_seg.remove(child)
        
        return etree.tostring(root, encoding='utf-8', xml_declaration=True)
