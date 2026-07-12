# @role: UIAutomationを用いて、現在フォーカスが当たっている要素や展開されたリスト（補完候補）の情報を取得する。

import time
from typing import Optional, Dict, Any
import uiautomation as auto

def get_focused_element_info() -> Optional[Dict[str, Any]]:
    try:
        # 背景: Tabキー押下直後はUIの描画やアニメーションが完了していない可能性があるため微小な待機を入れる
        time.sleep(0.15)
        
        focused_elem = auto.GetFocusedControl()
        if not focused_elem:
            return None
            
        info = {
            "name": focused_elem.Name,
            "control_type": focused_elem.ControlTypeName,
            "value": "",
            "candidates": []
        }
        
        # 修正: ValuePattern（入力確定後のテキスト取得）を安全に試行する
        try:
            info["value"] = focused_elem.GetValuePattern().Value
        except Exception:
            # ValuePatternをサポートしていないコントロールの場合はスキップ
            pass
            
        # 背景: ドロップダウンやコンボボックスの場合、子要素としてリストアイテムが展開されている可能性がある
        if focused_elem.ControlType in [auto.ControlType.ComboBoxControl, auto.ControlType.EditControl]:
            top_window = focused_elem.GetTopLevelControl()
            if top_window:
                list_control = top_window.ListControl(searchDepth=3)
                if list_control.Exists(0, 0):
                    items = list_control.GetChildren()
                    info["candidates"] = [item.Name for item in items if item.Name]

        return info

    except Exception as e:
        # 背景: UI要素の取得失敗はフリーズ等で頻発するため、アプリを落とさずエラー詳細をログとして返す
        return {"error": str(e)}