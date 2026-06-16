"""
TMX 导出器模块 - 导出翻译记忆库交换格式

TMX (Translation Memory eXchange) 是翻译记忆库的行业标准交换格式。
"""
from datetime import UTC, datetime
from typing import List, Optional
from xml.sax.saxutils import escape

from app.services.adapters.models import Segment


class TmxExporter:
    """TMX 导出器
    
    将翻译段落导出为 TMX 1.4b 格式。
    """

    def __init__(
        self,
        source_lang: str = "zh-CN",
        target_lang: str = "en-US",
        creation_tool: str = "Translation Memory Demo",
        creation_tool_version: str = "1.0",
    ):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.creation_tool = creation_tool
        self.creation_tool_version = creation_tool_version

    def export(
        self,
        segments: List[dict],
        filename: Optional[str] = None,
    ) -> bytes:
        """导出为 TMX 格式
        
        Args:
            segments: 段落列表，每个包含 source_text 和 target_text
            filename: 原始文件名（可选，用于元数据）
            
        Returns:
            bytes: TMX XML 文件字节
        """
        creation_date = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        
        # TMX 头部
        tmx_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE tmx SYSTEM "tmx14.dtd">',
            '<tmx version="1.4">',
            '  <header',
            f'    creationtool="{escape(self.creation_tool)}"',
            f'    creationtoolversion="{escape(self.creation_tool_version)}"',
            '    datatype="plaintext"',
            '    segtype="sentence"',
            '    adminlang="en-US"',
            f'    srclang="{escape(self.source_lang)}"',
            f'    creationdate="{creation_date}"',
        ]
        
        if filename:
            tmx_parts.append(f'    o-tmf="{escape(filename)}"')
        
        tmx_parts.extend([
            '  />',
            '  <body>',
        ])
        
        # 翻译单元
        for i, seg in enumerate(segments):
            source_text = seg.get("source_text", "")
            target_text = seg.get("target_text", "")
            
            if not source_text:
                continue
            
            segment_id = seg.get("segment_id", f"seg_{i}")
            
            tmx_parts.extend([
                f'    <tu tuid="{escape(segment_id)}">',
                f'      <tuv xml:lang="{escape(self.source_lang)}">',
                f'        <seg>{escape(source_text)}</seg>',
                '      </tuv>',
                f'      <tuv xml:lang="{escape(self.target_lang)}">',
                f'        <seg>{escape(target_text)}</seg>',
                '      </tuv>',
                '    </tu>',
            ])
        
        # TMX 尾部
        tmx_parts.extend([
            '  </body>',
            '</tmx>',
        ])
        
        return '\n'.join(tmx_parts).encode('utf-8')

    def export_from_tm(
        self,
        tm_entries: List[dict],
    ) -> bytes:
        """从翻译记忆库条目导出 TMX
        
        Args:
            tm_entries: TM 条目列表，每个包含 source_text 和 target_text
            
        Returns:
            bytes: TMX XML 文件字节
        """
        segments = [
            {
                "segment_id": f"tm_{i}",
                "source_text": entry.get("source_text", ""),
                "target_text": entry.get("target_text", ""),
            }
            for i, entry in enumerate(tm_entries)
        ]
        return self.export(segments)
