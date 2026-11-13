"""Module: @role: ブラウザ操作対象UIA要素のビューポート可視領域判定およびスクロール可視化を担当する。"""

import ctypes
import platform
import time
from typing import Any, Dict, Optional, Tuple

from core.executor.browser_uia_searcher import find_uia_element

SIMPLE_GENERIC_TAGS = {"div", "span", "p", "a", "li", "ul", "body"}


def _get_window_rect(hwnd: Optional[int]) -> Optional[Tuple[int, int, int, int]]:
    if not hwnd or platform.system() != "Windows":
        return None
    try:
        class _RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = _RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return None


def scroll_into_view(
    selector: Optional[str] = None,
    last_win_args: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 1.5,
    element_name: Optional[str] = None,
    url: Optional[str] = None,
    text: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    **kwargs: Any,
) -> Optional[Tuple[int, int]]:
    """対象要素がビューポート内に収まるようスクロールし、画面中心座標を返す。"""
    # Why: 汎用タグ単体セレクタまたは可視領域内座標は誤スクロール防止のため即時返却
    sel_clean = str(selector or "").strip().lower()
    if sel_clean in SIMPLE_GENERIC_TAGS:
        if x is not None and y is not None and x > 20 and y > 20:
            return (int(x), int(y))
        return None

    target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
    if not target_hwnd and platform.system() == "Windows":
        target_hwnd = ctypes.windll.user32.GetForegroundWindow()

    win_rect_pre = _get_window_rect(target_hwnd)
    if win_rect_pre and x is not None and y is not None:
        w_l, w_t, w_r, w_b = win_rect_pre
        if w_l <= x <= w_r and (w_t + 80) <= y <= (w_b - 40):
            return (int(x), int(y))

    elem = find_uia_element(
        selector=selector,
        last_win_args=last_win_args,
        timeout_sec=timeout_sec,
        element_name=element_name,
        url=url,
        text=text,
        x=x,
        y=y,
        **kwargs,
    )
    if not elem:
        return None

    import uiautomation as auto

    ctrl = None
    try:
        ctrl = auto.ControlFromElement(elem.element_info._element)
    except Exception:
        pass

    scrolled_via_pattern = False
    if ctrl and ctrl.Exists(0, 0):
        try:
            scroll_item = ctrl.GetScrollItemPattern()
            if scroll_item:
                scroll_item.ScrollIntoView()
                time.sleep(0.12)
                scrolled_via_pattern = True
        except Exception:
            pass

    if not scrolled_via_pattern:
        try:
            if hasattr(elem, "scroll_into_view"):
                elem.scroll_into_view()
                time.sleep(0.12)
                scrolled_via_pattern = True
        except Exception:
            pass

    win_rect = _get_window_rect(target_hwnd)
    for _ in range(4):
        rect = elem.rectangle()
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2

        if win_rect:
            w_left, w_top, w_right, w_bottom = win_rect
            vp_top = w_top + 80
            vp_bottom = w_bottom - 40

            # Why: 巨大要素(全画面コンテナ等)は中心座標による誤スクロールを抑止
            if rect.height() >= (vp_bottom - vp_top) * 0.7 or rect.width() >= (w_right - w_left) * 0.9:
                if x is not None and y is not None and w_left <= x <= w_right and vp_top <= y <= vp_bottom:
                    return (int(x), int(y))
                if vp_top <= cy <= vp_bottom and w_left <= cx <= w_right:
                    return (cx, cy)
                return (int(x), int(y)) if (x is not None and y is not None) else None

            if cy < vp_top or cy > vp_bottom or cx < w_left or cx > w_right:
                scroll_center_x = (w_left + w_right) // 2
                scroll_center_y = (w_top + w_bottom) // 2
                from core.executor.action_executor import MOUSEEVENTF_WHEEL, WHEEL_DELTA
                from core.executor.input_helper import smooth_move

                smooth_move(scroll_center_x, scroll_center_y, duration=0.05)
                time.sleep(0.04)

                diff_y = cy - (vp_top + vp_bottom) // 2
                dir_y = -1 if diff_y > 0 else 1
                notches = max(1, min(6, int(round(abs(diff_y) / 120))))
                wheel_val = int(dir_y * WHEEL_DELTA)

                for _ in range(notches):
                    ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_long(wheel_val).value, 0)
                    time.sleep(0.03)
                time.sleep(0.12)
                continue

        if cx > 20 and cy > 20 and rect.width() > 0 and rect.height() > 0:
            return (cx, cy)
        time.sleep(0.05)

    rect = elem.rectangle()
    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2
    if cx > 20 and cy > 20 and rect.width() > 0 and rect.height() > 0:
        return (cx, cy)
    return None
