from __future__ import annotations

from io import BytesIO
import re
from zipfile import ZipFile

from lxml.etree import XMLSyntaxError
from openpyxl import load_workbook


_GUID_NAMESPACE = re.compile(
    rb'''(\sxmlns(?::[A-Za-z_][\w.-]*)?\s*=\s*)(["'])'''
    rb'''\{([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\}\2'''
)


def normalize_xlsx_xml(content: bytes) -> bytes:
    """只转换 GUID 命名空间声明，不触碰单元格文本或图片数据。"""
    return _GUID_NAMESPACE.sub(rb"\1\2urn:uuid:\3\2", content)


def load_xlsx_workbook(raw_bytes: bytes, **options):
    """兼容部分办公软件写入的 GUID 命名空间，保留原始文件。"""
    try:
        return load_workbook(BytesIO(raw_bytes), **options)
    except XMLSyntaxError as exc:
        if "xmlns" not in str(exc) or "is not a valid URI" not in str(exc):
            raise
        # 只修复已知的 GUID 命名空间，不忽略其他 XML 语法错误。
        repaired = BytesIO()
        changed = False
        with ZipFile(BytesIO(raw_bytes)) as source, ZipFile(repaired, "w") as target:
            target.comment = source.comment
            for info in source.infolist():
                content = source.read(info)
                if info.filename.endswith(".xml"):
                    normalized = normalize_xlsx_xml(content)
                    changed = changed or normalized != content
                    content = normalized
                target.writestr(info, content)
        if not changed:
            raise
        repaired.seek(0)
        return load_workbook(repaired, **options)
