"""
PO 适配器模块 - 解析 gettext .po 文件

支持标准 PO 格式，包括：
- msgid/msgstr 对
- 多行字符串
- 复数形式 (msgid_plural/msgstr[n])
- 上下文 (msgctxt)
- 注释 (#, #., #:, #,)
"""
import re
from typing import List, Optional

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments


class PoAdapter(FormatAdapter):
    """PO (gettext) 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".po", ".pot"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".po"),
                segments=[],
                metadata={},
            )
        
        content = self._decode_content(raw_bytes)
        entries = self._parse_po(content)
        
        nodes = []
        for entry in entries:
            # 跳过空的 msgid（头部信息）
            if not entry["msgid"]:
                continue
            
            nodes.append(BlockNode(
                node_type=NodeType.PARAGRAPH,
                text_content=entry["msgid"],
                metadata={
                    "msgstr": entry.get("msgstr", ""),
                    "msgctxt": entry.get("msgctxt"),
                    "msgid_plural": entry.get("msgid_plural"),
                    "comments": entry.get("comments", []),
                    "references": entry.get("references", []),
                    "flags": entry.get("flags", []),
                },
            ))
        
        ast = DocumentAST(nodes=nodes, source_format=".po")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"entry_count": len(nodes)},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "iso-8859-1", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ParseError(filename="<unknown>", reason="无法识别文件编码")

    def _parse_po(self, content: str) -> List[dict]:
        """解析 PO 文件内容"""
        entries = []
        lines = content.replace('\r\n', '\n').split('\n')
        
        current_entry = self._new_entry()
        current_field = None
        
        for line in lines:
            line = line.rstrip()
            
            # 空行表示条目结束
            if not line:
                if current_entry["msgid"] is not None:
                    entries.append(current_entry)
                    current_entry = self._new_entry()
                    current_field = None
                continue
            
            # 注释行
            if line.startswith('#'):
                self._parse_comment(line, current_entry)
                continue
            
            # 字段行
            if line.startswith('msgctxt '):
                current_field = "msgctxt"
                current_entry["msgctxt"] = self._extract_string(line[8:])
            elif line.startswith('msgid '):
                current_field = "msgid"
                current_entry["msgid"] = self._extract_string(line[6:])
            elif line.startswith('msgid_plural '):
                current_field = "msgid_plural"
                current_entry["msgid_plural"] = self._extract_string(line[13:])
            elif line.startswith('msgstr '):
                current_field = "msgstr"
                current_entry["msgstr"] = self._extract_string(line[7:])
            elif line.startswith('msgstr['):
                # 复数形式
                match = re.match(r'msgstr\[(\d+)\]\s*', line)
                if match:
                    idx = match.group(1)
                    current_field = f"msgstr_{idx}"
                    current_entry[current_field] = self._extract_string(line[match.end():])
            elif line.startswith('"') and current_field:
                # 续行
                current_entry[current_field] += self._extract_string(line)
        
        # 添加最后一个条目
        if current_entry["msgid"] is not None:
            entries.append(current_entry)
        
        return entries

    def _new_entry(self) -> dict:
        return {
            "msgid": None,
            "msgstr": "",
            "msgctxt": None,
            "msgid_plural": None,
            "comments": [],
            "references": [],
            "flags": [],
        }

    def _parse_comment(self, line: str, entry: dict) -> None:
        """解析注释行"""
        if line.startswith('#.'):
            # 提取的注释
            entry["comments"].append(line[2:].strip())
        elif line.startswith('#:'):
            # 引用位置
            entry["references"].append(line[2:].strip())
        elif line.startswith('#,'):
            # 标志
            flags = [f.strip() for f in line[2:].split(',')]
            entry["flags"].extend(flags)
        elif line.startswith('# ') or line == '#':
            # 译者注释
            entry["comments"].append(line[1:].strip() if len(line) > 1 else "")

    def _extract_string(self, text: str) -> str:
        """从引号中提取字符串"""
        text = text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        # 处理转义
        text = text.replace('\\n', '\n')
        text = text.replace('\\t', '\t')
        text = text.replace('\\"', '"')
        text = text.replace('\\\\', '\\')
        return text
