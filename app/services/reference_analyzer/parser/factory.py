"""解析器工厂 - 根据文件扩展名选择解析器"""

import os
from .base import BaseParser, Document
from .txt_parser import TxtParser
from .docx_parser import DocxParser
from .xlsx_parser import XlsxParser
from .pdf_parser import PdfParser


PARSERS = [TxtParser, DocxParser, XlsxParser, PdfParser]


def get_parser(file_path: str) -> BaseParser:
    ext = os.path.splitext(file_path)[1].lower()
    for parser_cls in PARSERS:
        if ext in parser_cls.supported_extensions():
            return parser_cls()
    raise ValueError(f"不支持的文件格式: {ext}")


def parse_file(file_path: str) -> Document:
    parser = get_parser(file_path)
    return parser.parse(file_path)
