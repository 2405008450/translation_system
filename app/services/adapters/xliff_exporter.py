"""
XLIFF 导出器模块 - 导出可离线编辑的双语文件

XLIFF (XML Localization Interchange File Format) 是本地化行业的标准交换格式。
支持 XLIFF 1.2 和 2.0 版本。
"""
from datetime import datetime
from typing import List, Optional
from xml.sax.saxutils import escape


class XliffExporter:
    """XLIFF 导出器
    
    将翻译段落导出为 XLIFF 格式，支持离线翻译。
    """

    def __init__(
        self,
        source_lang: str = "zh-CN",
        target_lang: str = "en-US",
        tool_id: str = "translation-memory-demo",
        tool_name: str = "Translation Memory Demo",
        version: str = "1.2",
    ):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.tool_id = tool_id
        self.tool_name = tool_name
        self.version = version

    def export(
        self,
        segments: List[dict],
        filename: str = "document",
        original_format: str = "plaintext",
    ) -> bytes:
        """导出为 XLIFF 格式
        
        Args:
            segments: 段落列表
            filename: 原始文件名
            original_format: 原始文件格式
            
        Returns:
            bytes: XLIFF XML 文件字节
        """
        if self.version == "2.0":
            return self._export_xliff_2(segments, filename, original_format)
        else:
            return self._export_xliff_12(segments, filename, original_format)

    def _export_xliff_12(
        self,
        segments: List[dict],
        filename: str,
        original_format: str,
    ) -> bytes:
        """导出 XLIFF 1.2 格式"""
        xliff_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">',
            f'  <file original="{escape(filename)}" source-language="{escape(self.source_lang)}" target-language="{escape(self.target_lang)}" datatype="{escape(original_format)}">',
            '    <header>',
            '      <tool tool-id="{}" tool-name="{}"/>'.format(
                escape(self.tool_id), escape(self.tool_name)
            ),
            '    </header>',
            '    <body>',
        ]
        
        for i, seg in enumerate(segments):
            source_text = seg.get("source_text", "")
            target_text = seg.get("target_text", "")
            segment_id = seg.get("segment_id", f"seg_{i}")
            status = seg.get("status", "none")
            
            if not source_text:
                continue
            
            # 确定翻译状态
            state = self._get_xliff_state(status, target_text)
            
            xliff_parts.extend([
                f'      <trans-unit id="{escape(segment_id)}">',
                f'        <source>{escape(source_text)}</source>',
                f'        <target state="{state}">{escape(target_text)}</target>',
            ])
            
            # 添加匹配信息作为注释
            if seg.get("matched_source_text"):
                xliff_parts.append(
                    f'        <note>TM Match: {escape(seg.get("matched_source_text", ""))}</note>'
                )
            
            xliff_parts.append('      </trans-unit>')
        
        xliff_parts.extend([
            '    </body>',
            '  </file>',
            '</xliff>',
        ])
        
        return '\n'.join(xliff_parts).encode('utf-8')

    def _export_xliff_2(
        self,
        segments: List[dict],
        filename: str,
        original_format: str,
    ) -> bytes:
        """导出 XLIFF 2.0 格式"""
        xliff_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.0"',
            f'       srcLang="{escape(self.source_lang)}" trgLang="{escape(self.target_lang)}">',
            f'  <file id="f1" original="{escape(filename)}">',
        ]
        
        for i, seg in enumerate(segments):
            source_text = seg.get("source_text", "")
            target_text = seg.get("target_text", "")
            segment_id = seg.get("segment_id", f"seg_{i}")
            status = seg.get("status", "none")
            
            if not source_text:
                continue
            
            # XLIFF 2.0 状态
            state = self._get_xliff2_state(status, target_text)
            
            xliff_parts.extend([
                f'    <unit id="{escape(segment_id)}">',
                f'      <segment state="{state}">',
                f'        <source>{escape(source_text)}</source>',
                f'        <target>{escape(target_text)}</target>',
                '      </segment>',
            ])
            
            # 添加注释
            if seg.get("matched_source_text"):
                xliff_parts.extend([
                    '      <notes>',
                    f'        <note category="tm-match">{escape(seg.get("matched_source_text", ""))}</note>',
                    '      </notes>',
                ])
            
            xliff_parts.append('    </unit>')
        
        xliff_parts.extend([
            '  </file>',
            '</xliff>',
        ])
        
        return '\n'.join(xliff_parts).encode('utf-8')

    def _get_xliff_state(self, status: str, target_text: str) -> str:
        """获取 XLIFF 1.2 翻译状态"""
        if not target_text:
            return "new"
        if status == "exact":
            return "translated"
        if status == "fuzzy":
            return "needs-review-translation"
        return "translated" if target_text else "new"

    def _get_xliff2_state(self, status: str, target_text: str) -> str:
        """获取 XLIFF 2.0 翻译状态"""
        if not target_text:
            return "initial"
        if status == "exact":
            return "translated"
        if status == "fuzzy":
            return "reviewed"
        return "translated" if target_text else "initial"


class XliffImporter:
    """XLIFF 导入器
    
    从 XLIFF 文件导入翻译结果。
    """

    def import_xliff(self, xliff_bytes: bytes) -> List[dict]:
        """从 XLIFF 导入翻译
        
        Args:
            xliff_bytes: XLIFF 文件字节
            
        Returns:
            List[dict]: 段落列表，包含 segment_id, source_text, target_text
        """
        from lxml import etree
        
        root = etree.fromstring(xliff_bytes)
        segments = []
        
        # 检测 XLIFF 版本
        version = root.get("version", "1.2")
        
        if version.startswith("2"):
            segments = self._import_xliff_2(root)
        else:
            segments = self._import_xliff_12(root)
        
        return segments

    def _import_xliff_12(self, root) -> List[dict]:
        """导入 XLIFF 1.2"""
        ns = {"xliff": "urn:oasis:names:tc:xliff:document:1.2"}
        segments = []
        
        for tu in root.xpath("//xliff:trans-unit", namespaces=ns):
            segment_id = tu.get("id", "")
            source = tu.find("xliff:source", namespaces=ns)
            target = tu.find("xliff:target", namespaces=ns)
            
            segments.append({
                "segment_id": segment_id,
                "source_text": source.text if source is not None else "",
                "target_text": target.text if target is not None else "",
            })
        
        return segments

    def _import_xliff_2(self, root) -> List[dict]:
        """导入 XLIFF 2.0"""
        ns = {"xliff": "urn:oasis:names:tc:xliff:document:2.0"}
        segments = []
        
        for unit in root.xpath("//xliff:unit", namespaces=ns):
            segment_id = unit.get("id", "")
            segment = unit.find("xliff:segment", namespaces=ns)
            
            if segment is not None:
                source = segment.find("xliff:source", namespaces=ns)
                target = segment.find("xliff:target", namespaces=ns)
                
                segments.append({
                    "segment_id": segment_id,
                    "source_text": source.text if source is not None else "",
                    "target_text": target.text if target is not None else "",
                })
        
        return segments
