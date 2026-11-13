"""Module: @role: クリック・移動・ドラッグ・スクロールの物理マウスアクション実行とブラウザ/Excel向け最適化を担当する。"""

import ctypes
import logging
import platform
import time
from typing import Any, Callable, Dict, Optional, Tuple

from pynput.keyboard import Controller as KeyboardController, Key
from pynput.mouse import Button, Controller as MouseController

from core.executor.excel_handler import get_excel_application as _get_excel_application
from core.executor.input_helper import (
    smooth_move as _smooth_move,
    wait_for_screen_settle as _wait_for_screen_settle,
)

logger = logging.getLogger(__name__)

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120


def _try_select_excel_dest_cell(
    excel_dest_cell: Optional[str],
    last_win_args: Optional[Dict[str, Any]],
    excel_app_cache: Any,
    workflow_id: str,
) -> Tuple[Any, bool]:
    if not (excel_dest_cell and platform.system() == "Windows"):
        return excel_app_cache, False
    try:
        if excel_app_cache is None:
            target_hwnd = (
                last_win_args.get("mapped_hwnd") if last_win_args else None
            ) or ctypes.windll.user32.GetForegroundWindow()
            excel_app_cache = _get_excel_application(target_hwnd)
        if excel_app_cache:
            sheet = excel_app_cache.ActiveSheet
            sheet.Range(excel_dest_cell).Select()
            time.sleep(0.05)
            return excel_app_cache, True
    except Exception as e:
        logger.warning(f"[{workflow_id}] Failed to select Excel dest cell {excel_dest_cell}: {e}")
        excel_app_cache = None
    return excel_app_cache, False


def execute_click_action(
    args: Dict[str, Any],
    off_x: int,
    off_y: int,
    last_win_args: Optional[Dict[str, Any]],
    is_browser_target: bool,
    excel_app_cache: Any,
    workflow_id: str,
    mouse: MouseController,
) -> Any:
    """単一・連続クリックを実行する（Excelセル選択およびブラウザ高精度クリックを優先）。"""
    x = args.get("x", 0) + off_x
    y = args.get("y", 0) + off_y
    button_str = args.get("button", "left")
    clicks = args.get("clicks", 1)
    selector = args.get("selector")
    elem_name = args.get("element_name")
    target_url = args.get("url")

    excel_app_cache, skip_physical = _try_select_excel_dest_cell(
        args.get("excel_dest_cell"), last_win_args, excel_app_cache, workflow_id
    )

    # Why: "div"等の汎用タグ単体セレクタは誤爆防止のため物理座標クリックへ委譲
    is_generic_tag = selector and selector.strip().lower() in ["div", "span", "p", "a", "li", "ul", "body"]
    # Why: ブラウザ専用セレクタ/UIA要素が存在する場合は高精度クリックを最優先実行
    if not skip_physical and (selector or elem_name or target_url) and is_browser_target and not is_generic_tag:
        try:
            from core.executor.browser_controller import BrowserController

            bc = BrowserController.get_instance()
            if bc.click_by_uia_or_selector(
                selector, last_win_args, timeout=0.6, element_name=elem_name, url=target_url, x=x, y=y
            ):
                logger.info(f"[{workflow_id}] High-precision browser click succeeded on '{elem_name or selector}'.")
                skip_physical = True
        except Exception as b_err:
            logger.info(f"[{workflow_id}] High-precision browser click bypassed: {b_err}")

    if not skip_physical:
        btn = Button.right if button_str == "right" else Button.middle if button_str == "middle" else Button.left
        _smooth_move(int(x), int(y))
        time.sleep(0.06)
        mouse.click(btn, clicks)
        time.sleep(0.08)

    return excel_app_cache


def execute_move_action(
    args: Dict[str, Any],
    off_x: int,
    off_y: int,
    last_win_args: Optional[Dict[str, Any]],
    is_browser_target: bool,
    excel_app_cache: Any,
    workflow_id: str,
    mouse: MouseController,
) -> Any:
    """マウスカーソル移動およびホバー展開を実行する。"""
    x = args.get("x", 0) + off_x
    y = args.get("y", 0) + off_y
    selector = args.get("selector")
    elem_name = args.get("element_name")

    excel_app_cache, skip_physical = _try_select_excel_dest_cell(
        args.get("excel_dest_cell"), last_win_args, excel_app_cache, workflow_id
    )

    if not skip_physical:
        hovered = False
        # Why: ブラウザ操作時はセレクタ/UIA要素への高精度ホバー吸着を実行しドロップダウンメニュー展開を完全保証
        if is_browser_target and (selector or elem_name):
            try:
                from core.executor.browser_controller import BrowserController

                bc = BrowserController.get_instance()
                if bc.hover_by_uia_or_selector(
                    selector, last_win_args, timeout=0.8, element_name=elem_name, target_x=x, target_y=y
                ):
                    logger.info(
                        f"[{workflow_id}] High-precision browser hover succeeded on '{elem_name or selector}'."
                    )
                    hovered = True
            except Exception as b_err:
                logger.debug(f"[{workflow_id}] Browser hover bypassed: {b_err}")

        if not hovered:
            _smooth_move(int(x), int(y), duration=0.15)
            # Why: ブラウザのレンダラプロセスにWM_MOUSEMOVEを確実に受領させホバーメニューを展開
            if platform.system() == "Windows":
                mouse.position = (int(x), int(y))
                ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
                ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
            time.sleep(0.55)

    return excel_app_cache


def execute_drag_action(
    args: Dict[str, Any],
    off_x: int,
    off_y: int,
    mouse: MouseController,
) -> None:
    """ドラッグ＆ドロップを実行する。"""
    sx = args.get("start_x", args.get("x", 0)) + off_x
    sy = args.get("start_y", args.get("y", 0)) + off_y
    ex = args.get("end_x", sx) + off_x
    ey = args.get("end_y", sy) + off_y
    button_str = args.get("button", "left")
    btn = Button.right if button_str == "right" else Button.middle if button_str == "middle" else Button.left

    _smooth_move(int(sx), int(sy))
    time.sleep(0.06)
    mouse.press(btn)
    time.sleep(0.06)
    _smooth_move(int(ex), int(ey), steps=15, duration=0.25)
    time.sleep(0.06)
    mouse.release(btn)
    time.sleep(0.08)


def execute_scroll_action(
    args: Dict[str, Any],
    off_x: int,
    off_y: int,
    current_win_rect: Tuple[int, int, int, int],
    mouse: MouseController,
    keyboard: KeyboardController,
    check_stop_callback: Callable[[], None],
) -> bool:
    """OS設定を考慮した垂直/水平ホイールスクロールを実行する。スキップ時はTrueを返す。"""
    current_win_x, current_win_y, current_win_w, current_win_h = current_win_rect
    try:
        dx = float(args.get("dx") or 0.0)
        dy = float(args.get("dy") or 0.0)
    except (ValueError, TypeError):
        dx, dy = 0.0, 0.0
    x = args.get("x")
    y = args.get("y")

    if dx == 0.0 and dy == 0.0:
        return True

    # Why: 座標未指定・枠外時にウィンドウクライアント領域中央へ安全移動
    target_sx = (
        int(x + off_x) if (x is not None and x > 20) else (current_win_x + current_win_w // 2 if current_win_w > 100 else 960)
    )
    target_sy = (
        int(y + off_y) if (y is not None and y > 20) else (current_win_y + current_win_h // 2 if current_win_h > 100 else 540)
    )

    # Why: スクロール受領対象へ確実にカーソルを移動させホバー状態を確立
    _smooth_move(target_sx, target_sy, duration=0.08)
    time.sleep(0.05)

    if platform.system() == "Windows":
        from core.executor.os_env_controller import get_wheel_scroll_settings

        settings = get_wheel_scroll_settings()
        is_page = settings.get("is_page_scroll", False)

        if is_page and dy != 0.0:
            page_key = Key.page_down if dy < 0 else Key.page_up
            keyboard.press(page_key)
            keyboard.release(page_key)
            time.sleep(0.15)
        else:
            # Why: 環境設定に応じたスクロール係数を反映し物理ホイールを正確に送出
            lines_per_notch = settings.get("wheel_scroll_lines", 3)
            scroll_multiplier = max(1, min(5, lines_per_notch // 3)) if lines_per_notch > 0 else 1
            total_notches = max(1, int(round(abs(dy) * scroll_multiplier)))
            dir_y = 1 if dy > 0 else -1
            wheel_val = int(dir_y * WHEEL_DELTA)
            for _ in range(total_notches):
                check_stop_callback()
                ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_long(wheel_val).value, 0)
                time.sleep(0.04)

            if dx != 0.0:
                total_notches_x = max(1, int(round(abs(dx))))
                dir_x = 1 if dx > 0 else -1
                hwheel_val = int(dir_x * WHEEL_DELTA)
                for _ in range(total_notches_x):
                    check_stop_callback()
                    ctypes.windll.user32.mouse_event(MOUSEEVENTF_HWHEEL, 0, 0, ctypes.c_long(hwheel_val).value, 0)
                    time.sleep(0.04)
        # Why: スクロール直後のレイアウト再計算と描画収束を待機
        time.sleep(0.2)
        _wait_for_screen_settle(timeout=0.6, settle_threshold=0.003)
    else:
        steps = max(1, int(round(abs(dy))))
        dir_y = 1 if dy > 0 else -1
        for _ in range(steps):
            mouse.scroll(0, dir_y)
            time.sleep(0.04)
        time.sleep(0.2)

    return False
