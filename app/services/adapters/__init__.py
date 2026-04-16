# Multi-Format Document Adapters
"""
多格式文档适配器包

提供统一的接口解析和导出多种文档格式。

V1 支持的格式：
- DOCX: Word 文档
- TXT: 纯文本文件
- XLSX: Excel 文件（TM 导入专用）

V2 支持的格式：
- PDF: PDF 文档
- PPTX: PowerPoint 演示文稿
- DITA: DITA XML 文档
- SVG: SVG 矢量图形

使用示例：
    from app.services.adapters import get_registry, TxtAdapter, DocxAdapter
    
    # 获取注册表
    registry = get_registry()
    
    # 根据文件名获取适配器
    adapter = registry.get_adapter("document.docx")
    result = adapter.parse(raw_bytes)
    
    # 导出
    from app.services.adapters import ExportService
    service = ExportService()
    exported = service.export_docx(result.ast, translations)
"""

from app.services.adapters.base import FormatAdapter
from app.services.adapters.docx_adapter import DocxAdapter
from app.services.adapters.exceptions import (
    AdapterError,
    ExportError,
    FileTooLargeError,
    OCRRequiredError,
    ParseError,
    UnsupportedFormatError,
)
from app.services.adapters.export_service import ExportService
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
    Segment,
    TMEntry,
    TMImportResult,
)
from app.services.adapters.registry import (
    AdapterRegistry,
    get_registry,
    register_adapter,
)
from app.services.adapters.segment_extractor import SegmentExtractor, extract_segments
from app.services.adapters.txt_adapter import TxtAdapter
from app.services.adapters.xlsx_adapter import ColumnMapping, XlsxAdapter

# V2 适配器
from app.services.adapters.pdf_adapter import PdfAdapter
from app.services.adapters.pptx_adapter import PptxAdapter
from app.services.adapters.dita_adapter import DitaAdapter
from app.services.adapters.svg_adapter import SvgAdapter

# V3 适配器 - 配置和代码文件
from app.services.adapters.yaml_adapter import YamlAdapter
from app.services.adapters.json_adapter import JsonAdapter
from app.services.adapters.php_adapter import PhpAdapter

# V2 导出器
from app.services.adapters.dita_exporter import DitaExporter
from app.services.adapters.svg_exporter import SvgExporter

# V3 导出器 - 行业标准格式
from app.services.adapters.tmx_exporter import TmxExporter
from app.services.adapters.xliff_exporter import XliffExporter, XliffImporter


def _register_default_adapters() -> None:
    """注册默认适配器"""
    registry = get_registry()
    
    # V1 适配器
    registry.register(TxtAdapter())
    registry.register(DocxAdapter())
    registry.register(XlsxAdapter())
    
    # V2 适配器
    registry.register(PdfAdapter())
    registry.register(PptxAdapter())
    registry.register(DitaAdapter())
    registry.register(SvgAdapter())
    
    # V3 适配器 - 配置和代码文件
    registry.register(YamlAdapter())
    registry.register(JsonAdapter())
    registry.register(PhpAdapter())


# 自动注册默认适配器
_register_default_adapters()


__all__ = [
    # 基类和接口
    "FormatAdapter",
    "AdapterRegistry",
    "get_registry",
    "register_adapter",
    
    # V1 适配器
    "TxtAdapter",
    "DocxAdapter",
    "XlsxAdapter",
    "ColumnMapping",
    
    # V2 适配器
    "PdfAdapter",
    "PptxAdapter",
    "DitaAdapter",
    "SvgAdapter",
    
    # V3 适配器
    "YamlAdapter",
    "JsonAdapter",
    "PhpAdapter",
    
    # V2 导出器
    "DitaExporter",
    "SvgExporter",
    
    # V3 导出器 - 行业标准格式
    "TmxExporter",
    "XliffExporter",
    "XliffImporter",
    
    # 数据模型
    "DocumentAST",
    "BlockNode",
    "NodeType",
    "Segment",
    "ParseResult",
    "TMEntry",
    "TMImportResult",
    
    # 服务
    "ExportService",
    "SegmentExtractor",
    "extract_segments",
    
    # 异常
    "AdapterError",
    "UnsupportedFormatError",
    "ParseError",
    "FileTooLargeError",
    "OCRRequiredError",
    "ExportError",
]
