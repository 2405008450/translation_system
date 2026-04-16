"""
API 路由模块 - 文件上传、解析和导出接口

支持多种文档格式的上传、解析和导出。
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel

from app.database import get_db
from app.services.document_workspace import build_docx_workspace
from app.services.slate_parser import parse_docx_for_slate
from app.services.adapters import (
    get_registry,
    UnsupportedFormatError,
    FileTooLargeError,
    ParseError,
    ExportService,
    ExportError,
    DocumentAST,
    DitaExporter,
    SvgExporter,
    TmxExporter,
    XliffExporter,
    XliffImporter,
)


router = APIRouter()

# 支持的文件扩展名
SUPPORTED_EXTENSIONS = {".docx", ".txt", ".pdf", ".pptx", ".dita", ".ditamap", ".xml", ".svg"}


def _get_file_extension(filename: str) -> str:
    """获取文件扩展名（小写）"""
    return Path(filename or "").suffix.lower()


def _validate_file_upload(file: UploadFile, allowed_extensions: set[str] | None = None) -> str:
    """验证上传的文件
    
    Args:
        file: 上传的文件
        allowed_extensions: 允许的扩展名集合，None 表示使用默认支持的扩展名
        
    Returns:
        str: 文件扩展名
        
    Raises:
        HTTPException: 当文件格式不支持时
    """
    ext = _get_file_extension(file.filename)
    allowed = allowed_extensions or SUPPORTED_EXTENSIONS
    
    if ext not in allowed:
        supported_list = ", ".join(sorted(allowed))
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 '{ext}'。支持的格式: {supported_list}"
        )
    
    return ext


def _validate_docx_upload(file: UploadFile) -> None:
    """验证 DOCX 文件上传（向后兼容）"""
    _validate_file_upload(file, {".docx"})


@router.post("/parser/slate")
async def upload_for_slate(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    db: Session = Depends(get_db),
):
    """上传文件并解析为 Slate 编辑器格式
    
    目前仅支持 DOCX 格式。
    """
    _validate_docx_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    try:
        result = parse_docx_for_slate(db=db, raw_bytes=raw_bytes, similarity_threshold=threshold)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/parser/workspace")
async def upload_for_workspace(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    db: Session = Depends(get_db),
):
    """上传文件并构建翻译工作台
    
    目前仅支持 DOCX 格式。
    """
    _validate_docx_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    try:
        return build_docx_workspace(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=threshold,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/parser/parse")
async def parse_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """通用文档解析接口
    
    使用适配器系统解析多种格式的文档。
    
    支持的格式:
    - DOCX: Word 文档
    - TXT: 纯文本文件
    - PDF: PDF 文档
    - PPTX: PowerPoint 演示文稿
    - DITA/DITAMAP/XML: DITA 文档
    - SVG: SVG 矢量图形
    
    Returns:
        解析结果，包含 AST 和 segments
    """
    ext = _validate_file_upload(file)
    
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")
    
    try:
        registry = get_registry()
        adapter = registry.get_adapter(file.filename)
        
        # 使用带验证的解析
        result = adapter.parse_with_validation(raw_bytes, file.filename)
        
        return {
            "filename": file.filename,
            "format": ext,
            "ast": result.ast.to_dict(),
            "segments": [seg.to_dict() for seg in result.segments],
            "metadata": result.metadata,
        }
    except UnsupportedFormatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except ParseError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"解析失败: {str(exc)}") from exc


@router.get("/parser/formats")
async def get_supported_formats():
    """获取支持的文件格式列表
    
    Returns:
        支持的格式及其描述
    """
    registry = get_registry()
    
    formats = []
    for ext in sorted(registry.list_supported_extensions()):
        adapter = registry.get_adapter(f"test{ext}")
        formats.append({
            "extension": ext,
            "adapter": adapter.__class__.__name__,
            "max_size_mb": adapter.get_max_file_size() / (1024 * 1024),
        })
    
    return {
        "formats": formats,
        "total": len(formats),
    }



# ============== 导出相关模型 ==============

class ExportRequest(BaseModel):
    """导出请求模型"""
    segments: List[dict]  # 包含 segment_id, source_text, target_text 的列表
    format: str = "txt"  # 导出格式: txt, docx
    bilingual: bool = False  # 是否双语导出
    filename: Optional[str] = None  # 原始文件名


class DitaExportRequest(BaseModel):
    """DITA 导出请求模型"""
    ast: dict  # DocumentAST 的字典表示
    translations: Dict[str, str]  # segment_id -> translated_text
    original_content: Optional[str] = None  # Base64 编码的原始文件内容


class SvgExportRequest(BaseModel):
    """SVG 导出请求模型"""
    original_content: str  # Base64 编码的原始 SVG 内容
    translations: Dict[str, str]  # segment_id -> translated_text
    bilingual: bool = False  # 是否双语导出


class TmxExportRequest(BaseModel):
    """TMX 导出请求模型"""
    segments: List[dict]  # 包含 source_text, target_text 的列表
    source_lang: str = "zh-CN"
    target_lang: str = "en-US"
    filename: Optional[str] = None


class XliffExportRequest(BaseModel):
    """XLIFF 导出请求模型"""
    segments: List[dict]  # 包含 segment_id, source_text, target_text, status 的列表
    source_lang: str = "zh-CN"
    target_lang: str = "en-US"
    filename: str = "document"
    version: str = "1.2"  # 1.2 或 2.0


# ============== 导出接口 ==============

@router.post("/export/txt")
async def export_txt(request: ExportRequest):
    """导出为 TXT 格式
    
    Args:
        request: 导出请求，包含 segments 列表
        
    Returns:
        TXT 文件下载响应
    """
    try:
        service = ExportService()
        
        # 构建 AST
        from app.services.adapters.models import BlockNode, NodeType
        nodes = []
        for seg in request.segments:
            text = seg.get("target_text") or seg.get("source_text", "")
            if text:
                nodes.append(BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=text,
                ))
        
        ast = DocumentAST(nodes=nodes, source_format=".txt")
        
        # 构建翻译映射
        translations = {
            seg.get("segment_id", f"seg_{i}"): seg.get("target_text", "")
            for i, seg in enumerate(request.segments)
        }
        
        if request.bilingual:
            # 双语导出需要保留原文
            nodes_bilingual = []
            for seg in request.segments:
                source = seg.get("source_text", "")
                if source:
                    nodes_bilingual.append(BlockNode(
                        node_type=NodeType.PARAGRAPH,
                        text_content=source,
                    ))
            ast_bilingual = DocumentAST(nodes=nodes_bilingual, source_format=".txt")
            content = service.export_bilingual(ast_bilingual, translations, format="txt")
            filename = "bilingual_export.txt"
        else:
            content = service.export_txt(ast, translations)
            filename = "export.txt"
        
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}") from e


@router.post("/export/docx")
async def export_docx(request: ExportRequest):
    """导出为 DOCX 格式
    
    Args:
        request: 导出请求，包含 segments 列表
        
    Returns:
        DOCX 文件下载响应
    """
    try:
        service = ExportService()
        
        # 构建 AST
        from app.services.adapters.models import BlockNode, NodeType
        nodes = []
        for seg in request.segments:
            text = seg.get("target_text") or seg.get("source_text", "")
            if text:
                nodes.append(BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=text,
                ))
        
        ast = DocumentAST(nodes=nodes, source_format=".docx")
        
        # 构建翻译映射
        translations = {
            seg.get("segment_id", f"seg_{i}"): seg.get("target_text", "")
            for i, seg in enumerate(request.segments)
        }
        
        if request.bilingual:
            # 双语导出需要保留原文
            nodes_bilingual = []
            for seg in request.segments:
                source = seg.get("source_text", "")
                if source:
                    nodes_bilingual.append(BlockNode(
                        node_type=NodeType.PARAGRAPH,
                        text_content=source,
                    ))
            ast_bilingual = DocumentAST(nodes=nodes_bilingual, source_format=".docx")
            content = service.export_bilingual(ast_bilingual, translations, format="docx")
            filename = "bilingual_export.docx"
        else:
            content = service.export_docx(ast, translations)
            filename = "export.docx"
        
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}") from e


@router.post("/export/dita")
async def export_dita(request: DitaExportRequest):
    """导出为 DITA 格式
    
    Args:
        request: DITA 导出请求
        
    Returns:
        DITA XML 文件下载响应
    """
    try:
        import base64
        
        exporter = DitaExporter()
        ast = DocumentAST.from_dict(request.ast)
        
        original_bytes = None
        if request.original_content:
            original_bytes = base64.b64decode(request.original_content)
        
        content = exporter.export(ast, request.translations, original_bytes)
        
        return Response(
            content=content,
            media_type="application/xml",
            headers={
                "Content-Disposition": 'attachment; filename="export.dita"'
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DITA 导出失败: {str(e)}") from e


@router.post("/export/svg")
async def export_svg(request: SvgExportRequest):
    """导出为 SVG 格式
    
    Args:
        request: SVG 导出请求
        
    Returns:
        SVG 文件下载响应和警告信息
    """
    try:
        import base64
        
        exporter = SvgExporter()
        original_bytes = base64.b64decode(request.original_content)
        
        if request.bilingual:
            content, warnings = exporter.export_bilingual(original_bytes, request.translations)
            filename = "bilingual_export.svg"
        else:
            content, warnings = exporter.export(original_bytes, request.translations)
            filename = "export.svg"
        
        return Response(
            content=content,
            media_type="image/svg+xml",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Export-Warnings": str(len(warnings)),
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SVG 导出失败: {str(e)}") from e


@router.get("/export/formats")
async def get_export_formats():
    """获取支持的导出格式列表"""
    return {
        "formats": [
            {"id": "txt", "name": "纯文本 (TXT)", "extension": ".txt", "bilingual": True},
            {"id": "docx", "name": "Word 文档 (DOCX)", "extension": ".docx", "bilingual": True},
            {"id": "dita", "name": "DITA XML", "extension": ".dita", "bilingual": False},
            {"id": "svg", "name": "SVG 矢量图", "extension": ".svg", "bilingual": True},
            {"id": "tmx", "name": "翻译记忆库 (TMX)", "extension": ".tmx", "bilingual": False},
            {"id": "xliff", "name": "XLIFF 离线文件", "extension": ".xlf", "bilingual": False},
        ]
    }


@router.post("/export/tmx")
async def export_tmx(request: TmxExportRequest):
    """导出为 TMX 格式
    
    TMX (Translation Memory eXchange) 是翻译记忆库的行业标准交换格式。
    
    Args:
        request: TMX 导出请求
        
    Returns:
        TMX 文件下载响应
    """
    try:
        exporter = TmxExporter(
            source_lang=request.source_lang,
            target_lang=request.target_lang,
        )
        
        content = exporter.export(request.segments, request.filename)
        
        filename = "export.tmx"
        if request.filename:
            base_name = request.filename.rsplit(".", 1)[0]
            filename = f"{base_name}.tmx"
        
        return Response(
            content=content,
            media_type="application/x-tmx+xml",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TMX 导出失败: {str(e)}") from e


@router.post("/export/xliff")
async def export_xliff(request: XliffExportRequest):
    """导出为 XLIFF 格式
    
    XLIFF (XML Localization Interchange File Format) 是本地化行业的标准交换格式，
    支持离线翻译编辑。
    
    Args:
        request: XLIFF 导出请求
        
    Returns:
        XLIFF 文件下载响应
    """
    try:
        exporter = XliffExporter(
            source_lang=request.source_lang,
            target_lang=request.target_lang,
            version=request.version,
        )
        
        # 获取原始格式
        original_format = "plaintext"
        if request.filename:
            ext = request.filename.rsplit(".", 1)[-1].lower()
            format_map = {
                "docx": "winword",
                "pdf": "pdf",
                "pptx": "powerpoint",
                "txt": "plaintext",
                "xml": "xml",
                "dita": "xml",
            }
            original_format = format_map.get(ext, "plaintext")
        
        content = exporter.export(
            request.segments,
            request.filename or "document",
            original_format,
        )
        
        filename = "export.xlf"
        if request.filename:
            base_name = request.filename.rsplit(".", 1)[0]
            filename = f"{base_name}.xlf"
        
        return Response(
            content=content,
            media_type="application/xliff+xml",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"XLIFF 导出失败: {str(e)}") from e


@router.post("/import/xliff")
async def import_xliff(file: UploadFile = File(...)):
    """导入 XLIFF 文件
    
    从 XLIFF 文件导入翻译结果。
    
    Args:
        file: XLIFF 文件
        
    Returns:
        导入的段落列表
    """
    if not file.filename or not file.filename.lower().endswith((".xlf", ".xliff")):
        raise HTTPException(status_code=400, detail="请上传 XLIFF 文件 (.xlf 或 .xliff)")
    
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空")
    
    try:
        importer = XliffImporter()
        segments = importer.import_xliff(raw_bytes)
        
        return {
            "filename": file.filename,
            "segments": segments,
            "count": len(segments),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"XLIFF 导入失败: {str(e)}") from e
