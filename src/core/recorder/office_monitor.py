# @role: ExcelのCOMイベントを非同期で監視し、セルの選択移動や入力確定をフックする。

import threading
import time
import win32com.client
import pythoncom
from typing import Callable

# COMイベントクラスからコールバックを呼ぶためのモジュール変数
_excel_callback = None

class ExcelEvents:
    """Excelのイベントハンドラ"""
    def OnSheetChange(self, Sh, Target):
        if _excel_callback:
            try:
                _excel_callback({
                    "type": "excel",
                    "message": f"✏️ [入力確定] シート: '{Sh.Name}' | セル: {Target.Address} | 値: {Target.Value}"
                })
            except Exception:
                pass

    def OnSheetSelectionChange(self, Sh, Target):
        if _excel_callback:
            try:
                _excel_callback({
                    "type": "excel",
                    "message": f"🖱️ [選択移動] シート: '{Sh.Name}' | セル: {Target.Address}"
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
                        _excel_callback({"type": "excel", "message": "✅ [System] Excelプロセスを検知し、監視を接続しました。"})
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
                        _excel_callback({"type": "excel", "message": "❌ [System] Excelプロセスから切断されました。"})
            
            time.sleep(0.1)
            
        pythoncom.CoUninitialize()