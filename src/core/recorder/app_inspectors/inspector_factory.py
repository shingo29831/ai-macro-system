# Role: プロセス名やウィンドウ名から適切なインスペクターを生成するFactory

from typing import Optional, Dict, Any
from core.shared.app_log_profile import AppProfile, detect_app_profile, detect_office_sub_app
from .base_inspector import BaseInspector
from .excel_inspector import ExcelInspector
from .browser_inspector import BrowserInspector

class InspectorFactory:
    @staticmethod
    def get_inspector(window_info: Dict[str, Any]) -> Optional[BaseInspector]:
        profile = detect_app_profile(window_info)
        if profile == AppProfile.OFFICE and detect_office_sub_app(window_info) == "Excel":
            return ExcelInspector()
        elif profile == AppProfile.BROWSER:
            return BrowserInspector()
        return None
