"""
文件解析服务 - 使用适配器系统解析多种文档格式
"""
from pathlib import Path

from fastapi import UploadFile

from app.services.adapters import (
    get_registry,
    UnsupportedFormatError,
    FileTooLargeError,
    ParseError,
)
from app.services.document_workspace import parse_docx_workspace


# 支持的文件扩展名
SUPPORTED_EXTENSIONS = {
    ".txt", ".docx", ".pdf", ".pptx", ".xlsx", ".dita", ".ditamap", ".xml", ".svg",
    ".yaml", ".yml", ".json", ".php",
    # V4 新增格式
    ".html", ".htm", ".properties", ".po", ".pot", ".strings",
    ".md", ".markdown", ".srt", ".csv",
    # V5 新增格式
    ".sdlxliff", ".txml", ".dxf", ".zip",
    # V6 新增格式
    ".idml", ".mif", ".rar",
}


async def parse_uploaded_file(upload_file: UploadFile) -> str:
    """解析上传的文件，返回纯文本内容

    Args:
        upload_file: FastAPI 上传文件对象

    Returns:
        str: 提取的文本内容

    Raises:
        ValueError: 当文件格式不支持或解析失败时
    """
    filename = upload_file.filename or ""
    extension = Path(filename).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        supported_list = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"不支持的文件格式 '{extension}'。支持的格式: {supported_list}")

    raw_bytes = await upload_file.read()
    if not raw_bytes:
        return ""

    try:
        registry = get_registry()
        adapter = registry.get_adapter(filename)
        result = adapter.parse_with_validation(raw_bytes, filename)

        # 从 segments 提取文本
        texts = [seg.source_text for seg in result.segments if seg.source_text]
        return "\n".join(texts)

    except UnsupportedFormatError as e:
        raise ValueError(str(e)) from e
    except FileTooLargeError as e:
        raise ValueError(str(e)) from e
    except ParseError as e:
        raise ValueError(str(e)) from e
    except Exception as e:
        raise ValueError(f"解析文件失败: {str(e)}") from e


def _parse_txt(raw_bytes: bytes) -> str:
    """Fallback TXT 解析"""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("TXT 文件编码无法识别，请使用 UTF-8 或 GB18030。")


def _parse_docx(raw_bytes: bytes) -> str:
    """Fallback DOCX 解析"""
    workspace = parse_docx_workspace(raw_bytes)
    return "\n".join(segment["display_text"] for segment in workspace["segments"])
