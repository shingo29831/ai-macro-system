"""Module: @role: Excelワークブックからのレコード抽出（openpyxl/COMフォールバック）、ステータス更新、およびループエラー行スキップを担う。"""

import logging
from pathlib import Path
import platform
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.executor.excel_utils import (
    build_excel_row_record,
    col_idx_to_letter,
    is_completed_record,
    is_file_locked,
)

logger = logging.getLogger(__name__)


def _resolve_excel_path(file_path: str, target_dir: Optional[Path] = None) -> str:
    # Why: 相対パス指定時もtarget_dirからのフォールバック解決によりファイル未検出を根絶
    p = Path(file_path)
    if not p.is_absolute() and target_dir and (target_dir / file_path).exists():
        return str((target_dir / file_path).resolve())
    return str(p.resolve())


def _read_records_openpyxl(
    resolved: str,
    sheet_name: Optional[str],
    start_row: int,
    end_row: Optional[int],
    status_col: Optional[str],
    skip_completed: bool,
) -> List[Dict[str, Any]]:
    import openpyxl

    wb = openpyxl.load_workbook(resolved, data_only=True)
    try:
        ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
        header_map: Dict[str, str] = {}
        for col_idx in range(1, ws.max_column + 1):
            val = ws.cell(row=1, column=col_idx).value
            if val is not None:
                header_map[col_idx_to_letter(col_idx)] = str(val).strip()

        records: List[Dict[str, Any]] = []
        max_r = end_row or ws.max_row
        for r in range(start_row, max_r + 1):
            cells = (ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1))
            row_dict = build_excel_row_record(r, cells, header_map)
            if row_dict is None:
                continue
            if skip_completed and is_completed_record(row_dict, status_col):
                continue
            records.append(row_dict)
        return records
    finally:
        wb.close()


def _read_records_com(
    resolved: str,
    file_name: str,
    sheet_name: Optional[str],
    start_row: int,
    end_row: Optional[int],
    status_col: Optional[str],
    skip_completed: bool,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    excel = None
    should_quit = False
    try:
        import win32com.client

        # Why: 既に開かれているExcelからのインメモリ直接読み取りを優先し切断・排他ロック競合を完全回避
        try:
            excel = win32com.client.GetActiveObject("Excel.Application")
        except Exception:
            excel = win32com.client.Dispatch("Excel.Application")
            should_quit = True

        wb = None
        for open_wb in excel.Workbooks:
            if open_wb.FullName.lower() == resolved.lower() or open_wb.Name.lower() == file_name.lower():
                wb = open_wb
                break

        should_close = False
        if wb is None:
            wb = excel.Workbooks.Open(resolved, ReadOnly=True)
            should_close = True

        ws = wb.Sheets(sheet_name) if sheet_name else wb.ActiveSheet
        vals = None
        for _ in range(3):
            try:
                vals = ws.UsedRange.Value
                break
            except Exception:
                time.sleep(0.2)

        if should_close:
            wb.Close(SaveChanges=False)

        if vals and len(vals) >= start_row:
            headers = vals[0]
            header_map = {
                col_idx_to_letter(idx + 1): str(h).strip()
                for idx, h in enumerate(headers)
                if h is not None
            }
            max_r = min(end_row, len(vals)) if end_row else len(vals)
            for r in range(start_row, max_r + 1):
                row_dict = build_excel_row_record(r, vals[r - 1], header_map)
                if row_dict is None:
                    continue
                if skip_completed and is_completed_record(row_dict, status_col):
                    continue
                records.append(row_dict)
    except Exception as com_err:
        logger.error(f"COM fallback read also failed: {com_err}")
    finally:
        if excel and should_quit:
            try:
                excel.Quit()
            except Exception:
                pass
            del excel
    return records


def read_excel_records(
    file_path: str,
    sheet_name: Optional[str] = None,
    start_row: int = 2,
    end_row: Optional[int] = None,
    status_col: Optional[str] = None,
    skip_completed: bool = True,
    target_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    if not file_path:
        return []
    resolved = _resolve_excel_path(file_path, target_dir)
    if not Path(resolved).exists():
        logger.error(f"Excel file not found: {resolved}")
        return []

    try:
        return _read_records_openpyxl(resolved, sheet_name, start_row, end_row, status_col, skip_completed)
    except Exception as e:
        logger.warning(f"openpyxl headless read failed ({e}), falling back to COM...")

    if platform.system() == "Windows":
        return _read_records_com(
            resolved, Path(file_path).name, sheet_name, start_row, end_row, status_col, skip_completed
        )
    return []


def _col_letter_to_idx(col_str: str) -> Optional[int]:
    s = col_str.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", s):
        return None
    idx = 0
    for ch in s:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx if 1 <= idx <= 16384 else None


def _resolve_status_col_index(headers: Iterable[Any], status_col: str) -> Optional[int]:
    # Why: ID等のアルファベット短ヘッダーを列記号と誤認しないようヘッダー一致を優先解決
    target = status_col.strip()
    if not target:
        return None
    target_lower = target.lower()
    for idx, h in enumerate(headers, start=1):
        if h is not None and str(h).strip().lower() == target_lower:
            return idx
    return _col_letter_to_idx(target)


def write_excel_status(
    file_path: str,
    sheet_name: Optional[str],
    row_idx: int,
    status_col: str,
    status_text: str,
    target_dir: Optional[Path] = None,
) -> None:
    # Why: 開かれているExcelはCOMインメモリ即時更新、非オープン時はopenpyxl直接保存を自動選択
    if not file_path or not status_col or not row_idx:
        return
    resolved = _resolve_excel_path(file_path, target_dir)

    if not is_file_locked(resolved):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(resolved)
            try:
                ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
                headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
                col_idx = _resolve_status_col_index(headers, status_col)
                if col_idx is None:
                    logger.warning(f"Status column '{status_col}' could not be resolved in {resolved}")
                    return
                ws.cell(row=row_idx, column=col_idx, value=status_text)
                wb.save(resolved)
                return
            finally:
                wb.close()
        except Exception as e:
            logger.warning(f"openpyxl status update failed ({e}), routing to COM...")

    if platform.system() == "Windows":
        excel = None
        should_quit = False
        try:
            import win32com.client

            try:
                excel = win32com.client.GetActiveObject("Excel.Application")
            except Exception:
                excel = win32com.client.Dispatch("Excel.Application")
                should_quit = True
            wb = None
            for open_wb in excel.Workbooks:
                if open_wb.FullName.lower() == resolved.lower() or open_wb.Name.lower() == Path(file_path).name.lower():
                    wb = open_wb
                    break
            should_close = False
            if wb is None:
                wb = excel.Workbooks.Open(resolved)
                should_close = True
            ws = wb.Sheets(sheet_name) if sheet_name else wb.ActiveSheet
            # Why: COMビジー時のRPC拒否に備え最大3回のリトライバックオフを実施
            for _ in range(3):
                try:
                    used = ws.UsedRange
                    max_col = max(1, used.Column + used.Columns.Count - 1)
                    raw_headers = ws.Range(ws.Cells(1, 1), ws.Cells(1, max_col)).Value
                    if isinstance(raw_headers, tuple):
                        first_row = raw_headers[0] if raw_headers and isinstance(raw_headers[0], tuple) else raw_headers
                        headers = list(first_row)
                    else:
                        headers = [raw_headers]
                    col_idx = _resolve_status_col_index(headers, status_col)
                    if col_idx is None:
                        logger.warning(f"Status column '{status_col}' could not be resolved via COM in {resolved}")
                        break
                    ws.Cells(row_idx, col_idx).Value = status_text
                    wb.Save()
                    break
                except Exception:
                    time.sleep(0.3)
            if should_close:
                wb.Close(SaveChanges=True)
        except Exception as com_err:
            logger.error(f"COM status update failed: {com_err}")
        finally:
            if excel and should_quit:
                try:
                    excel.Quit()
                except Exception:
                    pass
                del excel


def skip_failed_loop_step(
    current_i: int,
    commands: List[Dict[str, Any]],
    loop_stack: List[Dict[str, Any]],
    workflow_id: str,
    update_ui: Optional[Callable[[str, bool], None]],
    err_msg: str,
) -> int:
    # Why: エラー発生時に行ステータスを更新し対応するloop_endまで安全にジャンプして次行へ継続
    curr_loop = loop_stack[-1]
    curr_loop["current_had_error"] = True
    logger.warning(f"[{workflow_id}] Step error: {err_msg}. continue_on_error enabled, skipping to next record.")
    if curr_loop.get("data_source") == "excel":
        curr_rec = curr_loop["records"][curr_loop["current_iteration"]]
        st_col = curr_loop.get("status_column")
        f_path = curr_loop.get("file_path")
        s_name = curr_loop.get("sheet_name")
        t_dir = curr_loop.get("target_dir")
        if st_col and f_path:
            write_excel_status(f_path, s_name, curr_rec.get("_row_idx"), st_col, f"エラー: {str(err_msg)[:25]}", target_dir=t_dir)
    if update_ui:
        update_ui(f"エラー行スキップ: {err_msg[:20]}", is_warning=True)
    nest = 1
    j = current_i + 1
    while j < len(commands) and nest > 0:
        if commands[j].get("method") == "loop_start":
            nest += 1
        elif commands[j].get("method") == "loop_end":
            nest -= 1
        if nest == 0:
            break
        j += 1
    return j
