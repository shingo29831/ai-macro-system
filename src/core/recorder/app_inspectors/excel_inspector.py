# Role: Excel特有のUIA/API情報（セル内容や数式バーのテキストなど）を取得する実装

from typing import Any, Dict, Optional
import traceback
from .base_inspector import BaseInspector

class ExcelInspector(BaseInspector):
    def inspect(self, window_info: Dict[str, Any], x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        result = {"app": "Excel"}
        try:
            import pywinauto
            import pythoncom
            # COMの初期化 (別スレッドから呼ばれる場合を考慮して安全に初期化)
            pythoncom.CoInitialize()

            # x, y が指定されている場合はその座標の要素を調べる
            if x is not None and y is not None:
                desktop = pywinauto.Desktop(backend="uia")
                element = desktop.from_point(int(x), int(y))
                
                try:
                    result["element_name"] = element.window_text()
                except Exception:
                    pass
                
                try:
                    result["control_type"] = getattr(element.element_info, "control_type", "")
                except Exception:
                    pass

                # TODO: 数式バーやアクティブセルの値取得などのより詳細なUIAアクセスを追加
                # (現状はタブ補完後の文字取得などを前提とした基本的な要素情報のみを取得)
                
        except ImportError:
            result["error"] = "pywinauto or pywin32 not installed"
        except Exception as e:
            result["error"] = str(e)
            traceback.print_exc()
        finally:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
                
        return result