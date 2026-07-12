# Role: プロセス名やウィンドウ名から適切なインスペクターを生成するFactory

from typing import Optional, Dict, Any
from .base_inspector import BaseInspector
from .excel_inspector import ExcelInspector
from .browser_inspector import BrowserInspector

class InspectorFactory:
    @staticmethod
    def get_inspector(window_info: Dict[str, Any]) -> Optional[BaseInspector]:
        process_name = str(window_info.get("process_name", "")).lower()
        if "excel.exe" in process_name:
            return ExcelInspector()
        elif "chrome.exe" in process_name or "msedge.exe" in process_name:
            return BrowserInspector()
        return None