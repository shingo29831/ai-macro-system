"""Module: @role: マクロ実行エンジンにおけるExcel操作のファサードおよび個別Excelアクションの実行ディスパッチを担う。"""

import ctypes
import logging
from pathlib import Path
import platform
from typing import Any, Dict, Optional, Tuple

from core.executor.excel_com_client import get_excel_application
from core.executor.excel_data_io import (
    read_excel_records,
    skip_failed_loop_step,
    write_excel_status,
)
from core.executor.excel_utils import (
    col_idx_to_letter,
    extract_referenced_columns,
    is_file_locked,
)

__all__ = [
    "col_idx_to_letter",
    "extract_referenced_columns",
    "is_file_locked",
    "read_excel_records",
    "write_excel_status",
    "skip_failed_loop_step",
    "get_excel_application",
    "execute_excel_action",
]

logger = logging.getLogger(__name__)


def execute_excel_action(args: Dict[str, Any], variables: Dict[str, Any], excel_app: Any, last_win_args: Optional[Dict[str, Any]], workflow_id: str) -> Tuple[Any, Any]:
    if platform.system() != "Windows":
        logger.warning(f"[{workflow_id}] Excel COM automation is only supported on Windows.")
        return excel_app, None

    import win32com.client
    import win32gui

    action = args.get("action", "")
    file_path = args.get("file_path")
    sheet_name = args.get("sheet_name")
    cell = args.get("cell")
    range_addr = args.get("range_address") or cell
    value = args.get("value")
    var_name = args.get("variable_name")
    macro_name = args.get("macro_name")
    close_save = args.get("close_save", True)

    if isinstance(value, str):
        for k, v in variables.items():
            value = value.replace(f"{{{{{k}}}}}", str(v)).replace(f"${{{k}}}", str(v))

    if excel_app is None:
        target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else None) or win32gui.GetForegroundWindow()
        expected_t = last_win_args.get("window_title", "") if last_win_args else ""
        excel_app = get_excel_application(target_hwnd, expected_t)

    if excel_app is None and action == "open_workbook":
        excel_app = win32com.client.Dispatch("Excel.Application")
        excel_app.Visible = True

    if excel_app is None:
        try:
            excel_app = win32com.client.GetActiveObject("Excel.Application")
            excel_app.Visible = True
        except Exception:
            excel_app = win32com.client.Dispatch("Excel.Application")
            excel_app.Visible = True

    # Why: Excel操作時にウィンドウを前面化し他アプリとの切り替えを可視化
    try:
        if hasattr(excel_app, "Hwnd") and excel_app.Hwnd:
            ctypes.windll.user32.SetForegroundWindow(excel_app.Hwnd)
    except Exception:
        pass

    wb = None
    if file_path:
        resolved_path = str(Path(file_path).resolve())
        for open_wb in excel_app.Workbooks:
            if open_wb.FullName.lower() == resolved_path.lower() or open_wb.Name.lower() == Path(file_path).name.lower():
                wb = open_wb
                break
        if wb is None and action == "open_workbook":
            wb = excel_app.Workbooks.Open(resolved_path)

    if wb is None:
        try:
            wb = excel_app.ActiveWorkbook
        except Exception:
            wb = None

    sheet = None
    if wb:
        if sheet_name:
            try:
                sheet = wb.Sheets(sheet_name)
            except Exception:
                sheet = wb.ActiveSheet
        else:
            try:
                sheet = wb.ActiveSheet
            except Exception:
                pass

    result = None
    if action == "open_workbook":
        if wb:
            wb.Activate()
            result = wb.Name
    elif action == "save_workbook":
        if wb:
            if file_path and str(Path(file_path).resolve()).lower() != str(Path(wb.FullName).resolve()).lower():
                wb.SaveAs(str(Path(file_path).resolve()))
            else:
                wb.Save()
    elif action == "close_workbook":
        if wb:
            wb.Close(SaveChanges=close_save)
    elif action == "select_sheet":
        if sheet:
            sheet.Activate()
    elif action == "add_sheet":
        if wb:
            new_sheet = wb.Sheets.Add()
            if sheet_name:
                new_sheet.Name = sheet_name
            new_sheet.Activate()
    elif action == "read_cell":
        if sheet and cell:
            result = sheet.Range(cell).Value
            if var_name:
                variables[var_name] = result
                logger.info(f"[{workflow_id}] Read Excel cell {cell} -> variables['{var_name}'] = {result}")
    elif action == "write_cell":
        if sheet and cell:
            sheet.Range(cell).Value = value
    elif action == "read_range":
        if sheet and range_addr:
            raw_vals = sheet.Range(range_addr).Value
            result = [list(r) if isinstance(r, tuple) else r for r in raw_vals] if isinstance(raw_vals, tuple) else raw_vals
            if var_name:
                variables[var_name] = result
                logger.info(f"[{workflow_id}] Read Excel range {range_addr} -> variables['{var_name}']")
    elif action == "write_range":
        if sheet and range_addr:
            sheet.Range(range_addr).Value = value
    elif action == "insert_row":
        if sheet and cell:
            sheet.Rows(cell).Insert()
    elif action == "delete_row":
        if sheet and cell:
            sheet.Rows(cell).Delete()
    elif action == "clear_range":
        if sheet and range_addr:
            sheet.Range(range_addr).ClearContents()
    elif action == "run_macro":
        if macro_name:
            result = excel_app.Run(macro_name)
    elif action == "read_records":
        st_row = args.get("start_row", 2)
        end_r = args.get("end_row")
        st_col = args.get("status_column")
        skip_comp = args.get("skip_completed", True)
        records = read_excel_records(file_path, sheet_name, st_row, end_r, st_col, skip_comp)
        if var_name:
            variables[var_name] = records
        result = records
    elif action == "update_status":
        r_idx = args.get("row_index") or variables.get("_row_idx")
        st_col = args.get("status_column")
        st_val = str(args.get("value", "完了"))
        write_excel_status(file_path, sheet_name, r_idx, st_col, st_val)
        result = True
    else:
        logger.warning(f"[{workflow_id}] Unknown Excel action: {action}")

    return excel_app, result
