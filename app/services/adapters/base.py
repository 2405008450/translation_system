"""
适配器基类模块 - 定义 Format_Adapter 抽象基类

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 16.2, 16.3
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from app.services.adapters.exceptions import FileTooLargeError
from app.services.adapters.models import ParseResult


# 默认文件大小限制（字节）
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# 按格式的文件大小限制
FORMAT_SIZE_LIMITS: Dict[str, int] = {
    ".txt": 10 * 1024 * 1024,    # 10 MB
    ".dat": 10 * 1024 * 1024,    # 10 MB
    ".doc": 50 * 1024 * 1024,    # 50 MB
    ".docx": 50 * 1024 * 1024,   # 50 MB
    ".xlsx": 50 * 1024 * 1024,   # 50 MB
    ".pdf": 100 * 1024 * 1024,   # 100 MB
    ".pptx": 100 * 1024 * 1024,  # 100 MB
    ".dita": 10 * 1024 * 1024,   # 10 MB
    ".ditamap": 10 * 1024 * 1024,# 10 MB
    ".xml": 10 * 1024 * 1024,    # 10 MB
    ".svg": 20 * 1024 * 1024,    # 20 MB
    ".yaml": 10 * 1024 * 1024,   # 10 MB
    ".yml": 10 * 1024 * 1024,    # 10 MB
    ".json": 10 * 1024 * 1024,   # 10 MB
    ".php": 10 * 1024 * 1024,    # 10 MB
}


class FormatAdapter(ABC):
    """格式适配器抽象基类
    
    所有格式适配器必须继承此类并实现抽象方法。
    """

    def __init__(self, max_file_size: Optional[int] = None):
        """初始化适配器
        
        Args:
            max_file_size: 最大文件大小限制（字节），None 表示使用默认值
        """
        self._max_file_size = max_file_size

    def get_max_file_size(self) -> int:
        """获取最大文件大小限制
        
        Returns:
            int: 最大文件大小（字节）
        """
        if self._max_file_size is not None:
            return self._max_file_size
        
        # 使用格式特定的限制
        for ext in self.supported_extensions():
            if ext.lower() in FORMAT_SIZE_LIMITS:
                return FORMAT_SIZE_LIMITS[ext.lower()]
        
        return DEFAULT_MAX_FILE_SIZE

    def validate_file_size(self, raw_bytes: bytes, filename: str = "<unknown>") -> None:
        """验证文件大小
        
        Args:
            raw_bytes: 文件字节内容
            filename: 文件名（用于错误消息）
            
        Raises:
            FileTooLargeError: 当文件超过大小限制时
        """
        max_size = self.get_max_file_size()
        if len(raw_bytes) > max_size:
            raise FileTooLargeError(
                filename=filename,
                size=len(raw_bytes),
                max_size=max_size,
            )

    def parse_with_validation(self, raw_bytes: bytes, filename: str = "<unknown>") -> ParseResult:
        """带文件大小验证的解析
        
        Args:
            raw_bytes: 文档的原始字节内容
            filename: 文件名（用于错误消息）
            
        Returns:
            ParseResult: 包含 AST 和 Segment 列表的解析结果
            
        Raises:
            FileTooLargeError: 当文件超过大小限制时
            ParseError: 当文件内容损坏或无法解析时
        """
        self.validate_file_size(raw_bytes, filename)
        return self.parse(raw_bytes)

    def parse_with_options(
        self,
        raw_bytes: bytes,
        filename: str = "<unknown>",
        options: Optional[dict] = None,
    ) -> ParseResult:
        """带文件大小验证和解析选项的解析。

        默认适配器不使用额外选项；需要格式专属开关时由子类覆盖。
        """
        self.validate_file_size(raw_bytes, filename)
        return self.parse(raw_bytes)

    @abstractmethod
    def parse(self, raw_bytes: bytes) -> ParseResult:
        """解析文档字节流
        
        Args:
            raw_bytes: 文档的原始字节内容
            
        Returns:
            ParseResult: 包含 AST 和 Segment 列表的解析结果
            
        Raises:
            ParseError: 当文件内容损坏或无法解析时
            UnsupportedFormatError: 当文件格式不被支持时
        """
        pass

    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """返回支持的文件扩展名列表
        
        Returns:
            List[str]: 扩展名列表，如 [".docx", ".doc"]
        """
        pass

    def can_parse(self, filename: str) -> bool:
        """检查文件是否可被此适配器解析
        
        Args:
            filename: 文件名或文件路径
            
        Returns:
            bool: 如果文件扩展名在支持列表中返回 True
        """
        ext = Path(filename).suffix.lower()
        return ext in [e.lower() for e in self.supported_extensions()]
