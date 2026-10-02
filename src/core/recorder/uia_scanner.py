# @role: UIAutomationを用いて、現在フォーカスが当たっている要素や展開されたリスト（補完候補）の情報を取得する。

import time
from typing import Optional, Dict, Any
import uiautomation as auto

from core.recorder.window_inspector import should_ignore_window

def get_focused_element_info() -> Optional[Dict[str, Any]]:
    try:
        # 背景: Tabキー押下直後はUIの描画やアニメーションが完了していない可能性があるため微小な待機を入れる
        time.sleep(0.15)
        
        focused_elem = auto.GetFocusedControl()
        if not focused_elem:
            return None
            
        top_window = focused_elem.GetTopLevelControl()
        # Why: 記録ダイアログ等のシステムUIへのフォーカス誤検知を遮断
        if top_window and should_ignore_window(top_window.Name):
            return None

        info = {
            "name": focused_elem.Name,
            "control_type": focused_elem.ControlTypeName,
            "value": "",
            "candidates": []
        }
        
        # Why: ValuePattern / LegacyIAccessible / コントロール名から安全に確定値を順次試行
        val = ""
        try:
            val = focused_elem.GetValuePattern().Value
        except Exception:
            pass
        if not val:
            try:
                legacy = focused_elem.GetLegacyIAccessiblePattern()
                if legacy and legacy.Value:
                    val = legacy.Value
            except Exception:
                pass
        if not val and focused_elem.ControlType in [auto.ControlType.ListItemControl, auto.ControlType.MenuItemControl]:
            val = focused_elem.Name
        info["value"] = val or ""

        # Why: ドロップダウンやオートコンプリート候補リストを複数深度・ポップアップから取得
        top_window = focused_elem.GetTopLevelControl()
        if top_window:
            try:
                list_ctrl = top_window.ListControl(searchDepth=4)
                if list_ctrl.Exists(0, 0):
                    info["candidates"] = [it.Name for it in list_ctrl.GetChildren() if it.Name]
            except Exception:
                pass
            if not info["candidates"]:
                try:
                    for child in top_window.GetChildren():
                        if child.ControlType in [auto.ControlType.ListControl, auto.ControlType.MenuControl, auto.ControlType.PaneControl]:
                            c_items = [c.Name for c in child.GetChildren() if c.Name and c.ControlType == auto.ControlType.ListItemControl]
                            if c_items:
                                info["candidates"] = c_items
                                break
                except Exception:
                    pass

        return info

    except Exception as e:
        # 背景: UI要素の取得失敗はフリーズ等で頻発するため、アプリを落とさずエラー詳細をログとして返す
        return {"error": str(e)}