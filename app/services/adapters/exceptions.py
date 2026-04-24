"""
异常类模块 - 定义适配器相关的所有异常类型

Requirements: 16.1, 16.2
"""


class AdapterError(Exception):
    """适配器基础异常"""
    pass


class UnsupportedFormatError(AdapterError):
    """不支持的格式异常
    
    当尝试解析不支持的文件格式时抛出
    """
    def __init__(self, extension: str):
        self.extension = extension
        super().__init__(f"Unsupported format: {extension}")


class ParseError(AdapterError):
    """解析错误异常
    
    当文件内容损坏或无法解析时抛出
    """
    def __init__(self, filename: str, reason: str):
        self.filename = filename
        self.reason = reason
        super().__init__(f"Failed to parse {filename}: {reason}")


class FileTooLargeError(AdapterError):
    """文件过大异常
    
    当文件超过配置的大小限制时抛出
    """
    def __init__(self, filename: str, size: int, max_size: int = None, limit: int = None):
        self.filename = filename
        self.size = size
        # 支持 max_size 和 limit 两种参数名
        self.limit = max_size if max_size is not None else limit
        self.max_size = self.limit
        super().__init__(
            f"File {filename} ({size} bytes) exceeds limit ({self.limit} bytes)"
        )


class OCRRequiredError(AdapterError):
    """需要 OCR 异常
    
    当 PDF 文件是扫描件或纯图片，没有嵌入文本时抛出
    """
    def __init__(self, filename: str):
        self.filename = filename
        super().__init__(
            f"File {filename} is image-based and requires OCR to extract text"
        )


class ExportError(AdapterError):
    """导出错误异常
    
    当导出文档失败时抛出
    """
    def __init__(self, format: str = None, reason: str = None, filename: str = None):
        self.format = format or filename
        self.filename = filename or format
        self.reason = reason
        super().__init__(f"Failed to export {self.format}: {reason}")
