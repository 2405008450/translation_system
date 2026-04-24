"""
SRT 适配器模块 - 解析 SRT 字幕文件

支持标准 SRT 格式：
- 序号
- 时间轴 (00:00:00,000 --> 00:00:00,000)
- 字幕文本（可多行）
- 空行分隔
"""
import re
from typing import List

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments


# 时间轴正则
TIMECODE_PATTERN = re.compile(
    r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})'
)


class SrtAdapter(FormatAdapter):
    """SRT 字幕文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".srt"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".srt"),
                segments=[],
                metadata={},
            )
        
        content = self._decode_content(raw_bytes)
        entries = self._parse_srt(content)
        
        nodes = []
        for entry in entries:
            if entry["text"].strip():
                nodes.append(BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=entry["text"],
                    metadata={
                        "index": entry["index"],
                        "start": entry["start"],
                        "end": entry["end"],
                    },
                ))
        
        ast = DocumentAST(nodes=nodes, source_format=".srt")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"subtitle_count": len(nodes)},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        # SRT 常见编码
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "iso-8859-1", "cp1252"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ParseError(filename="<unknown>", reason="无法识别文件编码")

    def _parse_srt(self, content: str) -> List[dict]:
        """解析 SRT 内容"""
        entries = []
        
        # 规范化换行
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        
        # 按空行分割字幕块
        blocks = re.split(r'\n\n+', content.strip())
        
        for block in blocks:
            if not block.strip():
                continue
            
            lines = block.strip().split('\n')
            if len(lines) < 2:
                continue
            
            # 解析序号
            try:
                index = int(lines[0].strip())
            except ValueError:
                continue
            
            # 解析时间轴
            timecode_match = TIMECODE_PATTERN.match(lines[1])
            if not timecode_match:
                continue
            
            start = timecode_match.group(1)
            end = timecode_match.group(2)
            
            # 提取字幕文本（可能多行）
            text_lines = lines[2:]
            text = '\n'.join(text_lines)
            
            # 移除 HTML 标签（如 <i>, <b>）但保留文本
            text = re.sub(r'<[^>]+>', '', text)
            
            entries.append({
                "index": index,
                "start": start,
                "end": end,
                "text": text.strip(),
            })
        
        return entries
