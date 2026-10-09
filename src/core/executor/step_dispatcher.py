"""Module: @role: マクロ実行時の単一ステップ事前検証（画面照合・修復・ダイアログ解除）および各種アクションのディスパッチを担当する。"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import ctypes
import logging
import platform
import time

import cv2
import numpy as np
from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Controller as MouseController

from core.executor.action_executor import execute_physical_action, verify_and_heal_target
from core.executor.excel_handler import execute_excel_action as _execute_excel_action, skip_failed_loop_step as _skip_failed_loop_step
from core.executor.input_helper import (
    detect_unexpected_dialog,
    get_window_offset,
    resolve_variables,
    wait_for_screen_settle,
)
from core.executor.loop_controller import LoopController
from core.executor.os_env_controller import set_system_cursor
from core.executor.screen_matcher import align_scroll_by_screen_match, wait_for_screen_match
from core.executor.window_manager import activate_and_restore_window
from core.recorder.screen_capturer import take_screenshot

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
        # Why: 全体コマンドから主要ブラウザ操作の有無を包括判定しブラウザモードを確立
        self.is_browser_target = any(
            any(b in str(c.get("args", {}).get("window_title", "")).lower() for b in ["firefox", "chrome", "edge", "brave", "opera"])
            for c in self.commands if c.get("method") == "activate_window"
        ) or any(c.get("method") == "browser_action" for c in self.commands)

        first_activate_cmd = next((cmd for cmd in self.commands if cmd.get("method") == "activate_window"), None)
        if not first_activate_cmd:
            return

        args = first_activate_cmd.get("args", {})
        window_title = args.get("window_title", "")
        app_name = window_title.split("—")[-1].split("-")[-1].strip().lower()

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
                args.get("is_maximized")
            )
        except Exception as e:
            has_subsequent_activate = any(c.get("method") == "activate_window" for c in self.commands[1:])
            if has_subsequent_activate:
                logger.warning(f"[{self.workflow_id}] First window activation bypassed: {e}")
            else:
                raise

        if activated_hwnd:
            first_alias = args.get("window_alias")
            # Why: ループ毎新規起動指定時は初回HWNDでコマンド引数を固定せず都度起動を保証
            if not args.get("loop_launch_each_time"):
                args["mapped_hwnd"] = activated_hwnd
            for cmd in self.commands:
                if cmd.get("method") == "activate_window":
                    cmd_args = cmd.setdefault("args", {})
                    cmd_app = cmd_args.get("window_title", "").split("—")[-1].split("-")[-1].strip().lower()
                    if cmd_app == app_name or (first_alias and cmd_args.get("window_alias") == first_alias):
                        if not cmd_args.get("loop_launch_each_time"):
                            cmd_args["mapped_hwnd"] = activated_hwnd

        time.sleep(1.0)
        self.check_stop_callback()

        logger.info(f"[{self.workflow_id}] Buffering initial frames to detect dynamic regions (e.g., videos)...")
        initial_frames = []
        for _ in range(5):
            self.check_stop_callback()
            img_pil, _ = take_screenshot()
            img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2GRAY)
            initial_frames.append(img_cv)
            time.sleep(0.1)

        std_dev_global = np.std(initial_frames, axis=0)
        mask = (std_dev_global > 12).astype(np.uint8) * 255

        edges_list = [cv2.Canny(f, 50, 150) for f in initial_frames]
        static_edges = edges_list[0]
        for e in edges_list[1:]:
            static_edges = cv2.bitwise_and(static_edges, e)

        kernel_protect = np.ones((3, 3), np.uint8)
        static_edges_dilated = cv2.dilate(static_edges, kernel_protect, iterations=1)
        mask[static_edges_dilated == 255] = 0

        # Why: 大画面動画が流れるトップページでも静的枠組みを保持して照合可能に
        if np.count_nonzero(mask) / mask.size > 0.85:
            mask = (std_dev_global > 25).astype(np.uint8) * 255
            mask[static_edges_dilated == 255] = 0

        self.global_dynamic_mask = mask
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

        self._align_scroll_if_needed(i, method, raw_event_id)

        if method == "activate_window":
            self._update_window_rect_from_hwnd(args)

        self._scroll_browser_element_into_view(method, args)

        if method in ["click", "type_text"] and raw_event_id:
            self._perform_screen_matching(i, args, raw_event_id, loop_stack, step_log, step_msg)

        self.check_stop_callback()

        if raw_event_id and target_id and method in ["click", "move"]:
            next_i, aborted = self._heal_target_or_skip(i, args, target_id, raw_event_id, step_log, loop_stack)
            if aborted:
                return next_i, step_log, True

        next_i, should_skip = self._execute_method(i, cmd, method, args, raw_args, variables, loop_stack, step_log)
        return next_i, step_log, should_skip

    def _align_scroll_if_needed(self, i: int, method: str, raw_event_id: Optional[str]) -> None:
        has_scroll_in_macro = any(c.get("method") == "scroll" for c in self.commands)
        has_prior_scroll = any(self.commands[k].get("method") == "scroll" for k in range(max(0, i - 3), i))
        if raw_event_id and method in ["click", "move", "type_text", "browser_action"] and self.is_browser_target and has_scroll_in_macro and has_prior_scroll:
            target_hwnd = (self.last_win_args.get("mapped_hwnd") if self.last_win_args else None) or 0
            cur_x, cur_y, cur_w, cur_h = self.current_win_rect
            scrolled_ok, match_sc = align_scroll_by_screen_match(
                target_dir=self.target_dir,
                raw_event_id=raw_event_id,
                win_x=cur_x,
                win_y=cur_y,
                win_w=cur_w,
                win_h=cur_h,
                target_hwnd=target_hwnd,
                status_callback=self.status_callback,
                check_cancel_callback=self.is_stop_requested,
            )
            if scrolled_ok:
                logger.info(f"[{self.workflow_id}] Visual scroll aligned with step {i+1} pre-image: {match_sc}")

    def _update_window_rect_from_hwnd(self, args: Dict[str, Any]) -> None:
        cur_x = args.get("x", 0)
        cur_y = args.get("y", 0)
        cur_w = args.get("width", 0)
        cur_h = args.get("height", 0)
        if cur_x == 0 and cur_y == 0 and self.last_win_args and self.last_win_args.get("mapped_hwnd"):
            try:
                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                r = RECT()
                if ctypes.windll.user32.GetWindowRect(self.last_win_args["mapped_hwnd"], ctypes.byref(r)):
                    cur_x, cur_y = r.left, r.top
                    cur_w, cur_h = r.right - r.left, r.bottom - r.top
                    self.last_win_args["x"], self.last_win_args["y"] = cur_x, cur_y
                    self.last_win_args["width"], self.last_win_args["height"] = cur_w, cur_h
            except Exception:
                pass
        self.current_win_rect = (cur_x, cur_y, cur_w, cur_h)

    def _scroll_browser_element_into_view(self, method: str, args: Dict[str, Any]) -> None:
        cur_sel = str(args.get("selector") or "").strip().lower()
        is_generic = cur_sel in ["div", "span", "p", "a", "li", "ul", "body"]
        is_coords_visible = args.get("x") is not None and args.get("y") is not None and args.get("x") > 20 and args.get("y") > 45
        if self.is_browser_target and method in ["click", "type_text", "move"] and (args.get("selector") or args.get("element_name") or args.get("url")) and not is_generic and not is_coords_visible:
            try:
                from core.executor.browser_controller import BrowserController
                bc = BrowserController.get_instance()
                target_hwnd_for_offset = (self.last_win_args.get("mapped_hwnd") if self.last_win_args else None) or (ctypes.windll.user32.GetForegroundWindow() if platform.system() == "Windows" else None)
                cur_x, cur_y, _, _ = self.current_win_rect
                pre_off_x, pre_off_y = get_window_offset(target_hwnd_for_offset, cur_x, cur_y)
                new_coords = bc.scroll_into_view(
                    selector=args.get("selector"),
                    last_win_args=self.last_win_args,
                    timeout_sec=0.8,
                    element_name=args.get("element_name"),
                    url=args.get("url"),
                    text=args.get("text"),
                    x=args.get("x"),
                    y=args.get("y")
                )
                if new_coords:
                    args["x"] = new_coords[0] - pre_off_x
                    args["y"] = new_coords[1] - pre_off_y
                    logger.info(f"[{self.workflow_id}] Element scrolled into view. Updated coords to ({new_coords[0]}, {new_coords[1]})")
            except Exception as sc_err:
                logger.debug(f"[{self.workflow_id}] Auto scroll_into_view bypassed: {sc_err}")

    def _perform_screen_matching(
        self,
        i: int,
        args: Dict[str, Any],
        raw_event_id: str,
        loop_stack: List[Dict[str, Any]],
        step_log: Dict[str, Any],
        step_msg: str
    ) -> None:
        if loop_stack:
            wait_for_screen_settle(timeout=1.5, settle_threshold=0.004)
        else:
            match_eid = args.get("match_event_id") or raw_event_id
            cur_x, cur_y, cur_w, cur_h = self.current_win_rect
            match_info = wait_for_screen_match(
                self.target_dir, match_eid, cur_x, cur_y, cur_w, cur_h,
                self.workflow_id, self.status_callback, i, timeout=1.5,
                check_cancel_callback=self.is_stop_requested
            )
            self.check_stop_callback()
            step_log["match_info"] = match_info
            self.update_ui_callback(step_msg, False)

    def _heal_target_or_skip(
        self,
        i: int,
        args: Dict[str, Any],
        target_id: str,
        raw_event_id: str,
        step_log: Dict[str, Any],
        loop_stack: List[Dict[str, Any]]
    ) -> Tuple[int, bool]:
        recovered_ok, recovery_info = verify_and_heal_target(
            workflow_id=self.workflow_id,
            target_id=target_id,
            raw_event_id=raw_event_id,
            args=args,
            target_dir=self.target_dir,
            last_win_args=self.last_win_args,
            status_callback=self.status_callback,
            check_stop_callback=self.check_stop_callback,
        )
        if recovery_info:
            step_log["recovery_info"] = recovery_info
            if recovered_ok:
                self.macro_needs_save = True
            else:
                if loop_stack and loop_stack[-1].get("continue_on_error"):
                    skipped_i = _skip_failed_loop_step(i, self.commands, loop_stack, self.workflow_id, self.update_ui_callback, "UI検出失敗")
                    return skipped_i, True
                raise RuntimeError("対象のUIが見つからず、自己修復にも失敗したためマクロを安全停止しました。")
        return i + 1, False

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
            prev_method = self.commands[i - 1].get("method") if i > 0 else ""
            next_has_pre_img = False
            for j in range(i + 1, len(self.commands)):
                if self.commands[j].get("method") not in ["wait", "activate_window", "loop_start", "loop_end"]:
                    next_eid = self.commands[j].get("args", {}).get("raw_event_id")
                    if next_eid and (self.target_dir / "images" / f"{next_eid}_pre.png").exists():
                        next_has_pre_img = True
                    break

            # Why: スクロール直後は描画・アニメーション完了が必須のため固定待機を維持
            if next_has_pre_img and prev_method not in ["move", "click", "scroll"]:
                logger.info(f"[{self.workflow_id}] Skipping fixed wait in favor of screen matching for the next action.")
                return i + 1, False

            duration = float(args.get("duration", 0.0))
            # Why: 0.6秒以上の待機時は画面描画収束を検知して無駄な待機時間を適応短縮
            if duration >= 0.6 and prev_method in ["click", "type_text", "press_key", "browser_action"]:
                settled = wait_for_screen_settle(timeout=min(duration, 3.0), settle_threshold=0.003)
                if settled:
                    logger.info(f"[{self.workflow_id}] Smart adaptive wait: Screen settled early, proceeding.")
                    return i + 1, False

            sleep_intervals = int(duration * 10)
            for _ in range(sleep_intervals):
                self.check_stop_callback()
                time.sleep(0.1)
            remainder = duration - (sleep_intervals * 0.1)
            if remainder > 0:
                self.check_stop_callback()
                time.sleep(remainder)
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
                if self.last_win_args and platform.system() == "Windows":
                    mapped_hwnd = self.last_win_args.get("mapped_hwnd")
                    hwnd = ctypes.windll.user32.GetForegroundWindow()
                    root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 3)
                    needs_act = False
                    if mapped_hwnd and hwnd != mapped_hwnd and root_hwnd != mapped_hwnd:
                        needs_act = True
                    if needs_act:
                        activate_and_restore_window(
                            self.last_win_args.get("window_title", ""),
                            self.last_win_args.get("x", 0),
                            self.last_win_args.get("y", 0),
                            self.last_win_args.get("width", 0),
                            self.last_win_args.get("height", 0),
                            self.workflow_id,
                            self.last_win_args.get("launch_cmd", ""),
                            mapped_hwnd,
                            self.last_win_args.get("is_maximized")
                        )
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
            logger.warning(f"Unknown method: {method}")
            return i + 1, False
