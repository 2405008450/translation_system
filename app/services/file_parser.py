from pathlib import Path

from fastapi import UploadFile

from app.services.document_workspace import parse_docx_workspace


SUPPORTED_EXTENSIONS = {".txt", ".docx"}


async def parse_uploaded_file(upload_file: UploadFile) -> str:
    extension = Path(upload_file.filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("仅支持 TXT 或 DOCX 文件。")

    raw_bytes = await upload_file.read()
    if not raw_bytes:
        return ""

    if extension == ".txt":
        return _parse_txt(raw_bytes)
    if extension == ".docx":
        return _parse_docx(raw_bytes)

    raise ValueError("不支持的文件格式。")


def _parse_txt(raw_bytes: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("TXT 文件编码无法识别，请使用 UTF-8 或 GB18030。")


def _parse_docx(raw_bytes: bytes) -> str:
    workspace = parse_docx_workspace(raw_bytes)
    return "\n".join(segment["display_text"] for segment in workspace["segments"])
