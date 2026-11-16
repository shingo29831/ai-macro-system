"""Module: @role: マクロ実行時の単一ステップ事前検証（画面照合・修復・ダイアログ解除）およびアクションディスパッチのオーケストレーションを担当する。"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import logging
import time

import numpy as np
from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Controller as MouseController

from core.executor.action_executor import execute_step_action
from core.executor.excel_handler import skip_failed_loop_step as _skip_failed_loop_step
from core.executor.input_helper import detect_unexpected_dialog, resolve_variables
from core.executor.loop_controller import LoopController
from core.executor.step_runtime_helpers import compute_dynamic_region_mask
from core.executor.step_verifier import (
    align_scroll_if_needed,
    detect_browser_target,
    heal_target_or_skip,
    perform_screen_matching,
    propagate_mapped_hwnd,
    scroll_browser_element_into_view,
)
from core.executor.window_manager import activate_and_restore_window
from core.shared.window_utils import get_window_rect_xywh

logger = logging.getLogger(__name__)


def _is_variable_placeholder(value: Any) -> bool:
    return isinstance(value, str) and ("{{" in value or "${" in value)


class StepDispatcher:
    """単一ステップの事前検証、ダイアログ監視、UI修復、およびアクション実行委譲を統括するディスパッチャー"""

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
            args.get("height", 0),
        )

    def dispatch_step(
        self,
        i: int,
        cmd: Dict[str, Any],
        variables: Dict[str, Any],
        loop_stack: List[Dict[str, Any]],
        step_msg: str,
    ) -> Tuple[int, Optional[Dict[str, Any]], bool]:
        """単一ステップを検証・実行する。戻り値: (next_i, step_log, should_skip)"""
        method = cmd.get("method")
        raw_args = cmd.get("args", {}).copy()
        args = resolve_variables(raw_args, variables)

        pre_seq_x = args.get("x")
        pre_seq_y = args.get("y")
        LoopController.apply_sequence_variables(args, loop_stack)
        seq_dx = (args.get("x", 0) - pre_seq_x) if isinstance(pre_seq_x, (int, float)) and isinstance(args.get("x"), (int, float)) else 0
        seq_dy = (args.get("y", 0) - pre_seq_y) if isinstance(pre_seq_y, (int, float)) and isinstance(args.get("y"), (int, float)) else 0

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
            "timestamp": datetime.now().isoformat(),
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
                # Why: コピー辞書への修復結果を元コマンドへ書き戻し永続化と次回実行を一致させる
                cmd_args = cmd.setdefault("args", {})
                if "x" in args and not _is_variable_placeholder(cmd_args.get("x")):
                    cmd_args["x"] = int(args["x"] - seq_dx)
                if "y" in args and not _is_variable_placeholder(cmd_args.get("y")):
                    cmd_args["y"] = int(args["y"] - seq_dy)
                self.macro_needs_save = True
            if aborted:
                return next_i, step_log, True

        try:
            next_i, self.last_win_args, self.current_win_rect, self.excel_app_cache, action_result, should_skip = execute_step_action(
                method=method or "",
                cmd=cmd,
                args=args,
                raw_args=raw_args,
                commands=self.commands,
                step_index=i,
                variables=variables,
                loop_stack=loop_stack,
                last_win_args=self.last_win_args,
                current_win_rect=self.current_win_rect,
                is_browser_target=self.is_browser_target,
                excel_app_cache=self.excel_app_cache,
                target_dir=self.target_dir,
                workflow_id=self.workflow_id,
                mouse=self.mouse,
                keyboard=self.keyboard,
                check_stop_callback=self.check_stop_callback,
            )
            if method == "excel_action" and action_result is not None:
                step_log["excel_result"] = action_result
            elif method == "browser_action" and action_result is not None:
                step_log["browser_result"] = action_result
            return next_i, step_log, should_skip
        except Exception as e:
            if method in ("excel_action", "browser_action") and loop_stack and loop_stack[-1].get("continue_on_error"):
                logger.error(f"[{self.workflow_id}] {method} failed, skipping iteration: {e}")
                skipped_i = _skip_failed_loop_step(
                    i, self.commands, loop_stack, self.workflow_id, self.update_ui_callback, str(e)
                )
                return skipped_i, step_log, True
            raise

    def _update_window_rect_from_hwnd(self, args: Dict[str, Any]) -> None:
        cur_x = args.get("x", 0)
        cur_y = args.get("y", 0)
        cur_w = args.get("width", 0)
        cur_h = args.get("height", 0)
        if cur_x == 0 and cur_y == 0 and self.last_win_args and self.last_win_args.get("mapped_hwnd"):
            rect = get_window_rect_xywh(self.last_win_args["mapped_hwnd"])
            if rect:
                cur_x, cur_y, cur_w, cur_h = rect
                self.last_win_args["x"], self.last_win_args["y"] = cur_x, cur_y
                self.last_win_args["width"], self.last_win_args["height"] = cur_w, cur_h
        self.current_win_rect = (cur_x, cur_y, cur_w, cur_h)
