"""Module: @role: マクロ実行時の各種個別アクション（クリック、移動、スクロール、テキスト入力、キー押下）の物理再現およびUI自己修復を担当する。"""

import ctypes
import logging
import platform
from typing import Any, Callable, Dict, Optional, Tuple

from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Controller as MouseController

from core.executor.input_helper import get_window_offset as _get_window_offset
from core.executor.physical_keyboard_actions import (
    execute_press_key_action,
    execute_type_text_action,
)
from core.executor.physical_mouse_actions import (
    MOUSEEVENTF_HWHEEL,
    MOUSEEVENTF_WHEEL,
    WHEEL_DELTA,
    execute_click_action,
    execute_drag_action,
    execute_move_action,
    execute_scroll_action,
)
from core.executor.target_healer import verify_and_heal_target
from core.executor.window_manager import activate_and_restore_window

logger = logging.getLogger(__name__)


def ensure_window_foreground(last_win_args: Optional[Dict[str, Any]], workflow_id: str) -> None:
    if not last_win_args or platform.system() != "Windows":
        return
    mapped_hwnd = last_win_args.get("mapped_hwnd")
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 3)
    needs_activation = False

    # Why: 子ウィンドウやタブ内部フォーカス時もルートハンドル一致で余計な再前面化を防止
    if mapped_hwnd:
        if hwnd != mapped_hwnd and root_hwnd != mapped_hwnd:
            needs_activation = True
    else:
        window_title = last_win_args.get("window_title", "")
        app_name = window_title.split("—")[-1].split("-")[-1].strip()
        if app_name:
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
            current_fg_title = buff.value
            if app_name.lower() not in current_fg_title.lower():
                needs_activation = True

    if needs_activation:
        logger.info(f"[{workflow_id}] Target window is not in foreground. Activating...")
        activate_and_restore_window(
            last_win_args.get("window_title", ""),
            last_win_args.get("x", 0),
            last_win_args.get("y", 0),
            last_win_args.get("width", 0),
            last_win_args.get("height", 0),
            workflow_id,
            last_win_args.get("launch_cmd", ""),
            mapped_hwnd,
            last_win_args.get("is_maximized"),
        )


def execute_physical_action(
    method: str,
    args: Dict[str, Any],
    last_win_args: Optional[Dict[str, Any]],
    current_win_rect: Tuple[int, int, int, int],
    is_browser_target: bool,
    excel_app_cache: Any,
    workflow_id: str,
    mouse: MouseController,
    keyboard: KeyboardController,
    check_stop_callback: Callable[[], None],
) -> Tuple[Any, bool]:
    ensure_window_foreground(last_win_args, workflow_id)

    current_win_x, current_win_y, _, _ = current_win_rect
    target_hwnd_for_offset = (last_win_args.get("mapped_hwnd") if last_win_args else None) or (
        ctypes.windll.user32.GetForegroundWindow() if platform.system() == "Windows" else None
    )
    off_x, off_y = _get_window_offset(target_hwnd_for_offset, current_win_x, current_win_y)

    if method == "click":
        excel_app_cache = execute_click_action(
            args, off_x, off_y, last_win_args, is_browser_target, excel_app_cache, workflow_id, mouse
        )
    elif method == "move":
        excel_app_cache = execute_move_action(
            args, off_x, off_y, last_win_args, is_browser_target, excel_app_cache, workflow_id, mouse
        )
    elif method == "drag":
        execute_drag_action(args, off_x, off_y, mouse)
    elif method == "scroll":
        should_skip = execute_scroll_action(
            args, off_x, off_y, current_win_rect, mouse, keyboard, check_stop_callback
        )
        if should_skip:
            return excel_app_cache, True
    elif method == "type_text":
        excel_app_cache = execute_type_text_action(
            args, off_x, off_y, last_win_args, is_browser_target, excel_app_cache, workflow_id, mouse, keyboard
        )
    elif method == "press_key":
        execute_press_key_action(args, keyboard)

    return excel_app_cache, False
