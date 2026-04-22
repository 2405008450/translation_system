from __future__ import annotations

from io import BytesIO
from urllib.parse import quote

from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_tabular_xlsx(
    sheet_title: str,
    headers: list[str],
    rows: list[list[object]],
) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = _normalize_sheet_title(sheet_title)
    worksheet.append(headers)

    for row in rows:
        worksheet.append(list(row))

    worksheet.freeze_panes = "A2"
    if rows:
        worksheet.auto_filter.ref = worksheet.dimensions

    _adjust_column_widths(worksheet)

    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def build_xlsx_download_response(filename: str, xlsx_bytes: bytes) -> StreamingResponse:
    safe_filename = filename if filename.lower().endswith(".xlsx") else f"{filename}.xlsx"
    ascii_filename = safe_filename.encode("ascii", "ignore").decode("ascii").strip() or "export.xlsx"
    ascii_filename = ascii_filename.replace('"', "")
    quoted_filename = quote(safe_filename)

    return StreamingResponse(
        BytesIO(xlsx_bytes),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_filename}"; '
                f"filename*=UTF-8''{quoted_filename}"
            )
        },
    )


def _normalize_sheet_title(value: str) -> str:
    cleaned = "".join(char for char in value if char not in '\\/*?:[]')
    cleaned = cleaned.strip() or "Sheet1"
    return cleaned[:31]


def _adjust_column_widths(worksheet: Worksheet) -> None:
    for column_cells in worksheet.columns:
        max_length = 0
        for cell in column_cells:
            cell_value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, len(cell_value))

        column_letter = column_cells[0].column_letter
        worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 48)
