"""Module: @role: ブラウザ上のセレクトボックス選択およびチェックボックス状態制御の実行ロジックを担当する。"""

import logging
import time
from typing import Any, Dict, Optional

from pynput.keyboard import Controller as KeyboardController, Key

from core.executor.browser_input_driver import BrowserInputDriver
from core.executor.browser_uia_finder import (
    find_uia_element,
    get_combobox_current_value,
    scroll_into_view,
)

logger = logging.getLogger(__name__)


def parse_checkbox_desired_state(raw_v: Any) -> bool:
    # Why: エクセル由来の多様な表記を普遍的に真偽値へ正規化
    if raw_v is None:
        return False
    if isinstance(raw_v, bool):
        return raw_v
    if isinstance(raw_v, (int, float)):
        return raw_v != 0
    s_v = str(raw_v).strip().lower()
    return s_v in ["true", "1", "yes", "y", "on", "あり", "有", "チェック", "○", "レ", "checked"]


def _resolve_control_from_point(
    auto: Any,
    last_win_args: Optional[Dict[str, Any]],
    opt_x: Optional[int],
    opt_y: Optional[int],
) -> Any:
    if opt_x is None or opt_y is None:
        return None
    try:
        target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
        rec_x = last_win_args.get("x", 0) if last_win_args else 0
        rec_y = last_win_args.get("y", 0) if last_win_args else 0
        from core.executor.input_helper import get_window_offset

        off_x, off_y = get_window_offset(target_hwnd, rec_x, rec_y)
        return auto.ControlFromPoint(int(opt_x + off_x), int(opt_y + off_y))
    except Exception:
        return None


def execute_select_option_action(
    input_driver: BrowserInputDriver,
    keyboard: KeyboardController,
    args: Dict[str, Any],
    selector: str,
    text: str,
    value: Any,
    attr_name: str,
    timeout_sec: float,
    last_win_args: Optional[Dict[str, Any]],
    res_data: Dict[str, Any],
) -> Dict[str, Any]:
    target_val = str(value if value is not None else text).strip()
    elem_name_arg = str(attr_name or args.get("element_name") or "").strip()
    opt_x = args.get("x")
    opt_y = args.get("y")

    sc_coords = scroll_into_view(
        selector,
        last_win_args,
        timeout_sec=max(2.5, min(timeout_sec, 6.0)),
        element_name=elem_name_arg,
        text=target_val,
        x=opt_x,
        y=opt_y,
    )
    if sc_coords:
        opt_x, opt_y = sc_coords
    elem = find_uia_element(
        selector,
        last_win_args,
        timeout_sec=max(2.5, min(timeout_sec, 6.0)),
        element_name=elem_name_arg,
        text=target_val,
        x=opt_x,
        y=opt_y,
    )

    selected_ok = False
    try:
        import uiautomation as auto

        ctrl = auto.ControlFromElement(elem.element_info._element) if elem else None
        cur_v = get_combobox_current_value(ctrl, elem, elem_name_arg)
        if cur_v and (cur_v.lower() == target_val.lower() or target_val.lower() in cur_v.lower()):
            logger.info(f"select_option: '{target_val}' is already selected.")
            res_data["selected"] = target_val
            return res_data

        if elem:
            rect = elem.rectangle()
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            if cx > 20 and cy > 20:
                input_driver.click_physical_coords(cx, cy, last_win_args)
            else:
                elem.click_input()
        elif opt_x is not None and opt_y is not None:
            input_driver.click_physical_coords(opt_x, opt_y, last_win_args)

        time.sleep(0.12)

        # Why: 展開状態では確定までValueが変化しないためEscで閉じフォーカスを保持
        keyboard.press(Key.esc)
        keyboard.release(Key.esc)
        time.sleep(0.08)

        if not ctrl:
            try:
                ctrl = auto.GetFocusedControl()
                if not ctrl or not ctrl.Exists(0, 0):
                    ctrl = _resolve_control_from_point(auto, last_win_args, opt_x, opt_y)
            except Exception:
                pass

        cur_v = get_combobox_current_value(ctrl, elem, elem_name_arg)
        if cur_v and (cur_v.lower() == target_val.lower() or target_val.lower() in cur_v.lower()):
            selected_ok = True

        if not selected_ok:
            keyboard.press(Key.home)
            keyboard.release(Key.home)
            time.sleep(0.08)

            prev_v = ""
            for step in range(30):
                cur_v = get_combobox_current_value(ctrl, elem, elem_name_arg)
                if cur_v and (cur_v.lower() == target_val.lower() or target_val.lower() in cur_v.lower()):
                    logger.info(f"select_option: matched '{target_val}' at step {step} (detected: '{cur_v}')")
                    selected_ok = True
                    break
                if cur_v and cur_v == prev_v and step > 1:
                    break
                prev_v = cur_v
                keyboard.press(Key.down)
                keyboard.release(Key.down)
                time.sleep(0.08)

        keyboard.press(Key.enter)
        keyboard.release(Key.enter)
        time.sleep(0.06)
        res_data["selected"] = target_val
    except Exception as e:
        logger.warning(f"select_option failed: {e}")
        res_data["status"] = "failed"

    return res_data


def execute_set_checkbox_action(
    input_driver: BrowserInputDriver,
    args: Dict[str, Any],
    selector: str,
    text: str,
    value: Any,
    attr_name: str,
    timeout_sec: float,
    last_win_args: Optional[Dict[str, Any]],
    res_data: Dict[str, Any],
) -> Dict[str, Any]:
    desired = parse_checkbox_desired_state(value if value is not None else text)
    opt_x = args.get("x")
    opt_y = args.get("y")
    elem_name_arg = str(attr_name or args.get("element_name") or "").strip()

    sc_coords = scroll_into_view(
        selector,
        last_win_args,
        timeout_sec=max(2.5, min(timeout_sec, 6.0)),
        element_name=elem_name_arg,
        text=text,
        x=opt_x,
        y=opt_y,
    )
    if sc_coords:
        opt_x, opt_y = sc_coords
    elem = find_uia_element(
        selector,
        last_win_args,
        timeout_sec=max(2.5, min(timeout_sec, 6.0)),
        element_name=elem_name_arg,
        text=text,
        x=opt_x,
        y=opt_y,
    )

    import uiautomation as auto

    ctrl = auto.ControlFromElement(elem.element_info._element) if elem else None
    if not ctrl:
        ctrl = _resolve_control_from_point(auto, last_win_args, opt_x, opt_y)

    is_checked = None
    if ctrl and ctrl.Exists(0, 0):
        try:
            tog_pat = ctrl.GetTogglePattern()
            if tog_pat:
                is_checked = tog_pat.ToggleState == 1
        except Exception:
            pass
        if is_checked is None:
            try:
                leg_pat = ctrl.GetLegacyIAccessiblePattern()
                if leg_pat:
                    is_checked = bool(leg_pat.CurrentState & 0x10)
            except Exception:
                pass

    # Why: 既に希望状態であれば不要な反転クリックを完全抑止
    if is_checked is not None and is_checked == desired:
        logger.info(f"set_checkbox: '{selector}' is already {desired}.")
        res_data["checked"] = desired
        return res_data

    toggled = False
    if ctrl and ctrl.Exists(0, 0):
        try:
            tog_pat = ctrl.GetTogglePattern()
            if tog_pat:
                tog_pat.Toggle()
                time.sleep(0.08)
                toggled = tog_pat.ToggleState == (1 if desired else 0)
        except Exception:
            pass

    if not toggled:
        cx, cy = None, None
        if elem:
            rect = elem.rectangle()
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
        elif opt_x is not None and opt_y is not None:
            cx, cy = opt_x, opt_y

        if cx is not None and cy is not None and cx > 20 and cy > 20:
            if input_driver.click_physical_coords(cx, cy, last_win_args):
                toggled = True

    if not toggled and elem:
        try:
            elem.click_input()
            toggled = True
        except Exception:
            pass

    time.sleep(0.06)
    res_data["checked"] = desired
    if not toggled and is_checked is None:
        res_data["status"] = "element_not_found"
    return res_data
