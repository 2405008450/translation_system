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

# V4 适配器 - 本地化和排版文件
from app.services.adapters.html_adapter import HtmlAdapter
from app.services.adapters.properties_adapter import PropertiesAdapter
from app.services.adapters.po_adapter import PoAdapter
from app.services.adapters.strings_adapter import StringsAdapter
from app.services.adapters.markdown_adapter import MarkdownAdapter
from app.services.adapters.srt_adapter import SrtAdapter
from app.services.adapters.csv_adapter import CsvAdapter

# V5 适配器 - 双语文件和工程文件
from app.services.adapters.sdlxliff_adapter import SdlxliffAdapter
from app.services.adapters.txml_adapter import TxmlAdapter
from app.services.adapters.dxf_adapter import DxfAdapter
from app.services.adapters.zip_adapter import ZipAdapter

# V6 适配器 - 复杂格式
from app.services.adapters.idml_adapter import IdmlAdapter
from app.services.adapters.mif_adapter import MifAdapter
from app.services.adapters.rar_adapter import RarAdapter

# V2 导出器
from app.services.adapters.dita_exporter import DitaExporter
from app.services.adapters.svg_exporter import SvgExporter

# V3 导出器 - 行业标准格式
from app.services.adapters.tmx_exporter import TmxExporter
from app.services.adapters.xliff_exporter import XliffExporter, XliffImporter

# V4 导出器 - 本地化和排版文件
from app.services.adapters.html_exporter import HtmlExporter
from app.services.adapters.properties_exporter import PropertiesExporter
from app.services.adapters.po_exporter import PoExporter
from app.services.adapters.strings_exporter import StringsExporter
from app.services.adapters.markdown_exporter import MarkdownExporter
from app.services.adapters.srt_exporter import SrtExporter
from app.services.adapters.csv_exporter import CsvExporter

# V5 导出器 - 双语文件和工程文件
from app.services.adapters.sdlxliff_exporter import SdlxliffExporter
from app.services.adapters.txml_exporter import TxmlExporter
from app.services.adapters.dxf_exporter import DxfExporter
from app.services.adapters.zip_exporter import ZipExporter

# V6 导出器 - 复杂格式
from app.services.adapters.idml_exporter import IdmlExporter
from app.services.adapters.mif_exporter import MifExporter
from app.services.adapters.rar_exporter import RarExporter

# V7 多格式导出服务
from app.services.adapters.export_formats import (
    EXPORT_OPTIONS,
    FORMAT_EXPORT_SUPPORT,
    get_supported_exports,
    get_export_option,
)
from app.services.adapters.multi_format_exporter import (
    MultiFormatExporter,
    get_export_options_for_file,
    export_file,
)


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
    
    # V4 适配器 - 本地化和排版文件
    registry.register(HtmlAdapter())
    registry.register(PropertiesAdapter())
    registry.register(PoAdapter())
    registry.register(StringsAdapter())
    registry.register(MarkdownAdapter())
    registry.register(SrtAdapter())
    registry.register(CsvAdapter())
    
    # V5 适配器 - 双语文件和工程文件
    registry.register(SdlxliffAdapter())
    registry.register(TxmlAdapter())
    registry.register(DxfAdapter())
    registry.register(ZipAdapter())
    
    # V6 适配器 - 复杂格式
    registry.register(IdmlAdapter())
    registry.register(MifAdapter())
    registry.register(RarAdapter())


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
    
    # V4 适配器
    "HtmlAdapter",
    "PropertiesAdapter",
    "PoAdapter",
    "StringsAdapter",
    "MarkdownAdapter",
    "SrtAdapter",
    "CsvAdapter",
    
    # V5 适配器
    "SdlxliffAdapter",
    "TxmlAdapter",
    "DxfAdapter",
    "ZipAdapter",
    
    # V6 适配器
    "IdmlAdapter",
    "MifAdapter",
    "RarAdapter",
    
    # V2 导出器
    "DitaExporter",
    "SvgExporter",
    
    # V3 导出器 - 行业标准格式
    "TmxExporter",
    "XliffExporter",
    "XliffImporter",
    
    # V4 导出器 - 本地化和排版文件
    "HtmlExporter",
    "PropertiesExporter",
    "PoExporter",
    "StringsExporter",
    "MarkdownExporter",
    "SrtExporter",
    "CsvExporter",
    
    # V5 导出器 - 双语文件和工程文件
    "SdlxliffExporter",
    "TxmlExporter",
    "DxfExporter",
    "ZipExporter",
    
    # V6 导出器 - 复杂格式
    "IdmlExporter",
    "MifExporter",
    "RarExporter",
    
    # V7 多格式导出服务
    "EXPORT_OPTIONS",
    "FORMAT_EXPORT_SUPPORT",
    "get_supported_exports",
    "get_export_option",
    "MultiFormatExporter",
    "get_export_options_for_file",
    "export_file",
    
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
