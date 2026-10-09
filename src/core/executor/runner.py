# Role: 生成されたExecutable Macro (executable_macro.json) を読み込み、ローカルで自律実行する実行エンジン。

import json
import logging
import time
import platform
import ctypes
import subprocess
import threading
import sys
import traceback
from pathlib import Path
import numpy as np
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key, Listener as KeyboardListener

from models.data_types import AppConfig
from core.executor.window_manager import set_dpi_awareness, set_ime_state, activate_and_restore_window, reset_browser_activation_flag
from core.executor.os_env_controller import set_system_cursor, restore_system_cursor
from core.executor.screen_matcher import wait_for_screen_match, is_screen_match
from core.executor.watchdog import (
    WorkflowStoppedException,
    WorkflowFreezeException,
    ExecutionWatchdog,
    get_thread_stack_trace,
    inject_async_exception as _inject_async_exception,
)
from core.executor.input_helper import (
    send_unicode_string as _send_unicode_string,
    set_clipboard_text as _set_clipboard_text,
    resolve_variables as _resolve_variables,
    smooth_move as _smooth_move,
    get_window_offset as _get_window_offset,
    wait_for_screen_settle as _wait_for_screen_settle,
    detect_unexpected_dialog as _detect_unexpected_dialog,
)
from core.executor.excel_handler import (
    col_idx_to_letter as _col_idx_to_letter,
    extract_referenced_columns as _extract_referenced_columns,
    read_excel_records as _read_excel_records,
    is_file_locked as _is_file_locked,
    write_excel_status as _write_excel_status,
    skip_failed_loop_step as _skip_failed_loop_step,
    get_excel_application as _get_excel_application,
    execute_excel_action as _execute_excel_action,
)
from core.executor.action_executor import (
    execute_physical_action,
    verify_and_heal_target,
    MOUSEEVENTF_WHEEL,
    MOUSEEVENTF_HWHEEL,
    WHEEL_DELTA,
)

logger = logging.getLogger(__name__)


_is_running = False
_stop_requested = False

set_dpi_awareness()

def run_workflow(workflow_id: str, config: AppConfig, status_callback=None, temp_commands: list[dict] = None, on_freeze_callback=None):
    global _is_running, _stop_requested
    _is_running = True
    _stop_requested = False
    reset_browser_activation_flag()
    set_system_cursor("run_idle")
    
    logger.info(f"[{workflow_id}] Starting executable macro execution...")
    exec_thread_id = threading.get_ident()

    def on_freeze(step_info: str, elapsed: float, stack_trace: str):
        stop_workflow()
        restore_system_cursor(force=True)
        update_ui(f"フリーズ検知により強制終了 ({step_info})", is_warning=True)
        if on_freeze_callback:
            try:
                on_freeze_callback(step_info, elapsed, stack_trace)
            except Exception as e:
                logger.error(f"Failed to propagate on_freeze_callback: {e}")

    watchdog = ExecutionWatchdog(exec_thread_id, timeout_sec=30.0, on_freeze_callback=on_freeze)
    watchdog.start()

    mouse = MouseController()
    keyboard = KeyboardController()

    def _check_stop():
        if _stop_requested:
            raise WorkflowStoppedException("Execution aborted by user emergency stop.")

    _pressed_keys_for_stop = set()

    def on_press(key):
        try:
            key_name = ""
            if hasattr(key, 'char') and key.char is not None:
                key_name = str(key.char).lower()
            else:
                key_name = str(key).replace("Key.", "").lower()

            _pressed_keys_for_stop.add(key_name)

            has_ctrl = any(k in {"ctrl", "ctrl_l", "ctrl_r"} for k in _pressed_keys_for_stop)
            vk = getattr(key, 'vk', None)
            is_backslash = key_name in {"\\", "¥", "yen", "\x1c"} or vk in {220, 226}
            
            if has_ctrl and is_backslash:
                logger.warning("Emergency stop shortcut (Ctrl+\\) triggered.")
                stop_workflow()
                return False
        except Exception:
            pass

    def on_release(key):
        try:
            key_name = ""
            if hasattr(key, 'char') and key.char is not None:
                key_name = str(key.char).lower()
            else:
                key_name = str(key).replace("Key.", "").lower()
            _pressed_keys_for_stop.discard(key_name)
        except Exception:
            pass

    listener = KeyboardListener(on_press=on_press, on_release=on_release)
    listener.start()
    
    def update_ui(text, is_warning=False):
        if status_callback:
            status_callback(text, is_warning)
        else:
            try:
                from ui.views.running_dialog import RunningDialog
                RunningDialog.set_status(text, is_warning)
            except Exception:
                pass

    try:
        from core.recorder.screen_capturer import get_macros_root
        from datetime import datetime
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id
        
        execution_log = {
            "workflow_id": workflow_id,
            "start_time": datetime.now().isoformat(),
            "start_step": 0,
            "steps": [],
            "status": "running"
        }
        
        executable_macro_path = target_dir / "executable_macro.json"
        variables_path = target_dir / "variables.json"
        
        macro_data = {}
        if executable_macro_path.exists():
            with open(executable_macro_path, 'r', encoding='utf-8') as f:
                macro_data = json.load(f)
        elif temp_commands is None:
            raise FileNotFoundError(f"Missing executable_macro.json in {target_dir}")

        variables = {}
        if variables_path.exists():
            try:
                with open(variables_path, 'r', encoding='utf-8') as f:
                    variables = json.load(f)
            except Exception as e:
                logger.warning(f"[{workflow_id}] Failed to load variables.json: {e}")
            
        if temp_commands is not None:
            commands = temp_commands
        else:
            commands = macro_data.get("commands", [])
            
        macro_needs_save = False
        excel_app_cache = None
        
        start_index = 0
        screen_matched = False
        is_browser_target = False
        force_skip_match_until_enter = False
        current_win_x, current_win_y, current_win_w, current_win_h = 0, 0, 0, 0
        last_win_args = None
        
        try:
            # Why: 全体コマンドから主要ブラウザ操作の有無を包括判定しブラウザモードを確立
            is_browser_target = any(
                any(b in str(c.get("args", {}).get("window_title", "")).lower() for b in ["firefox", "chrome", "edge", "brave", "opera"])
                for c in commands if c.get("method") == "activate_window"
            ) or any(c.get("method") == "browser_action" for c in commands)

            first_activate_cmd = next((cmd for cmd in commands if cmd.get("method") == "activate_window"), None)
            if first_activate_cmd:
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
                        workflow_id,
                        args.get("launch_cmd", ""),
                        args.get("mapped_hwnd"),
                        args.get("is_maximized")
                    )
                except Exception as e:
                    has_subsequent_activate = any(c.get("method") == "activate_window" for c in commands[1:])
                    if has_subsequent_activate:
                        logger.warning(f"[{workflow_id}] First window activation bypassed: {e}")
                    else:
                        raise
                if activated_hwnd:
                    first_alias = args.get("window_alias")
                    # Why: ループ毎新規起動指定時は初回HWNDでコマンド引数を固定せず都度起動を保証
                    if not args.get("loop_launch_each_time"):
                        args["mapped_hwnd"] = activated_hwnd
                    for cmd in commands:
                        if cmd.get("method") == "activate_window":
                            cmd_args = cmd.setdefault("args", {})
                            cmd_app = cmd_args.get("window_title", "").split("—")[-1].split("-")[-1].strip().lower()
                            if cmd_app == app_name or (first_alias and cmd_args.get("window_alias") == first_alias):
                                if not cmd_args.get("loop_launch_each_time"):
                                    cmd_args["mapped_hwnd"] = activated_hwnd
                time.sleep(1.0)
                _check_stop()
                
                import cv2
                import numpy as np
                from core.recorder.screen_capturer import take_screenshot
                
                logger.info(f"[{workflow_id}] Buffering initial frames to detect dynamic regions (e.g., videos)...")
                initial_frames = []
                for _ in range(5):
                    _check_stop()
                    img_pil, curr_monitor = take_screenshot()
                    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2GRAY)
                    initial_frames.append(img_cv)
                    time.sleep(0.1)
                
                curr_img_cv = initial_frames[-1]
                offset_x = curr_monitor.get("left", 0) if isinstance(curr_monitor, dict) else 0
                offset_y = curr_monitor.get("top", 0) if isinstance(curr_monitor, dict) else 0

                std_dev_global = np.std(initial_frames, axis=0)
                global_dynamic_mask = (std_dev_global > 12).astype(np.uint8) * 255
                
                edges_list = [cv2.Canny(f, 50, 150) for f in initial_frames]
                static_edges = edges_list[0]
                for e in edges_list[1:]:
                    static_edges = cv2.bitwise_and(static_edges, e)
                
                kernel_protect = np.ones((3, 3), np.uint8)
                static_edges_dilated = cv2.dilate(static_edges, kernel_protect, iterations=1)
                
                global_dynamic_mask[static_edges_dilated == 255] = 0
                
                # Why: 大画面動画が流れるトップページでも静的枠組みを保持して照合可能に
                if np.count_nonzero(global_dynamic_mask) / global_dynamic_mask.size > 0.85:
                    global_dynamic_mask = (std_dev_global > 25).astype(np.uint8) * 255
                    global_dynamic_mask[static_edges_dilated == 255] = 0

                last_win_args = args
                # Why: 初期画面の一致誤判定によるステップ途中ジャンプ・即終了を防止しStep 1から確実に遂行
                start_index = 0
                screen_matched = False
                
        except Exception as e:
            logger.warning(f"[{workflow_id}] Failed to determine start step by screen match: {e}")
            
        if last_win_args:
            current_win_x = last_win_args.get("x", 0)
            current_win_y = last_win_args.get("y", 0)
            current_win_w = last_win_args.get("width", 0)
            current_win_h = last_win_args.get("height", 0)

        # Why: loop_start前の操作でもExcel変数を参照できるよう第1レコードを先行ロード
        first_excel_loop = next((c for c in commands if c.get("method") == "loop_start" and c.get("args", {}).get("data_source") == "excel"), None)
        if first_excel_loop:
            f_args = first_excel_loop.get("args", {})
            f_st_col = f_args.get("status_column")
            all_ref_cols = _extract_referenced_columns(commands)
            # Why: 抽出対象列とステータス列が一致する場合はステータス列参照を解除
            if f_st_col and f_st_col.strip().upper() in all_ref_cols:
                f_st_col = None
            pre_records = _read_excel_records(
                f_args.get("file_path"), f_args.get("sheet_name"),
                f_args.get("start_row", 2), f_args.get("end_row"),
                f_st_col, f_args.get("skip_completed", True),
                target_dir=target_dir
            )
            # Why: 全件完了済みの場合も第1行の変数を解決可能にするため全レコードから先行取得
            if not pre_records and f_args.get("skip_completed", True):
                pre_records = _read_excel_records(
                    f_args.get("file_path"), f_args.get("sheet_name"),
                    f_args.get("start_row", 2), f_args.get("end_row"),
                    f_args.get("status_column"), skip_completed=False,
                    target_dir=target_dir
                )
            if pre_records:
                logger.info(f"[{workflow_id}] Pre-loaded {len(pre_records)} Excel records. First row keys: {list(pre_records[0].keys())}")
                for k, v in pre_records[0].items():
                    variables[k] = v
                variables[f_args.get("item_variable", "row")] = pre_records[0]
        
        i = start_index
        loop_stack = []
        
        while i < len(commands):
            _check_stop()
            cmd = commands[i]
            watchdog.heartbeat(f"Step {i+1}/{len(commands)}: {cmd.get('method')}")
            method = cmd.get("method")
            raw_args = cmd.get("args", {}).copy()
            args = _resolve_variables(raw_args, variables)

            # Why: アクション種別を問わずステップ実行直前にモーダルダイアログを自動解除しスタック防止
            has_dlg, dlg_title = _detect_unexpected_dialog(last_win_args.get("mapped_hwnd") if last_win_args else None, auto_dismiss=True)
            if has_dlg:
                logger.warning(f"[{workflow_id}] Unexpected dialog detected and dismissed before step {i+1}: {dlg_title}")

            if loop_stack:
                current_loop = loop_stack[-1]
                iteration = current_loop["current_iteration"]
                seq_vars = args.get("seq_vars", {})
                for key, seq_info in seq_vars.items():
                    if isinstance(seq_info, dict) and "step" in seq_info:
                        step_val = seq_info["step"]
                        if key in args:
                            if key == "text":
                                try:
                                    start_val = int(args[key])
                                    args[key] = str(start_val + step_val * iteration)
                                except ValueError:
                                    pass
                            else:
                                args[key] = args[key] + step_val * iteration
            
            step_log = {
                "step_index": i,
                "method": method,
                "args": args,
                "match_info": None,
                "recovery_info": None,
                "timestamp": datetime.now().isoformat()
            }
            
            step_msg = f"Step {i+1}/{len(commands)}: {method}"
            if loop_stack:
                step_msg += f" (Loop {loop_stack[-1]['current_iteration']+1}/{loop_stack[-1]['total_count']})"
            logger.info(f"[{workflow_id}] {step_msg}")
            update_ui(step_msg, False)
            
            raw_event_id = args.get("raw_event_id")
            target_id = args.get("target_id")

            # Why: マクロ内にスクロールコマンドが存在しない場合は誤判定による意図しないホイール回転を完全遮断
            has_scroll_in_macro = any(c.get("method") == "scroll" for c in commands)
            has_prior_scroll = any(commands[k].get("method") == "scroll" for k in range(max(0, i - 3), i))
            if raw_event_id and method in ["click", "move", "type_text", "browser_action"] and is_browser_target and has_scroll_in_macro and has_prior_scroll:
                from core.executor.screen_matcher import align_scroll_by_screen_match
                target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else None) or 0
                scrolled_ok, match_sc = align_scroll_by_screen_match(
                    target_dir=target_dir,
                    raw_event_id=raw_event_id,
                    win_x=current_win_x,
                    win_y=current_win_y,
                    win_w=current_win_w,
                    win_h=current_win_h,
                    target_hwnd=target_hwnd,
                    status_callback=status_callback,
                    check_cancel_callback=lambda: _stop_requested
                )
                if scrolled_ok:
                    logger.info(f"[{workflow_id}] Visual scroll aligned with step {i+1} pre-image: {match_sc}")

            if method == "loop_start":
                data_source = args.get("data_source", "static")
                if data_source == "excel":
                    f_path = args.get("file_path")
                    s_name = args.get("sheet_name")
                    st_row = args.get("start_row", 2)
                    ed_row = args.get("end_row")
                    st_col = args.get("status_column")
                    skip_comp = args.get("skip_completed", True)

                    # Why: ループ内コマンドを走査し文字抽出対象セルへのステータス上書き破壊を完全遮断
                    nest_sub = 1
                    j_sub = i + 1
                    sub_cmds = []
                    while j_sub < len(commands) and nest_sub > 0:
                        if commands[j_sub].get("method") == "loop_start": nest_sub += 1
                        elif commands[j_sub].get("method") == "loop_end": nest_sub -= 1
                        if nest_sub > 0: sub_cmds.append(commands[j_sub])
                        j_sub += 1
                    extracted_cols = _extract_referenced_columns(sub_cmds)
                    if st_col and st_col.strip().upper() in extracted_cols:
                        logger.warning(f"[{workflow_id}] status_column '{st_col}' overlaps with extracted data. Disabling status write to protect source cell text.")
                        st_col = None

                    records = _read_excel_records(f_path, s_name, st_row, ed_row, st_col, skip_comp, target_dir=target_dir)
                    
                    # Why: 全件完了済み等で未処理行が0件の場合、テスト再試行のため全レコードから再取得
                    if not records and skip_comp:
                        logger.info(f"[{workflow_id}] No uncompleted records found. Retrying with skip_completed=False.")
                        records = _read_excel_records(f_path, s_name, st_row, ed_row, st_col, skip_completed=False, target_dir=target_dir)

                    if not records:
                        # Why: ファイル空等の真の0件時のみloop_endまでスキップして安全終了
                        logger.warning(f"[{workflow_id}] No records available in Excel file. Skipping loop.")
                        nest = 1
                        j = i + 1
                        while j < len(commands) and nest > 0:
                            if commands[j].get("method") == "loop_start": nest += 1
                            elif commands[j].get("method") == "loop_end": nest -= 1
                            j += 1
                        i = j
                        continue

                    item_var = args.get("item_variable", "row")
                    for k, v in records[0].items():
                        variables[k] = v
                    variables[item_var] = records[0]

                    # Why: 中間ステータス「処理中」書き込みによる元データ汚染・誤入力を完全撤廃
                    max_iter = args.get("max_iterations", 10000)
                    clamped_records = records[:max_iter]
                    loop_stack.append({
                        "start_index": i,
                        "total_count": len(clamped_records),
                        "current_iteration": 0,
                        "data_source": "excel",
                        "records": clamped_records,
                        "file_path": f_path,
                        "sheet_name": s_name,
                        "status_column": st_col,
                        "item_variable": item_var,
                        "continue_on_error": args.get("continue_on_error", False)
                    })
                else:
                    loop_count = args.get("loop_count", 10)
                    loop_stack.append({
                        "start_index": i,
                        "total_count": loop_count,
                        "current_iteration": 0,
                        "data_source": "static"
                    })
                i += 1
                continue
                
            elif method == "loop_end":
                if loop_stack:
                    current_loop = loop_stack[-1]
                    if current_loop.get("data_source") == "excel":
                        # Why: エラー行に対する「完了」ステータスの上書きを防止
                        if not current_loop.pop("current_had_error", False):
                            curr_rec = current_loop["records"][current_loop["current_iteration"]]
                            st_col = current_loop.get("status_column")
                            f_path = current_loop.get("file_path")
                            s_name = current_loop.get("sheet_name")
                            if st_col and f_path:
                                _write_excel_status(f_path, s_name, curr_rec.get("_row_idx"), st_col, "完了")

                    current_loop["current_iteration"] += 1
                    if current_loop["current_iteration"] < current_loop["total_count"]:
                        if current_loop.get("data_source") == "excel":
                            next_rec = current_loop["records"][current_loop["current_iteration"]]
                            item_var = current_loop.get("item_variable", "row")
                            for k, v in next_rec.items():
                                variables[k] = v
                            variables[item_var] = next_rec
                            # Why: 次行開始時の「処理中」上書きを撤廃
                        i = current_loop["start_index"] + 1
                        continue
                    else:
                        loop_stack.pop()
                i += 1
                continue

            if method == "activate_window":
                current_win_x = args.get("x", 0)
                current_win_y = args.get("y", 0)
                current_win_w = args.get("width", 0)
                current_win_h = args.get("height", 0)
                if (current_win_x == 0 and current_win_y == 0) and last_win_args and last_win_args.get("mapped_hwnd"):
                    try:
                        class RECT(ctypes.Structure):
                            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                        r = RECT()
                        if ctypes.windll.user32.GetWindowRect(last_win_args["mapped_hwnd"], ctypes.byref(r)):
                            current_win_x, current_win_y = r.left, r.top
                            current_win_w, current_win_h = r.right - r.left, r.bottom - r.top
                            last_win_args["x"], last_win_args["y"] = current_win_x, current_win_y
                            last_win_args["width"], last_win_args["height"] = current_win_w, current_win_h
                    except Exception:
                        pass

            # Why: 汎用タグ(div等)や既に可視領域内にある座標は自律スクロールせず直ちに入力・クリックへ移行
            cur_sel = str(args.get("selector") or "").strip().lower()
            is_generic = cur_sel in ["div", "span", "p", "a", "li", "ul", "body"]
            is_coords_visible = args.get("x") is not None and args.get("y") is not None and args.get("x") > 20 and args.get("y") > 45
            if is_browser_target and method in ["click", "type_text", "move"] and (args.get("selector") or args.get("element_name") or args.get("url")) and not is_generic and not is_coords_visible:
                try:
                    from core.executor.browser_controller import BrowserController
                    bc = BrowserController.get_instance()
                    target_hwnd_for_offset = (last_win_args.get("mapped_hwnd") if last_win_args else None) or (ctypes.windll.user32.GetForegroundWindow() if platform.system() == "Windows" else None)
                    pre_off_x, pre_off_y = _get_window_offset(target_hwnd_for_offset, current_win_x, current_win_y)
                    new_coords = bc.scroll_into_view(
                        selector=args.get("selector"),
                        last_win_args=last_win_args,
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
                        logger.info(f"[{workflow_id}] Element scrolled into view. Updated coords to ({new_coords[0]}, {new_coords[1]})")
                except Exception as sc_err:
                    logger.debug(f"[{workflow_id}] Auto scroll_into_view bypassed: {sc_err}")

            # Why: スクロールや移動での照合待機を排除し主要入力時も最大1.5秒で高速評価
            if method in ["click", "type_text"] and raw_event_id:
                if loop_stack:
                    _wait_for_screen_settle(timeout=1.5, settle_threshold=0.004)
                else:
                    match_eid = args.get("match_event_id") or raw_event_id
                    match_info = wait_for_screen_match(
                        target_dir, match_eid, current_win_x, current_win_y, current_win_w, current_win_h, 
                        workflow_id, status_callback, i, timeout=1.5, check_cancel_callback=lambda: _stop_requested
                    )
                    _check_stop()
                    step_log["match_info"] = match_info
                    update_ui(step_msg, False)
            
            _check_stop()
            
            if raw_event_id and target_id and method in ["click", "move"]:
                recovered_ok, recovery_info = verify_and_heal_target(
                    workflow_id=workflow_id,
                    target_id=target_id,
                    raw_event_id=raw_event_id,
                    args=args,
                    target_dir=target_dir,
                    last_win_args=last_win_args,
                    status_callback=status_callback,
                    check_stop_callback=_check_stop,
                )
                if recovery_info:
                    step_log["recovery_info"] = recovery_info
                    if recovered_ok:
                        macro_needs_save = True
                    else:
                        if loop_stack and loop_stack[-1].get("continue_on_error"):
                            i = _skip_failed_loop_step(i, commands, loop_stack, workflow_id, update_ui, "UI検出失敗")
                            continue
                        execution_log["status"] = "failed"
                        execution_log["error"] = "Healer failed to recover target"
                        raise RuntimeError("対象のUIが見つからず、自己修復にも失敗したためマクロを安全停止しました。")
            
            if method == "wait":
                prev_method = commands[i - 1].get("method") if i > 0 else ""
                next_has_pre_img = False
                for j in range(i + 1, len(commands)):
                    if commands[j].get("method") not in ["wait", "activate_window", "loop_start", "loop_end"]:
                        next_eid = commands[j].get("args", {}).get("raw_event_id")
                        if next_eid and (target_dir / "images" / f"{next_eid}_pre.png").exists():
                            next_has_pre_img = True
                        break

                # Why: スクロール直後は描画・アニメーション完了が必須のため固定待機を維持
                if next_has_pre_img and prev_method not in ["move", "click", "scroll"]:
                    logger.info(f"[{workflow_id}] Skipping fixed wait in favor of screen matching for the next action.")
                    i += 1
                    continue

                duration = float(args.get("duration", 0.0))
                # Why: 0.6秒以上の待機時は画面描画収束を検知して無駄な待機時間を適応短縮
                if duration >= 0.6 and prev_method in ["click", "type_text", "press_key", "browser_action"]:
                    settled = _wait_for_screen_settle(timeout=min(duration, 3.0), settle_threshold=0.003)
                    if settled:
                        logger.info(f"[{workflow_id}] Smart adaptive wait: Screen settled early, proceeding.")
                        i += 1
                        continue

                sleep_intervals = int(duration * 10)
                for _ in range(sleep_intervals):
                    _check_stop()
                    time.sleep(0.1)
                remainder = duration - (sleep_intervals * 0.1)
                if remainder > 0:
                    _check_stop()
                    time.sleep(remainder)
                    
            elif method == "activate_window":
                last_win_args = args
                window_title = args.get("window_title", "")
                win_x = args.get("x", 0)
                win_y = args.get("y", 0)
                win_w = args.get("width", 0)
                win_h = args.get("height", 0)
                launch_cmd = args.get("launch_cmd", "")
                loop_launch = raw_args.get("loop_launch_each_time", False) or args.get("loop_launch_each_time", False)

                # Why: ループ毎新規起動が有効な場合は各周回でmapped_hwnd=-1として新規起動を実行
                if loop_stack and loop_launch:
                    mapped_hwnd = -1
                else:
                    mapped_hwnd = args.get("mapped_hwnd")
                current_alias = args.get("window_alias")

                # Why: ループ内でのウィンドウ再アクティベート時もHWNDを追跡・固定
                act_hwnd = activate_and_restore_window(window_title, win_x, win_y, win_w, win_h, workflow_id, launch_cmd, mapped_hwnd, args.get("is_maximized"))
                if act_hwnd:
                    if not loop_launch:
                        args["mapped_hwnd"] = act_hwnd
                        cmd.setdefault("args", {})["mapped_hwnd"] = act_hwnd
                        if current_alias:
                            for future_cmd in commands[i+1:]:
                                if future_cmd.get("method") == "activate_window" and future_cmd.get("args", {}).get("window_alias") == current_alias:
                                    future_cmd.setdefault("args", {})["mapped_hwnd"] = act_hwnd
                    last_win_args["mapped_hwnd"] = act_hwnd
                set_system_cursor("run_idle")

            elif method in ["click", "move", "scroll", "type_text", "press_key"]:
                excel_app_cache, should_skip = execute_physical_action(
                    method=method,
                    args=args,
                    last_win_args=last_win_args,
                    current_win_rect=(current_win_x, current_win_y, current_win_w, current_win_h),
                    is_browser_target=is_browser_target,
                    excel_app_cache=excel_app_cache,
                    workflow_id=workflow_id,
                    mouse=mouse,
                    keyboard=keyboard,
                    check_stop_callback=_check_stop,
                )
                if should_skip:
                    i += 1
                    continue
            elif method == "excel_action":
                try:
                    excel_app_cache, excel_res = _execute_excel_action(
                        args, variables, excel_app_cache, last_win_args, workflow_id
                    )
                    step_log["excel_result"] = excel_res
                except Exception as e:
                    logger.error(f"[{workflow_id}] Excel action execution failed: {e}")
                    if loop_stack and loop_stack[-1].get("continue_on_error"):
                        i = _skip_failed_loop_step(i, commands, loop_stack, workflow_id, update_ui, str(e))
                        continue
                    raise
            elif method == "browser_action":
                try:
                    if last_win_args and platform.system() == "Windows":
                        mapped_hwnd = last_win_args.get("mapped_hwnd")
                        hwnd = ctypes.windll.user32.GetForegroundWindow()
                        root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 3)
                        needs_act = False
                        if mapped_hwnd and hwnd != mapped_hwnd and root_hwnd != mapped_hwnd:
                            needs_act = True
                        if needs_act:
                            activate_and_restore_window(
                                last_win_args.get("window_title", ""),
                                last_win_args.get("x", 0),
                                last_win_args.get("y", 0),
                                last_win_args.get("width", 0),
                                last_win_args.get("height", 0),
                                workflow_id,
                                last_win_args.get("launch_cmd", ""),
                                mapped_hwnd,
                                last_win_args.get("is_maximized")
                            )
                    from core.executor.browser_controller import BrowserController
                    controller = BrowserController.get_instance()
                    browser_res = controller.execute_action(
                        args, variables, last_win_args, workflow_id
                    )
                    step_log["browser_result"] = browser_res
                except Exception as e:
                    logger.error(f"[{workflow_id}] Browser action execution failed: {e}")
                    if loop_stack and loop_stack[-1].get("continue_on_error"):
                        i = _skip_failed_loop_step(i, commands, loop_stack, workflow_id, update_ui, str(e))
                        continue
                    raise
            else:
                logger.warning(f"Unknown method: {method}")
                
            execution_log["steps"].append(step_log)
            i += 1
                
        if macro_needs_save and temp_commands is None:
            try:
                with open(executable_macro_path, 'w', encoding='utf-8') as f:
                    json.dump(macro_data, f, indent=4, ensure_ascii=False)
                logger.info(f"[{workflow_id}] Successfully saved healed coordinates to executable_macro.json for future runs.")
            except Exception as e:
                logger.error(f"[{workflow_id}] Failed to save healed macro to file: {e}")

        execution_log["status"] = "success"
        logger.info(f"[{workflow_id}] Macro execution finished successfully.")
        
    except WorkflowStoppedException as e:
        execution_log["status"] = "stopped"
        logger.warning(f"[{workflow_id}] {e}")
    except WorkflowFreezeException as e:
        execution_log["status"] = "frozen_aborted"
        err_msg = f"マクロの無応答（フリーズ）を検知したため強制終了しました (30秒タイムアウト)。\n発生箇所: {watchdog.current_step_info}"
        execution_log["error"] = err_msg
        logger.error(f"[{workflow_id}] {err_msg}")
        update_ui("フリーズ検知により強制終了しました", is_warning=True)
        raise RuntimeError(err_msg) from e
    except Exception as e:
        # Why: 異常停止時にExcelのステータス列へエラー詳細を即時書き戻し二重処理を抑止
        if 'loop_stack' in locals() and loop_stack and loop_stack[-1].get("data_source") == "excel":
            try:
                curr_loop = loop_stack[-1]
                rec = curr_loop["records"][curr_loop["current_iteration"]]
                s_col = curr_loop.get("status_column")
                fp = curr_loop.get("file_path")
                sn = curr_loop.get("sheet_name")
                if s_col and fp:
                    _write_excel_status(fp, sn, rec.get("_row_idx"), s_col, f"エラー: {str(e)[:30]}")
            except Exception:
                pass
        execution_log["status"] = "failed"
        execution_log["error"] = str(e)
        logger.error(f"[{workflow_id}] Execution failed: {e}", exc_info=True)
        update_ui(f"エラー停止: {e}", is_warning=True)
        raise
    finally:
        watchdog.stop()
        restore_system_cursor(force=True)
        try:
            execution_log["end_time"] = datetime.now().isoformat()
            log_dir = target_dir / "execution_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"run_log_{int(time.time())}.json"
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump(execution_log, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save execution log: {e}")
            
        listener.stop()
        _is_running = False
        _stop_requested = False

def stop_workflow():
    global _stop_requested
    _stop_requested = True
    logger.warning("Emergency stop signal activated by user.")
