"""
DXF 适配器模块 - 解析 AutoCAD DXF 文件中的文本

提取 DXF 文件中的 TEXT、MTEXT 和 DIMENSION 实体的文本内容。
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


class DxfAdapter(FormatAdapter):
    """DXF 文件适配器"""

    def supported_extensions(self) -> List[str]:
        return [".dxf"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        return self._parse_with_options(raw_bytes, skip_non_translatable=False)

    def parse_with_options(self, raw_bytes: bytes, filename: str = "<unknown>", options: dict | None = None) -> ParseResult:
        self.validate_file_size(raw_bytes, filename)
        return self._parse_with_options(
            raw_bytes,
            skip_non_translatable=bool((options or {}).get("skip_non_translatable", True)),
        )

    def _parse_with_options(self, raw_bytes: bytes, skip_non_translatable: bool) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".dxf"),
                segments=[],
                metadata={},
            )
        
        content = self._decode_content(raw_bytes)
        nodes = self._parse_dxf(content, skip_non_translatable=skip_non_translatable)
        
        ast = DocumentAST(nodes=nodes, source_format=".dxf")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"text_count": len(nodes), "skip_non_translatable": skip_non_translatable},
        )

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "iso-8859-1", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ParseError(filename="<unknown>", reason="无法识别文件编码")

    def _parse_dxf(self, content: str, skip_non_translatable: bool = False) -> List[BlockNode]:
        """解析 DXF 文件提取文本"""
        nodes = []
        lines = content.replace('\r\n', '\n').split('\n')
        
        i = 0
        current_entity = None
        entity_data = {}
        
        while i < len(lines):
            line = lines[i].strip()
            
            # DXF 格式: 组码在奇数行，值在偶数行
            if i + 1 < len(lines):
                try:
                    group_code = int(line)
                    value = lines[i + 1].strip()
                    
                    # 实体开始
                    if group_code == 0:
                        # 保存前一个实体
                        if current_entity in ('TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'):
                            node = self._create_text_node(current_entity, entity_data)
                            if node and (
                                not skip_non_translatable
                                or self._is_translatable_text(node.text_content or "")
                            ):
                                nodes.append(node)
                        
                        current_entity = value
                        entity_data = {}
                    else:
                        entity_data[group_code] = value
                    
                    i += 2
                    continue
                except ValueError:
                    pass
            
            i += 1
        
        # 处理最后一个实体
        if current_entity in ('TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'):
            node = self._create_text_node(current_entity, entity_data)
            if node and (
                not skip_non_translatable
                or self._is_translatable_text(node.text_content or "")
            ):
                nodes.append(node)
        
        return nodes

    def _create_text_node(self, entity_type: str, data: dict) -> Optional[BlockNode]:
        """从实体数据创建文本节点"""
        text = None
        
        # 组码 1 是主要文本内容
        if 1 in data:
            text = data[1]
        
        # MTEXT 可能有多行文本（组码 3）
        if entity_type == 'MTEXT':
            additional = []
            for code in sorted(data.keys()):
                if code == 3:
                    additional.append(data[code])
            if additional:
                text = (text or '') + ''.join(additional)
            
            # 清理 MTEXT 格式代码
            if text:
                text = self._clean_mtext(text)
        
        if not text or not text.strip():
            return None
        
        # 获取位置信息
        x = data.get(10, '0')
        y = data.get(20, '0')
        
        return BlockNode(
            node_type=NodeType.PARAGRAPH,
            text_content=text.strip(),
            metadata={
                "entity_type": entity_type,
                "x": x,
                "y": y,
                "layer": data.get(8, '0'),
                "height": data.get(40, ''),
                "rotation": data.get(50, '0'),
            },
        )

    def _clean_mtext(self, text: str) -> str:
        """清理 MTEXT 格式代码"""

        # 移除格式代码
        # \\P = 换行
        text = text.replace('\\P', '\n')
        
        # \\L, \\l = 下划线开关
        text = re.sub(r'\\[Ll]', '', text)
        
        # \\O, \\o = 上划线开关
        text = re.sub(r'\\[Oo]', '', text)
        
        # \\K, \\k = 删除线开关
        text = re.sub(r'\\[Kk]', '', text)
        
        # \\Ffontname; = 字体
        text = re.sub(r'\\F[^;]*;', '', text)
        
        # \\Hheight; = 高度
        text = re.sub(r'\\H[^;]*;', '', text)
        
        # \\Wwidth; = 宽度
        text = re.sub(r'\\W[^;]*;', '', text)
        
        # \\Qangle; = 倾斜角度
        text = re.sub(r'\\Q[^;]*;', '', text)
        
        # \\Tspacing; = 字符间距
        text = re.sub(r'\\T[^;]*;', '', text)
        
        # \\Ccolor; = 颜色
        text = re.sub(r'\\C\d+;', '', text)
        
        # \\S...^...; = 堆叠文本
        text = re.sub(r'\\S[^;]*;', '', text)
        
        # \\A alignment = 对齐
        text = re.sub(r'\\A\d;', '', text)
        
        # {} = 分组
        text = text.replace('{', '').replace('}', '')
        
        # \\ = 反斜杠
        text = text.replace('\\\\', '\\')
        
        return text

    def _is_translatable_text(self, text: str) -> bool:
        """判断 DXF 文本是否包含可翻译字符，纯数字和符号默认跳过。"""
        return bool(re.search(r"[A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text or ""))
