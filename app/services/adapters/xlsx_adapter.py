"""
XLSX 适配器模块 - 解析 Excel 文件用于 TM 导入

Requirements: 7.1, 7.2, 7.3, 7.4
"""
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional

from openpyxl import load_workbook

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    DocumentAST,
    ParseResult,
    TMEntry,
    TMImportResult,
)


@dataclass
class ColumnMapping:
    """列映射配置"""
    source_column: int = 0  # 源文本列索引（从 0 开始）
    target_column: int = 1  # 目标文本列索引
    skip_header: bool = True  # 是否跳过表头行


class XlsxAdapter(FormatAdapter):
    """XLSX 文件适配器（TM 导入专用）
    
    用于从 Excel 文件导入翻译记忆，支持可配置的列映射。
    """

    def __init__(self, column_mapping: Optional[ColumnMapping] = None):
        """初始化适配器
        
        Args:
            column_mapping: 列映射配置，默认为源文本在 A 列，目标文本在 B 列
        """
        self.column_mapping = column_mapping or ColumnMapping()

    def supported_extensions(self) -> List[str]:
        return [".xlsx"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        """解析 XLSX 文件
        
        注意：此适配器返回的 ParseResult 中 ast 为空，
        实际数据在 metadata["tm_import_result"] 中。
        
        Args:
            raw_bytes: 文件字节内容
            
        Returns:
            ParseResult: 解析结果，包含 TM 导入数据
            
        Raises:
            ParseError: 当文件损坏或无法解析时
        """
        if not raw_bytes:
            return self._empty_result()
        
        try:
            workbook = load_workbook(BytesIO(raw_bytes), read_only=True, data_only=True)
        except Exception as e:
            raise ParseError(
                filename="<unknown>",
                reason=f"无法解析 XLSX 文件: {str(e)}"
            )
        
        entries: List[TMEntry] = []
        total_rows = 0
        skipped_rows = 0
        
        for sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]
            sheet_entries, sheet_total, sheet_skipped = self._parse_sheet(
                sheet, sheet_name
            )
            entries.extend(sheet_entries)
            total_rows += sheet_total
            skipped_rows += sheet_skipped
        
        workbook.close()
        
        tm_result = TMImportResult(
            entries=entries,
            skipped_rows=skipped_rows,
            total_rows=total_rows,
        )
        
        return ParseResult(
            ast=DocumentAST(nodes=[], source_format=".xlsx"),
            segments=[],
            metadata={"tm_import_result": tm_result},
        )

    def parse_for_tm(self, raw_bytes: bytes) -> TMImportResult:
        """直接解析为 TM 导入结果
        
        Args:
            raw_bytes: 文件字节内容
            
        Returns:
            TMImportResult: TM 导入结果
        """
        result = self.parse(raw_bytes)
        return result.metadata.get("tm_import_result", TMImportResult([], 0, 0))

    def _parse_sheet(self, sheet, sheet_name: str) -> tuple[List[TMEntry], int, int]:
        """解析单个工作表
        
        Args:
            sheet: openpyxl 工作表对象
            sheet_name: 工作表名称
            
        Returns:
            tuple: (条目列表, 总行数, 跳过行数)
        """
        entries: List[TMEntry] = []
        total_rows = 0
        skipped_rows = 0
        
        rows = list(sheet.iter_rows(values_only=True))
        start_row = 1 if self.column_mapping.skip_header else 0
        
        for row_idx, row in enumerate(rows[start_row:], start=start_row):
            total_rows += 1
            
            # 获取源文本和目标文本
            source_text = self._get_cell_value(row, self.column_mapping.source_column)
            target_text = self._get_cell_value(row, self.column_mapping.target_column)
            
            # 跳过空源文本的行
            if not source_text or not source_text.strip():
                skipped_rows += 1
                continue
            
            entry = TMEntry(
                source_text=source_text.strip(),
                target_text=(target_text or "").strip(),
                metadata={
                    "sheet": sheet_name,
                    "row": row_idx + 1,  # Excel 行号从 1 开始
                },
            )
            entries.append(entry)
        
        return entries, total_rows, skipped_rows

    def _get_cell_value(self, row: tuple, column_index: int) -> Optional[str]:
        """获取单元格值
        
        Args:
            row: 行数据元组
            column_index: 列索引
            
        Returns:
            Optional[str]: 单元格文本值
        """
        if column_index >= len(row):
            return None
        
        value = row[column_index]
        if value is None:
            return None
        
        return str(value)

    def _empty_result(self) -> ParseResult:
        """返回空结果"""
        return ParseResult(
            ast=DocumentAST(nodes=[], source_format=".xlsx"),
            segments=[],
            metadata={
                "tm_import_result": TMImportResult(entries=[], skipped_rows=0, total_rows=0)
            },
        )
