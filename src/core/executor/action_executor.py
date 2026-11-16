"""Module: @role: マクロ実行時の全個別アクション（ウィンドウ制御・待機・物理操作・Excel・ブラウザ）の実行を担当する。"""

import ctypes
import logging
import platform
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Controller as MouseController

from core.executor.excel_handler import execute_excel_action as _execute_excel_action
from core.executor.input_helper import get_window_offset as _get_window_offset
from core.executor.physical_keyboard_actions import (
    execute_press_key_action,
    execute_type_text_action,
)
from core.executor.physical_mouse_actions import (
    execute_click_action,
    execute_drag_action,
    execute_move_action,
    execute_scroll_action,
)
from core.executor.step_runtime_helpers import ensure_browser_window_foreground, execute_wait_step
from core.executor.window_manager import activate_and_restore_window
from core.shared.os_env import set_system_cursor
from core.shared.window_utils import extract_app_name

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
        app_name = extract_app_name(last_win_args.get("window_title", ""))
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


def execute_step_action(
    method: str,
    cmd: Dict[str, Any],
    args: Dict[str, Any],
    raw_args: Dict[str, Any],
    commands: List[Dict[str, Any]],
    step_index: int,
    variables: Dict[str, Any],
    loop_stack: List[Dict[str, Any]],
    last_win_args: Optional[Dict[str, Any]],
    current_win_rect: Tuple[int, int, int, int],
    is_browser_target: bool,
    excel_app_cache: Any,
    target_dir: Path,
    workflow_id: str,
    mouse: MouseController,
    keyboard: KeyboardController,
    check_stop_callback: Callable[[], None],
) -> Tuple[int, Optional[Dict[str, Any]], Tuple[int, int, int, int], Any, Any, bool]:
    """検証済み単一ステップをメソッド種別に応じて実行し、更新後ランタイム状態を返す。"""
    if method == "wait":
        execute_wait_step(commands, step_index, args, target_dir, workflow_id, check_stop_callback)
        return step_index + 1, last_win_args, current_win_rect, excel_app_cache, None, False

    if method == "activate_window":
        new_last_win_args = args
        window_title = args.get("window_title", "")
        win_x = args.get("x", 0)
        win_y = args.get("y", 0)
        win_w = args.get("width", 0)
        win_h = args.get("height", 0)
        launch_cmd = args.get("launch_cmd", "")
        loop_launch = raw_args.get("loop_launch_each_time", False) or args.get("loop_launch_each_time", False)

        # Why: ループ毎新規起動が有効な場合は各周回でmapped_hwnd=-1として新規起動を実行
        mapped_hwnd = -1 if (loop_stack and loop_launch) else args.get("mapped_hwnd")
        current_alias = args.get("window_alias")

        act_hwnd = activate_and_restore_window(
            window_title, win_x, win_y, win_w, win_h, workflow_id, launch_cmd, mapped_hwnd, args.get("is_maximized")
        )
        if act_hwnd:
            if not loop_launch:
                args["mapped_hwnd"] = act_hwnd
                cmd.setdefault("args", {})["mapped_hwnd"] = act_hwnd
                if current_alias:
                    for future_cmd in commands[step_index + 1:]:
                        if (
                            future_cmd.get("method") == "activate_window"
                            and future_cmd.get("args", {}).get("window_alias") == current_alias
                        ):
                            future_cmd.setdefault("args", {})["mapped_hwnd"] = act_hwnd
            new_last_win_args["mapped_hwnd"] = act_hwnd
        set_system_cursor("run_idle")
        return step_index + 1, new_last_win_args, current_win_rect, excel_app_cache, None, False

    if method in ["click", "move", "drag", "scroll", "type_text", "press_key"]:
        new_excel_cache, should_skip = execute_physical_action(
            method=method,
            args=args,
            last_win_args=last_win_args,
            current_win_rect=current_win_rect,
            is_browser_target=is_browser_target,
            excel_app_cache=excel_app_cache,
            workflow_id=workflow_id,
            mouse=mouse,
            keyboard=keyboard,
            check_stop_callback=check_stop_callback,
        )
        return step_index + 1, last_win_args, current_win_rect, new_excel_cache, None, should_skip

    if method == "excel_action":
        new_excel_cache, excel_res = _execute_excel_action(
            args, variables, excel_app_cache, last_win_args, workflow_id
        )
        return step_index + 1, last_win_args, current_win_rect, new_excel_cache, excel_res, False

    if method == "browser_action":
        ensure_browser_window_foreground(last_win_args, workflow_id)
        from core.executor.browser_controller import BrowserController

        controller = BrowserController.get_instance()
        browser_res = controller.execute_action(args, variables, last_win_args, workflow_id)
        return step_index + 1, last_win_args, current_win_rect, excel_app_cache, browser_res, False

    logger.error(f"[{workflow_id}] Unsupported execution command method: '{method}' at step {step_index+1}")
    raise ValueError(f"Unsupported execution command method: '{method}' at step {step_index+1}")
