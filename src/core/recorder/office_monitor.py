# @role: ExcelのCOMイベントを非同期で監視し、セルの選択移動や入力確定をフックする。

import threading
import time
import win32com.client
import pythoncom
from typing import Any, Callable

# COMイベントクラスからコールバックを呼ぶためのモジュール変数
_excel_callback = None


def _normalize_com_cell_value(raw_val: Any) -> str:
    """Excel COMのTarget.Value(日時・Decimal・2次元タプル等)をJSONシリアライズ安全な文字列へ正規化する。"""
    if raw_val is None:
        return ""
    if isinstance(raw_val, (tuple, list)):
        flat: list[str] = []
        for item in raw_val:
            if isinstance(item, (tuple, list)):
                for sub in item:
                    norm_sub = _normalize_com_cell_value(sub)
                    if norm_sub != "":
                        flat.append(norm_sub)
            else:
                norm_item = _normalize_com_cell_value(item)
                if norm_item != "":
                    flat.append(norm_item)
        return flat[0] if len(flat) == 1 else (", ".join(flat) if flat else "")
    if isinstance(raw_val, bool):
        return "TRUE" if raw_val else "FALSE"
    if isinstance(raw_val, int):
        return str(raw_val)
    if isinstance(raw_val, float):
        return str(int(raw_val)) if raw_val.is_integer() else str(raw_val)
    if hasattr(raw_val, "isoformat"):
        try:
            return str(raw_val.isoformat())
        except Exception:
            return str(raw_val)
    val_str = str(raw_val).strip()
    if val_str.endswith(".0") and val_str[:-2].lstrip("-").isdigit():
        return val_str[:-2]
    return val_str


def _normalize_com_structured_value(raw_val: Any, is_range: bool = False) -> Any:
    """範囲操作時は2次元リスト構造（または全クリア時None）を保持し、単一セル時は文字列へ正規化する。"""
    if not is_range:
        return _normalize_com_cell_value(raw_val)
    if raw_val is None:
        return None
    if isinstance(raw_val, (tuple, list)):
        matrix: list[list[str]] = []
        has_any_value = False
        for row in raw_val:
            if isinstance(row, (tuple, list)):
                norm_row = [_normalize_com_cell_value(cell) for cell in row]
            else:
                norm_row = [_normalize_com_cell_value(row)]
            if any(c != "" for c in norm_row):
                has_any_value = True
            matrix.append(norm_row)
        return matrix if has_any_value else None
    norm_scalar = _normalize_com_cell_value(raw_val)
    return norm_scalar if norm_scalar != "" else None


class ExcelEvents:
    """Excelのイベントハンドラ"""
    def OnSheetChange(self, Sh, Target):
        if _excel_callback:
            try:
                sheet_name = str(Sh.Name)
                raw_addr = str(Target.Address)
                # Why: 範囲貼り付け・クリア時の複数セル番地($A$1:$B$2)を切り詰めず保持
                cell_addr = raw_addr.replace("$", "")
                is_range = ":" in cell_addr
                structured_val = _normalize_com_structured_value(Target.Value, is_range=is_range)
                msg_val = _normalize_com_cell_value(Target.Value)
                _excel_callback({
                    "type": "excel",
                    "event_type": "cell_change",
                    "sheet": sheet_name,
                    "cell": cell_addr,
                    "is_range": is_range,
                    "value": structured_val,
                    "message": f"✏️ [入力確定] シート: '{sheet_name}' | セル: {raw_addr} | 値: {msg_val}",
                })
            except Exception:
                pass

    def OnSheetSelectionChange(self, Sh, Target):
        if _excel_callback:
            try:
                sheet_name = str(Sh.Name)
                raw_addr = str(Target.Address)
                cell_addr = raw_addr.replace("$", "")
                _excel_callback({
                    "type": "excel",
                    "event_type": "selection_change",
                    "sheet": sheet_name,
                    "cell": cell_addr,
                    "is_range": ":" in cell_addr,
                    "message": f"🖱️ [選択移動] シート: '{sheet_name}' | セル: {raw_addr}",
                })
            except Exception:
                pass

class OfficeMonitorManager:
    def __init__(self, callback: Callable[[dict], None]):
        global _excel_callback
        _excel_callback = callback
        self._is_running = False
        self._thread = None

    def start(self):
        if self._is_running:
            return
        self._is_running = True
        # 背景: COMオブジェクトはスレッド固有の初期化が必要なため、独立したスレッドを立てる
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._is_running = False

    def _monitor_loop(self):
        pythoncom.CoInitialize()
        attached = False
        excel = None
        events = None
        
        while self._is_running:
            if not attached:
                try:
                    # 起動中のExcelにアタッチを試みる
                    excel = win32com.client.GetActiveObject("Excel.Application")
                    events = win32com.client.WithEvents(excel, ExcelEvents)
                    attached = True
                    if _excel_callback:
                        _excel_callback({
                            "type": "excel",
                            "event_type": "system",
                            "message": "✅ [System] Excelプロセスを検知し、監視を接続しました。",
                        })
                except Exception:
                    pass # 起動していない場合は静かに待機
                    
            if attached:
                try:
                    # Excel側で発生したイベントをPython側にディスパッチする
                    pythoncom.PumpWaitingMessages()
                except Exception:
                    # Excelが閉じられた場合
                    attached = False
                    excel = None
                    events = None
                    if _excel_callback:
                        _excel_callback({
                            "type": "excel",
                            "event_type": "system",
                            "message": "❌ [System] Excelプロセスから切断されました。",
                        })
            
            time.sleep(0.1)
            
        pythoncom.CoUninitialize()
