"""Module: @role: マクロ実行時の単一ステップ事前検証（画面照合・修復・ダイアログ解除）および各種アクションのディスパッチを担当する。"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import logging
import time

import numpy as np
from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Controller as MouseController

from core.executor.action_executor import execute_physical_action
from core.executor.excel_handler import execute_excel_action as _execute_excel_action, skip_failed_loop_step as _skip_failed_loop_step
from core.executor.input_helper import detect_unexpected_dialog, resolve_variables
from core.executor.loop_controller import LoopController
from core.executor.os_env_controller import set_system_cursor
from core.executor.step_runtime_helpers import (
    compute_dynamic_region_mask,
    ensure_browser_window_foreground,
    execute_wait_step,
    get_window_rect_by_hwnd,
)
from core.executor.step_verifier import (
    align_scroll_if_needed,
    detect_browser_target,
    heal_target_or_skip,
    perform_screen_matching,
    propagate_mapped_hwnd,
    scroll_browser_element_into_view,
)
from core.executor.window_manager import activate_and_restore_window

logger = logging.getLogger(__name__)


class StepDispatcher:
    """単一ステップの事前検証、ダイアログ監視、UI修復、および各種アクションの実行を統括するディスパッチャー"""

    def __init__(
        self,
        workflow_id: str,
        target_dir: Path,
        commands: List[Dict[str, Any]],
        mouse: MouseController,
        keyboard: KeyboardController,
        status_callback: Optional[Callable[[str, bool], None]] = None,
        check_stop_callback: Optional[Callable[[], None]] = None,
        is_stop_requested: Optional[Callable[[], bool]] = None,
        update_ui_callback: Optional[Callable[[str, bool], None]] = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.target_dir = target_dir
        self.commands = commands
        self.mouse = mouse
        self.keyboard = keyboard
        self.status_callback = status_callback
        self.check_stop_callback = check_stop_callback or (lambda: None)
        self.is_stop_requested = is_stop_requested or (lambda: False)
        self.update_ui_callback = update_ui_callback or (lambda msg, is_warn=False: None)

        self.last_win_args: Optional[Dict[str, Any]] = None
        self.current_win_rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self.excel_app_cache: Any = None
        self.macro_needs_save: bool = False
        self.is_browser_target: bool = False
        self.global_dynamic_mask: Optional[np.ndarray] = None

    def initialize_runtime_environment(self) -> None:
        """初回ウィンドウのアクティベートおよび動的領域差分マスクの生成を行う"""
        self.is_browser_target = detect_browser_target(self.commands)

        first_idx, first_activate_cmd = next(
            ((idx, cmd) for idx, cmd in enumerate(self.commands) if cmd.get("method") == "activate_window"),
            (-1, None),
        )
        if not first_activate_cmd:
            return

        args = first_activate_cmd.get("args", {})
        window_title = args.get("window_title", "")

        # Why: 先頭ウィンドウが過渡的な場合に備え、後続に別ウィンドウがあれば単独失敗を許容
        activated_hwnd = None
        try:
            activated_hwnd = activate_and_restore_window(
                window_title,
                args.get("x", 0),
                args.get("y", 0),
                args.get("width", 0),
                args.get("height", 0),
                self.workflow_id,
                args.get("launch_cmd", ""),
                args.get("mapped_hwnd"),
                args.get("is_maximized"),
            )
        except Exception as e:
            # Why: 初回activate_window自身の位置より後方のみを後続ウィンドウとして判定
            has_subsequent_activate = any(
                c.get("method") == "activate_window" for c in self.commands[first_idx + 1 :]
            )
            if has_subsequent_activate:
                logger.warning(f"[{self.workflow_id}] First window activation bypassed: {e}")
            else:
                raise

        if activated_hwnd:
            propagate_mapped_hwnd(self.commands, activated_hwnd, args)

        time.sleep(1.0)
        self.check_stop_callback()

        self.global_dynamic_mask = compute_dynamic_region_mask(
            self.workflow_id, self.check_stop_callback
        )
        self.last_win_args = args
        self.current_win_rect = (
            args.get("x", 0),
            args.get("y", 0),
            args.get("width", 0),
            args.get("height", 0)
        )

    def dispatch_step(
        self,
        i: int,
        cmd: Dict[str, Any],
        variables: Dict[str, Any],
        loop_stack: List[Dict[str, Any]],
        step_msg: str,
    ) -> Tuple[int, Optional[Dict[str, Any]], bool]:
        """単一ステップを検証・実行する。戻り値: (next_i, step_log, should_continue)"""
        method = cmd.get("method")
        raw_args = cmd.get("args", {}).copy()
        args = resolve_variables(raw_args, variables)
        LoopController.apply_sequence_variables(args, loop_stack)

        # Why: アクション種別を問わずステップ実行直前にモーダルダイアログを自動解除しスタック防止
        mapped_hwnd = self.last_win_args.get("mapped_hwnd") if self.last_win_args else None
        has_dlg, dlg_title = detect_unexpected_dialog(mapped_hwnd, auto_dismiss=True)
        if has_dlg:
            logger.warning(f"[{self.workflow_id}] Unexpected dialog detected and dismissed before step {i+1}: {dlg_title}")

        step_log: Dict[str, Any] = {
            "step_index": i,
            "method": method,
            "args": args,
            "match_info": None,
            "recovery_info": None,
            "timestamp": datetime.now().isoformat()
        }

        raw_event_id = args.get("raw_event_id")
        target_id = args.get("target_id")

        align_scroll_if_needed(
            self.commands,
            i,
            method or "",
            raw_event_id,
            self.is_browser_target,
            self.last_win_args,
            self.current_win_rect,
            self.target_dir,
            self.workflow_id,
            self.status_callback,
            self.is_stop_requested,
        )

        if method == "activate_window":
            self._update_window_rect_from_hwnd(args)

        scroll_browser_element_into_view(
            method or "",
            args,
            self.is_browser_target,
            self.last_win_args,
            self.current_win_rect,
            self.workflow_id,
        )

        if method in ["click", "type_text"] and raw_event_id:
            perform_screen_matching(
                i,
                args,
                raw_event_id,
                loop_stack,
                step_log,
                step_msg,
                self.target_dir,
                self.current_win_rect,
                self.workflow_id,
                self.status_callback,
                self.check_stop_callback,
                self.is_stop_requested,
                self.update_ui_callback,
            )

        self.check_stop_callback()

        if raw_event_id and target_id and method in ["click", "move"]:
            next_i, aborted, healed = heal_target_or_skip(
                i,
                self.commands,
                args,
                target_id,
                raw_event_id,
                step_log,
                loop_stack,
                self.current_win_rect,
                self.last_win_args,
                self.target_dir,
                self.workflow_id,
                self.status_callback,
                self.check_stop_callback,
                self.update_ui_callback,
            )
            if healed:
                self.macro_needs_save = True
            if aborted:
                return next_i, step_log, True

        next_i, should_skip = self._execute_method(i, cmd, method, args, raw_args, variables, loop_stack, step_log)
        return next_i, step_log, should_skip

    def _update_window_rect_from_hwnd(self, args: Dict[str, Any]) -> None:
        cur_x = args.get("x", 0)
        cur_y = args.get("y", 0)
        cur_w = args.get("width", 0)
        cur_h = args.get("height", 0)
        if cur_x == 0 and cur_y == 0 and self.last_win_args and self.last_win_args.get("mapped_hwnd"):
            rect = get_window_rect_by_hwnd(self.last_win_args["mapped_hwnd"])
            if rect:
                cur_x, cur_y, cur_w, cur_h = rect
                self.last_win_args["x"], self.last_win_args["y"] = cur_x, cur_y
                self.last_win_args["width"], self.last_win_args["height"] = cur_w, cur_h
        self.current_win_rect = (cur_x, cur_y, cur_w, cur_h)

    def _execute_method(
        self,
        i: int,
        cmd: Dict[str, Any],
        method: str,
        args: Dict[str, Any],
        raw_args: Dict[str, Any],
        variables: Dict[str, Any],
        loop_stack: List[Dict[str, Any]],
        step_log: Dict[str, Any],
    ) -> Tuple[int, bool]:
        if method == "wait":
            execute_wait_step(
                self.commands,
                i,
                args,
                self.target_dir,
                self.workflow_id,
                self.check_stop_callback,
            )
            return i + 1, False

        elif method == "activate_window":
            self.last_win_args = args
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
                window_title, win_x, win_y, win_w, win_h, self.workflow_id, launch_cmd, mapped_hwnd, args.get("is_maximized")
            )
            if act_hwnd:
                if not loop_launch:
                    args["mapped_hwnd"] = act_hwnd
                    cmd.setdefault("args", {})["mapped_hwnd"] = act_hwnd
                    if current_alias:
                        for future_cmd in self.commands[i+1:]:
                            if future_cmd.get("method") == "activate_window" and future_cmd.get("args", {}).get("window_alias") == current_alias:
                                future_cmd.setdefault("args", {})["mapped_hwnd"] = act_hwnd
                self.last_win_args["mapped_hwnd"] = act_hwnd
            set_system_cursor("run_idle")
            return i + 1, False

        elif method in ["click", "move", "scroll", "type_text", "press_key"]:
            self.excel_app_cache, should_skip = execute_physical_action(
                method=method,
                args=args,
                last_win_args=self.last_win_args,
                current_win_rect=self.current_win_rect,
                is_browser_target=self.is_browser_target,
                excel_app_cache=self.excel_app_cache,
                workflow_id=self.workflow_id,
                mouse=self.mouse,
                keyboard=self.keyboard,
                check_stop_callback=self.check_stop_callback,
            )
            return i + 1, should_skip

        elif method == "excel_action":
            try:
                self.excel_app_cache, excel_res = _execute_excel_action(
                    args, variables, self.excel_app_cache, self.last_win_args, self.workflow_id
                )
                step_log["excel_result"] = excel_res
            except Exception as e:
                logger.error(f"[{self.workflow_id}] Excel action execution failed: {e}")
                if loop_stack and loop_stack[-1].get("continue_on_error"):
                    skipped_i = _skip_failed_loop_step(i, self.commands, loop_stack, self.workflow_id, self.update_ui_callback, str(e))
                    return skipped_i, True
                raise
            return i + 1, False

        elif method == "browser_action":
            try:
                ensure_browser_window_foreground(self.last_win_args, self.workflow_id)
                from core.executor.browser_controller import BrowserController
                controller = BrowserController.get_instance()
                browser_res = controller.execute_action(
                    args, variables, self.last_win_args, self.workflow_id
                )
                step_log["browser_result"] = browser_res
            except Exception as e:
                logger.error(f"[{self.workflow_id}] Browser action execution failed: {e}")
                if loop_stack and loop_stack[-1].get("continue_on_error"):
                    skipped_i = _skip_failed_loop_step(i, self.commands, loop_stack, self.workflow_id, self.update_ui_callback, str(e))
                    return skipped_i, True
                raise
            return i + 1, False

        else:
            logger.error(f"[{self.workflow_id}] Unsupported execution command method: '{method}' at step {i+1}")
            raise ValueError(f"Unsupported execution command method: '{method}' at step {i+1}")
