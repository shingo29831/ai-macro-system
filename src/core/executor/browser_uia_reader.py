"""Module: @role: ブラウザ操作におけるUIA要素からのテキスト・属性・選択値読み取りおよび要素存在待機を担当する。"""

import time
from typing import Any, Dict, Optional

from core.executor.browser_uia_searcher import find_uia_element


def get_combobox_current_value(ctrl: Any, elem: Any, label_name: str = "") -> str:
    # Why: リアルタイムにComboBoxの選択値をUIA/LegacyIAccessible/Selectionから抽出
    if ctrl and ctrl.Exists(0, 0):
        try:
            sel_pat = ctrl.GetSelectionPattern()
            if sel_pat:
                sel_items = sel_pat.GetSelection()
                if sel_items and len(sel_items) > 0:
                    s_name = str(getattr(sel_items[0], "Name", "") or "").strip()
                    if s_name and s_name != label_name:
                        return s_name
        except Exception:
            pass
        try:
            val = str(ctrl.GetLegacyIAccessiblePattern().CurrentValue or "").strip()
            if val and val != label_name:
                return val
        except Exception:
            pass
        try:
            val = str(ctrl.GetValuePattern().Value or "").strip()
            if val and val != label_name:
                return val
        except Exception:
            pass
        try:
            c_name = str(getattr(ctrl, "Name", "") or "").strip()
            if c_name and c_name != label_name:
                return c_name
        except Exception:
            pass

    if elem:
        try:
            if elem.is_value_pattern_available():
                v = str(elem.get_value() or "").strip()
                if v and v != label_name:
                    return v
        except Exception:
            pass
        try:
            leg_val = str(elem.legacy_properties().get("Value", "") or "").strip()
            if leg_val and leg_val != label_name:
                return leg_val
        except Exception:
            pass
        try:
            w_txt = str(elem.window_text() or "").strip()
            if w_txt and w_txt != label_name:
                return w_txt
        except Exception:
            pass
    return ""


def read_text_by_uia_or_selector(selector: str, last_win_args: Optional[Dict[str, Any]], timeout: float) -> str:
    elem = find_uia_element(selector, last_win_args, timeout_sec=timeout)
    if elem:
        try:
            if elem.is_value_pattern_available():
                return str(elem.get_value() or "").strip()
        except Exception:
            pass
        return str(elem.window_text() or "").strip()
    return ""


def read_attr_by_uia(selector: str, attr_name: str, last_win_args: Optional[Dict[str, Any]], timeout: float) -> str:
    elem = find_uia_element(selector, last_win_args, timeout_sec=timeout)
    if elem:
        return str(getattr(elem.element_info, attr_name, "") or "")
    return ""


def wait_element_exist(selector: str, last_win_args: Optional[Dict[str, Any]], timeout: float) -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout:
        if find_uia_element(selector, last_win_args):
            return True
        time.sleep(0.5)
    return False
