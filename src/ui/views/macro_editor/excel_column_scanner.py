"""Module: @role: Excelワークブックの1行目ヘッダーを軽量スキャンし、列文字とヘッダー名のマップを生成する。"""

from pathlib import Path
from typing import Dict, Optional

DEFAULT_COLUMN_COUNT = 8
MAX_SCAN_COLUMNS = 26


def _default_columns_map() -> Dict[str, str]:
    return {chr(65 + i): f"列{chr(65 + i)}" for i in range(DEFAULT_COLUMN_COUNT)}


def get_excel_columns_map(
    file_path: str,
    sheet_name: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> Dict[str, str]:
    """Excelの1行目を軽量スキャンし、列文字とヘッダー名の辞書を返す"""
    if not file_path:
        return _default_columns_map()
    p = Path(file_path)
    if not p.exists() and base_dir and (base_dir / file_path).exists():
        p = base_dir / file_path
    if not p.exists():
        return _default_columns_map()

    import openpyxl

    wb = None
    try:
        resolved = str(p.resolve())
        wb = openpyxl.load_workbook(resolved, read_only=True, data_only=True)
        ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
        cols: Dict[str, str] = {}
        max_col = min(ws.max_column or DEFAULT_COLUMN_COUNT, MAX_SCAN_COLUMNS)
        for col_idx in range(1, max_col + 1):
            letter = openpyxl.utils.get_column_letter(col_idx)
            val = ws.cell(row=1, column=col_idx).value
            cols[letter] = str(val).strip() if val is not None else f"列{letter}"
        return cols if cols else _default_columns_map()
    except Exception:
        return _default_columns_map()
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass
