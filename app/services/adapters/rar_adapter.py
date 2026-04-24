"""
RAR 适配器模块 - 解析 RAR 压缩包中的文件

递归解压并处理支持的文件格式。
需要安装 rarfile 库和 unrar 工具。
"""
from io import BytesIO
from pathlib import Path
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


class RarAdapter(FormatAdapter):
    """RAR 压缩包适配器"""

    def supported_extensions(self) -> List[str]:
        return [".rar"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".rar"),
                segments=[],
                metadata={},
            )
        
        try:
            import rarfile
        except ImportError:
            raise ParseError(
                filename="<unknown>",
                reason="需要安装 rarfile 库: pip install rarfile"
            )
        
        try:
            # rarfile 需要文件路径，创建临时文件
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.rar') as tmp:
                tmp.write(raw_bytes)
                tmp_path = tmp.name
            
            try:
                rf = rarfile.RarFile(tmp_path)
            finally:
                os.unlink(tmp_path)
                
        except rarfile.BadRarFile as e:
            raise ParseError(filename="<unknown>", reason=f"无效的 RAR 文件: {str(e)}")
        except rarfile.NeedFirstVolume:
            raise ParseError(filename="<unknown>", reason="这是分卷 RAR 的一部分，需要第一卷")
        
        nodes = []
        file_list = []
        
        # 延迟导入
        from app.services.adapters import get_registry
        registry = get_registry()
        
        for info in rf.infolist():
            name = info.filename
            
            # 跳过目录
            if info.is_dir():
                continue
            
            # 跳过隐藏文件
            if name.startswith('__') or name.startswith('.'):
                continue
            
            ext = Path(name).suffix.lower()
            
            # 检查是否支持
            if not registry.is_supported(name):
                continue
            
            try:
                file_bytes = rf.read(name)
                adapter = registry.get_adapter(name)
                result = adapter.parse(file_bytes)
                
                file_node = BlockNode(
                    node_type=NodeType.SECTION,
                    text_content=None,
                    children=[],
                    metadata={"rar_path": name, "file_type": ext},
                )
                
                for node in result.ast.nodes:
                    node.metadata["rar_path"] = name
                    file_node.children.append(node)
                
                if file_node.children:
                    nodes.append(file_node)
                    file_list.append(name)
                    
            except Exception:
                continue
        
        rf.close()
        
        ast = DocumentAST(nodes=nodes, source_format=".rar")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={"file_count": len(file_list), "files": file_list},
        )
