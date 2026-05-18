"""
HTML 适配器模块 - 解析 HTML/HTM 文件

提取 HTML 中的可翻译文本内容，保留结构信息用于导出还原。
"""
import re
from typing import List, Optional
from html.parser import HTMLParser

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments


# ---------------------------------------------------------------------------
# HTML 编码检测工具
# ---------------------------------------------------------------------------

# 用于从 HTML 原始字节中提取 charset 声明的正则（仅扫描前 4KB）
_CHARSET_META_RE = re.compile(
    rb'<meta[^>]+charset\s*=\s*["\']?\s*([a-zA-Z0-9_\-]+)',
    re.IGNORECASE,
)
_HTTP_EQUIV_RE = re.compile(
    rb'<meta[^>]+content\s*=\s*["\'][^"\']*charset=([a-zA-Z0-9_\-]+)',
    re.IGNORECASE,
)

# 常见 charset 名称到 Python codec 名称的映射
_CHARSET_ALIASES = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "big5": "big5hkscs",
    "shift-jis": "shift_jis",
    "shift_jis": "shift_jis",
    "euc-jp": "euc_jp",
    "euc-kr": "euc_kr",
    "iso-8859-1": "cp1252",  # 实际网页中 iso-8859-1 几乎总是 windows-1252
    "latin-1": "cp1252",
    "ascii": "utf-8",
}


def _detect_charset_from_meta(raw_bytes: bytes) -> Optional[str]:
    """从 HTML 前 4KB 中提取 meta charset 声明。"""
    head = raw_bytes[:4096]
    match = _CHARSET_META_RE.search(head) or _HTTP_EQUIV_RE.search(head)
    if match:
        declared = match.group(1).decode("ascii", errors="ignore").strip().lower()
        return _CHARSET_ALIASES.get(declared, declared)
    return None


def _decode_html_bytes(raw_bytes: bytes) -> str:
    """智能解码 HTML 字节内容。

    优先级：
    1. HTML meta charset 声明的编码
    2. UTF-8 BOM
    3. UTF-8 严格解码
    4. charset_normalizer 自动检测
    5. 最终 fallback: utf-8 replace
    """
    if not raw_bytes:
        return ""

    # 1. 检查 BOM
    if raw_bytes.startswith(b'\xef\xbb\xbf'):
        return raw_bytes.decode("utf-8-sig")

    # 2. 尝试 HTML 中声明的编码
    declared_charset = _detect_charset_from_meta(raw_bytes)
    if declared_charset:
        try:
            return raw_bytes.decode(declared_charset)
        except (UnicodeDecodeError, LookupError):
            pass

    # 3. 尝试 UTF-8 严格模式
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # 4. 使用 charset_normalizer 自动检测
    try:
        from charset_normalizer import from_bytes
        detection = from_bytes(raw_bytes)
        best = detection.best()
        if best is not None:
            return str(best)
    except Exception:
        pass

    # 5. 常见中文编码 fallback
    try:
        return raw_bytes.decode("gb18030")
    except UnicodeDecodeError:
        pass

    # 6. 最终 fallback
    return raw_bytes.decode("utf-8", errors="replace")


# 块级元素
BLOCK_ELEMENTS = {
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'li', 'td', 'th', 'dt', 'dd', 'blockquote', 'figcaption',
    'article', 'section', 'header', 'footer', 'main', 'aside',
    'address', 'pre', 'caption', 'summary',
}

# 需要跳过的元素（不提取文本）
SKIP_ELEMENTS = {
    'script', 'style', 'code', 'pre', 'textarea',
    'noscript', 'template', 'svg', 'math',
}

# 内联元素（保留标签信息）
INLINE_ELEMENTS = {
    'a', 'span', 'strong', 'b', 'em', 'i', 'u', 's',
    'mark', 'small', 'sub', 'sup', 'abbr', 'cite', 'q',
}


class HtmlContentParser(HTMLParser):
    """HTML 内容解析器"""
    
    def __init__(self):
        super().__init__()
        self.nodes: List[BlockNode] = []
        self.current_text = ""
        self.current_metadata = {}
        self.skip_depth = 0
        self.tag_stack = []
        self.inline_tags = []
    
    def handle_starttag(self, tag: str, attrs: list):
        tag_lower = tag.lower()
        self.tag_stack.append(tag_lower)
        
        if tag_lower in SKIP_ELEMENTS:
            self.skip_depth += 1
            return
        
        if self.skip_depth > 0:
            return
        
        # 块级元素开始前，保存当前文本
        if tag_lower in BLOCK_ELEMENTS:
            self._flush_text()
            self.current_metadata = {
                "tag": tag_lower,
                "attrs": dict(attrs),
            }
        elif tag_lower in INLINE_ELEMENTS:
            # 记录内联标签位置
            self.inline_tags.append({
                "tag": tag_lower,
                "start": len(self.current_text),
                "attrs": dict(attrs),
            })
    
    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        
        if self.tag_stack and self.tag_stack[-1] == tag_lower:
            self.tag_stack.pop()
        
        if tag_lower in SKIP_ELEMENTS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        
        if self.skip_depth > 0:
            return
        
        if tag_lower in INLINE_ELEMENTS:
            # 更新内联标签结束位置
            for inline in reversed(self.inline_tags):
                if inline["tag"] == tag_lower and "end" not in inline:
                    inline["end"] = len(self.current_text)
                    break
        elif tag_lower in BLOCK_ELEMENTS:
            self._flush_text()
    
    def handle_data(self, data: str):
        if self.skip_depth > 0:
            return
        
        # 规范化空白
        text = ' '.join(data.split())
        if text:
            if self.current_text and not self.current_text.endswith(' '):
                self.current_text += ' '
            self.current_text += text
    
    def _flush_text(self):
        """保存当前累积的文本"""
        text = self.current_text.strip()
        if text:
            node_type = NodeType.PARAGRAPH
            tag = self.current_metadata.get("tag", "")
            
            if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                node_type = NodeType.HEADING
                level = int(tag[1])
                self.current_metadata["level"] = level
            elif tag in ('li',):
                node_type = NodeType.LIST_ITEM
            elif tag in ('td', 'th'):
                node_type = NodeType.TABLE_CELL
            
            metadata = self.current_metadata.copy()
            if self.inline_tags:
                metadata["inline_tags"] = self.inline_tags.copy()
            
            self.nodes.append(BlockNode(
                node_type=node_type,
                text_content=text,
                metadata=metadata,
            ))
        
        self.current_text = ""
        self.current_metadata = {}
        self.inline_tags = []


class HtmlAdapter(FormatAdapter):
    """HTML 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".html", ".htm"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".html"),
                segments=[],
                metadata={},
            )
        
        content = self._decode_content(raw_bytes)
        
        try:
            parser = HtmlContentParser()
            parser.feed(content)
            parser._flush_text()  # 确保最后的文本被保存
            nodes = parser.nodes
        except Exception as e:
            raise ParseError(filename="<unknown>", reason=f"HTML 解析错误: {str(e)}")
        
        ast = DocumentAST(nodes=nodes, source_format=".html")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"node_count": len(nodes)},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        return _decode_html_bytes(raw_bytes)
