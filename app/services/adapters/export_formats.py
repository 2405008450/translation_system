"""
导出格式配置模块 - 定义各种文件格式支持的导出选项

导出类型：
- source: 上传源文件
- original: 原格式导出
- bilingual: 双语对照文件
- tmx: TMX 翻译记忆库格式
- xliff: XLIFF 工作流/离线文件
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class ExportOption:
    """导出选项"""
    id: str
    name: str
    description: str
    extension: str
    mime_type: str


# 标准导出选项定义
EXPORT_OPTIONS = {
    "source": ExportOption(
        id="source",
        name="源文件",
        description="下载上传时保留的原始源文件",
        extension="",  # 使用原扩展名
        mime_type="",  # 使用原 MIME 类型
    ),
    "original": ExportOption(
        id="original",
        name="目标文件",
        description="导出为原始文件格式，仅包含译文",
        extension="",  # 使用原扩展名
        mime_type="",  # 使用原 MIME 类型
    ),
    "bilingual": ExportOption(
        id="bilingual",
        name="双语文件",
        description="导出原格式双语对照文件（原文+译文）",
        extension="",  # 使用原扩展名
        mime_type="",  # 使用原 MIME 类型
    ),
    "bilingual_docx": ExportOption(
        id="bilingual_docx",
        name="双语 Word",
        description="导出为 Word 双语对照文档",
        extension=".docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "bilingual_docx_layout_source_first": ExportOption(
        id="bilingual_docx_layout_source_first",
        name="双语 Word（原文在前）",
        description="保留原 Word 排版，按原文、译文顺序导出",
        extension=".docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "bilingual_docx_layout_target_first": ExportOption(
        id="bilingual_docx_layout_target_first",
        name="双语 Word（译文在前）",
        description="保留原 Word 排版，按译文、原文顺序导出",
        extension=".docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "bilingual_txt": ExportOption(
        id="bilingual_txt",
        name="双语文本",
        description="导出源文和译文对照的纯文本文件",
        extension=".txt",
        mime_type="text/plain; charset=utf-8",
    ),
    "tmx": ExportOption(
        id="tmx",
        name="TMX 文档",
        description="导出为翻译记忆库交换格式，可导入其他 CAT 工具",
        extension=".tmx",
        mime_type="application/x-tmx+xml",
    ),
    "xliff": ExportOption(
        id="xliff",
        name="XLIFF 离线文件",
        description="导出为 XLIFF 格式，支持离线翻译和工作流",
        extension=".xlf",
        mime_type="application/xliff+xml",
    ),
    "xliff2": ExportOption(
        id="xliff2",
        name="XLIFF 2.0",
        description="导出为 XLIFF 2.0 格式",
        extension=".xlf",
        mime_type="application/xliff+xml",
    ),
    "bilingual_excel": ExportOption(
        id="bilingual_excel",
        name="双语 Excel",
        description="导出为 Excel 双语对照表格（原文/译文两列）",
        extension=".xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "bilingual_excel_original": ExportOption(
        id="bilingual_excel_original",
        name="Excel 原格式双语",
        description="保留原 Excel 工作簿元素，在原单元格内按原文、译文换行导出",
        extension=".xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}


# 各格式支持的导出选项
# 键: 文件扩展名, 值: 支持的导出选项 ID 列表
FORMAT_EXPORT_SUPPORT: Dict[str, List[str]] = {
    # 办公文档
    ".docx": [
        "original",
        "bilingual_docx_layout_source_first",
        "bilingual_docx_layout_target_first",
        "bilingual_docx",
        "bilingual_excel",
        "bilingual_txt",
        "tmx",
        "xliff",
    ],
    ".doc": [
        "original",
        "bilingual_docx_layout_source_first",
        "bilingual_docx_layout_target_first",
        "bilingual_docx",
        "bilingual_excel",
        "bilingual_txt",
        "tmx",
        "xliff",
    ],
    ".pdf": ["bilingual_docx", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],  # PDF 无法原格式导出
    ".pptx": ["original", "bilingual_docx", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".xlsx": ["original", "bilingual_excel_original", "bilingual_docx", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],

    # 纯文本
    ".txt": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".dat": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],

    # 本地化文件 - 支持所有导出选项
    ".properties": ["original", "bilingual", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".po": ["original", "bilingual", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".pot": ["original", "bilingual", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".strings": ["original", "bilingual", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".yaml": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".yml": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".json": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".php": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],

    # 网页/排版文件
    ".html": ["original", "bilingual", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".htm": ["original", "bilingual", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".md": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".markdown": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".csv": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".srt": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],

    # 技术写作文件
    ".dita": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".ditamap": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".xml": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".svg": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],

    # 双语文件 - 本身就是双语格式
    ".sdlxliff": ["original", "bilingual_excel", "tmx"],
    ".txml": ["original", "bilingual_excel", "tmx"],

    # 工程/设计文件
    ".dxf": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".idml": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],
    ".mif": ["original", "bilingual_excel", "bilingual_txt", "tmx", "xliff"],

    # 压缩包
    ".zip": ["original", "bilingual_excel", "tmx", "xliff"],
    ".rar": ["original", "bilingual_excel", "tmx", "xliff"],  # 导出为 ZIP
}


def get_supported_exports(extension: str) -> List[ExportOption]:
    """获取指定格式支持的导出选项
    
    Args:
        extension: 文件扩展名（如 ".html"）
        
    Returns:
        List[ExportOption]: 支持的导出选项列表
    """
    ext = extension.lower()
    option_ids = FORMAT_EXPORT_SUPPORT.get(ext, ["bilingual_txt", "tmx", "xliff"])
    option_ids = ["source", *option_ids]
    return [EXPORT_OPTIONS[opt_id] for opt_id in option_ids if opt_id in EXPORT_OPTIONS]


def get_export_option(option_id: str) -> Optional[ExportOption]:
    """获取导出选项详情"""
    return EXPORT_OPTIONS.get(option_id)
