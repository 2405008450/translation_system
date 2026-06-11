"""统一的文档对象和解析器基类"""

from dataclasses import dataclass, field
from typing import List, Optional
from abc import ABC, abstractmethod


@dataclass
class TableCell:
    text: str
    row: int
    col: int


@dataclass
class Table:
    headers: List[str]
    rows: List[List[str]]


@dataclass
class Paragraph:
    text: str
    style: Optional[str] = None  # 段落样式名


@dataclass
class Document:
    """统一文档对象 - 所有格式解析后的统一表示"""
    paragraphs: List[Paragraph] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
    filename: str = ""
    raw_text: str = ""  # 全文纯文本拼接


class BaseParser(ABC):
    """解析器基类"""

    @abstractmethod
    def parse(self, file_path: str) -> Document:
        pass

    @staticmethod
    def supported_extensions() -> List[str]:
        return []
