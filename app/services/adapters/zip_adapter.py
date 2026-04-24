"""
ZIP 适配器模块 - 解析 ZIP 压缩包中的文件

递归解压并处理支持的文件格式。
"""
import zipfile
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


class ZipAdapter(FormatAdapter):
    """ZIP 压缩包适配器"""

    def supported_extensions(self) -> List[str]:
        return [".zip"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".zip"),
                segments=[],
                metadata={},
            )
        
        try:
            zf = zipfile.ZipFile(BytesIO(raw_bytes), 'r')
        except zipfile.BadZipFile as e:
            raise ParseError(filename="<unknown>", reason=f"无效的 ZIP 文件: {str(e)}")
        
        nodes = []
        file_list = []
        
        # 延迟导入避免循环依赖
        from app.services.adapters import get_registry
        registry = get_registry()
        
        for name in zf.namelist():
            # 跳过目录
            if name.endswith('/'):
                continue
            
            # 跳过隐藏文件和系统文件
            if name.startswith('__') or name.startswith('.'):
                continue
            
            ext = Path(name).suffix.lower()
            
            # 检查是否支持该格式
            if not registry.is_supported(name):
                continue
            
            try:
                file_bytes = zf.read(name)
                adapter = registry.get_adapter(name)
                result = adapter.parse(file_bytes)
                
                # 为每个文件创建一个容器节点
                file_node = BlockNode(
                    node_type=NodeType.SECTION,
                    text_content=None,
                    children=[],
                    metadata={
                        "zip_path": name,
                        "file_type": ext,
                    },
                )
                
                # 添加文件中的所有节点作为子节点
                for node in result.ast.nodes:
                    # 更新节点元数据，添加文件路径
                    node.metadata["zip_path"] = name
                    file_node.children.append(node)
                
                if file_node.children:
                    nodes.append(file_node)
                    file_list.append(name)
                    
            except Exception:
                # 跳过无法解析的文件
                continue
        
        zf.close()
        
        ast = DocumentAST(nodes=nodes, source_format=".zip")
        segments = extract_segments(ast)
        
        return ParseResult(
            ast=ast,
            segments=segments,
            metadata={
                "file_count": len(file_list),
                "files": file_list,
            },
        )
