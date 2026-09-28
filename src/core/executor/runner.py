# Role: 生成されたExecutable Macro (executable_macro.json) を読み込み、ローカルで自律実行する実行エンジン。

import json
import logging
import time
import platform
import ctypes
import subprocess
from pathlib import Path
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key, Listener as KeyboardListener

from models.data_types import AppConfig
from core.executor.window_manager import set_dpi_awareness, set_ime_state, activate_and_restore_window, reset_browser_activation_flag
from core.executor.screen_matcher import wait_for_screen_match, is_screen_match

logger = logging.getLogger(__name__)

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120

_is_running = False
_stop_requested = False

set_dpi_awareness()

class WorkflowStoppedException(Exception):
    """ユーザーによってマクロの実行が強制停止された場合に送出される例外"""
    pass

def _smooth_move(target_x: int, target_y: int, steps: int = 10, duration: float = 0.12):
    # Why: ホバー位置から子メニューへのワープ移動によるドロップダウン消滅を連続軌跡で完全防止
    if platform.system() == "Windows":
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        start_x, start_y = pt.x, pt.y
        dist = ((target_x - start_x) ** 2 + (target_y - start_y) ** 2) ** 0.5
        if dist < 5:
            ctypes.windll.user32.SetCursorPos(int(target_x), int(target_y))
            ctypes.windll.user32.mouse_event(0x0001, 0, 0, 0, 0)
            return

        actual_steps = max(4, min(steps, int(dist / 12)))
        step_delay = duration / actual_steps
        for s in range(1, actual_steps + 1):
            t = s / actual_steps
            ease_t = 1.0 - (1.0 - t) ** 2
            cx = int(start_x + (target_x - start_x) * ease_t)
            cy = int(start_y + (target_y - start_y) * ease_t)
            ctypes.windll.user32.SetCursorPos(cx, cy)
            ctypes.windll.user32.mouse_event(0x0001, 0, 0, 0, 0)
            time.sleep(step_delay)
        ctypes.windll.user32.SetCursorPos(int(target_x), int(target_y))
        ctypes.windll.user32.mouse_event(0x0001, 0, 0, 0, 0)
    else:
        mouse = MouseController()
        mouse.position = (target_x, target_y)


def _get_window_offset(hwnd: int, rec_x: int, rec_y: int) -> tuple[int, int]:
    # Why: 記録時と実行時のウィンドウ配置の差分をオフセットとして補正
    if not hwnd or platform.system() != "Windows":
        return 0, 0
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect.left - rec_x, rect.top - rec_y
    except Exception:
        pass
    return 0, 0

def _get_excel_application(hwnd: int = None, expected_title: str = ""):
    if platform.system() != "Windows":
        return None
    try:
        import win32gui
        import win32com.client
        import pythoncom
        import ctypes

        excel7_hwnd = None
        if hwnd:
            # Why: タイトル文字列に依存せずウィンドウクラス名(XLMAIN/EXCEL7)でExcelを厳密判定
            cls_name = win32gui.GetClassName(hwnd)
            if cls_name == "EXCEL7":
                excel7_hwnd = hwnd
            elif cls_name == "XLMAIN":
                def enum_child(child, _):
                    nonlocal excel7_hwnd
                    try:
                        if win32gui.GetClassName(child) == "EXCEL7":
                            excel7_hwnd = child
                            return False
                    except Exception:
                        pass
                    return True
                try:
                    win32gui.EnumChildWindows(hwnd, enum_child, None)
                except Exception:
                    pass

        # 1. EXCEL7 から直接 COM オブジェクトを取得
        if excel7_hwnd:
            try:
                iid = (ctypes.c_byte * 16)(
                    0x00, 0x04, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
                    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46
                )
                p_acc = ctypes.c_void_p()
                hr = ctypes.oledll.oleacc.AccessibleObjectFromWindow(
                    excel7_hwnd, -16, iid, ctypes.byref(p_acc)
                )
                if hr == 0 and p_acc.value:
                    pycom_dll = ctypes.PyDLL(pythoncom.__file__)
                    pycom_func = pycom_dll.PyCom_PyObjectFromIUnknown
                    pycom_func.restype = ctypes.py_object
                    pycom_func.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
                    py_dispatch = pycom_func(p_acc.value, ctypes.byref(iid), 0)
                    if py_dispatch:
                        disp = win32com.client.Dispatch(py_dispatch)
                        app = getattr(disp, "Application", disp)
                        if app:
                            return app
            except Exception:
                pass

        # 2. フォールバック: 対象ブック名を特定して取得
        try:
            excel = win32com.client.GetActiveObject("Excel.Application")
            if excel:
                actual_title = ""
                if hwnd:
                    try:
                        actual_title = win32gui.GetWindowText(hwnd)
                    except Exception:
                        pass
                target_search = actual_title if actual_title else expected_title
                # Why: 操作対象ウィンドウの実タイトルから一致するWorkbookを特定して誤爆防止
                if target_search:
                    for wb in excel.Workbooks:
                        if wb.Name.lower() in target_search.lower():
                            wb.Activate()
                            return excel
                return excel
        except Exception:
            pass

    except Exception:
        pass
    return None

def _execute_excel_action(args: dict, variables: dict, excel_app, last_win_args: dict, workflow_id: str):
    if platform.system() != "Windows":
        logger.warning(f"[{workflow_id}] Excel COM automation is only supported on Windows.")
        return excel_app, None

    import win32com.client
    import win32gui

    action = args.get("action", "")
    file_path = args.get("file_path")
    sheet_name = args.get("sheet_name")
    cell = args.get("cell")
    range_addr = args.get("range_address") or cell
    value = args.get("value")
    var_name = args.get("variable_name")
    macro_name = args.get("macro_name")
    close_save = args.get("close_save", True)

    if isinstance(value, str):
        for k, v in variables.items():
            value = value.replace(f"{{{{{k}}}}}", str(v)).replace(f"${{{k}}}", str(v))

    if excel_app is None:
        target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else None) or win32gui.GetForegroundWindow()
        expected_t = last_win_args.get("window_title", "") if last_win_args else ""
        excel_app = _get_excel_application(target_hwnd, expected_t)

    if excel_app is None and action == "open_workbook":
        excel_app = win32com.client.Dispatch("Excel.Application")
        excel_app.Visible = True

    if excel_app is None:
        try:
            excel_app = win32com.client.GetActiveObject("Excel.Application")
            excel_app.Visible = True
        except Exception:
            excel_app = win32com.client.Dispatch("Excel.Application")
            excel_app.Visible = True

    # Why: Excel操作時にウィンドウを前面化し他アプリとの切り替えを可視化
    try:
        if hasattr(excel_app, "Hwnd") and excel_app.Hwnd:
            ctypes.windll.user32.SetForegroundWindow(excel_app.Hwnd)
    except Exception:
        pass

    wb = None
    if file_path:
        resolved_path = str(Path(file_path).resolve())
        for open_wb in excel_app.Workbooks:
            if open_wb.FullName.lower() == resolved_path.lower() or open_wb.Name.lower() == Path(file_path).name.lower():
                wb = open_wb
                break
        if wb is None and action == "open_workbook":
            wb = excel_app.Workbooks.Open(resolved_path)

    if wb is None:
        try:
            wb = excel_app.ActiveWorkbook
        except Exception:
            wb = None

    sheet = None
    if wb:
        if sheet_name:
            try:
                sheet = wb.Sheets(sheet_name)
            except Exception:
                sheet = wb.ActiveSheet
        else:
            try:
                sheet = wb.ActiveSheet
            except Exception:
                pass

    result = None
    if action == "open_workbook":
        if wb:
            wb.Activate()
            result = wb.Name
    elif action == "save_workbook":
        if wb:
            if file_path and str(Path(file_path).resolve()).lower() != str(Path(wb.FullName).resolve()).lower():
                wb.SaveAs(str(Path(file_path).resolve()))
            else:
                wb.Save()
    elif action == "close_workbook":
        if wb:
            wb.Close(SaveChanges=close_save)
    elif action == "select_sheet":
        if sheet:
            sheet.Activate()
    elif action == "add_sheet":
        if wb:
            new_sheet = wb.Sheets.Add()
            if sheet_name:
                new_sheet.Name = sheet_name
            new_sheet.Activate()
    elif action == "read_cell":
        if sheet and cell:
            result = sheet.Range(cell).Value
            if var_name:
                variables[var_name] = result
                logger.info(f"[{workflow_id}] Read Excel cell {cell} -> variables['{var_name}'] = {result}")
    elif action == "write_cell":
        if sheet and cell:
            sheet.Range(cell).Value = value
    elif action == "read_range":
        if sheet and range_addr:
            raw_vals = sheet.Range(range_addr).Value
            result = [list(r) if isinstance(r, tuple) else r for r in raw_vals] if isinstance(raw_vals, tuple) else raw_vals
            if var_name:
                variables[var_name] = result
                logger.info(f"[{workflow_id}] Read Excel range {range_addr} -> variables['{var_name}']")
    elif action == "write_range":
        if sheet and range_addr:
            sheet.Range(range_addr).Value = value
    elif action == "insert_row":
        if sheet and cell:
            sheet.Rows(cell).Insert()
    elif action == "delete_row":
        if sheet and cell:
            sheet.Rows(cell).Delete()
    elif action == "clear_range":
        if sheet and range_addr:
            sheet.Range(range_addr).ClearContents()
    elif action == "run_macro":
        if macro_name:
            result = excel_app.Run(macro_name)
    else:
        logger.warning(f"[{workflow_id}] Unknown Excel action: {action}")

    return excel_app, result


def run_workflow(workflow_id: str, config: AppConfig, status_callback=None, temp_commands: list[dict] = None):
    global _is_running, _stop_requested
    _is_running = True
    _stop_requested = False
    reset_browser_activation_flag()
    
    logger.info(f"[{workflow_id}] Starting executable macro execution...")
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
                        args.get("mapped_hwnd")
                    )
                except Exception as e:
                    has_subsequent_activate = any(c.get("method") == "activate_window" for c in commands[1:])
                    if has_subsequent_activate:
                        logger.warning(f"[{workflow_id}] First window activation bypassed: {e}")
                    else:
                        raise
                if activated_hwnd:
                    args["mapped_hwnd"] = activated_hwnd
                    first_alias = args.get("window_alias")
                    for cmd in commands:
                        if cmd.get("method") == "activate_window":
                            cmd_args = cmd.setdefault("args", {})
                            # Why: 同一アプリであればエイリアス差異に関わらずHWNDを共有し2重起動を防止
                            cmd_app = cmd_args.get("window_title", "").split("—")[-1].split("-")[-1].strip().lower()
                            if cmd_app == app_name or (first_alias and cmd_args.get("window_alias") == first_alias):
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
                # Why: デスクトップアプリの先頭入力スキップを防ぐためステップ途中再開はブラウザのみに限定
                if is_browser_target:
                    for i, cmd in enumerate(commands):
                        _check_stop()
                        if cmd.get("method") == "activate_window":
                            last_win_args = cmd.get("args", {})
                        
                        raw_event_id = cmd.get("args", {}).get("raw_event_id")
                        if raw_event_id and last_win_args:
                            pre_image_path = target_dir / "images" / f"{raw_event_id}_pre.png"
                            if pre_image_path.exists():
                                win_x = last_win_args.get("x", 0)
                                win_y = last_win_args.get("y", 0)
                                win_w = last_win_args.get("width", 0)
                                win_h = last_win_args.get("height", 0)
                                
                                is_match, scores = is_screen_match(pre_image_path, curr_img_cv, win_x, win_y, win_w, win_h, offset_x, offset_y, global_dynamic_mask)
                                if is_match:
                                    logger.info(f"[{workflow_id}] Current screen matches step {i+1} (event: {raw_event_id}). Starting from here. Scores: {scores}")
                                    start_index = i
                                    screen_matched = True
                                    execution_log["start_step"] = start_index
                                    execution_log["initial_match_scores"] = scores
                                    break
                                
            if screen_matched and start_index > 0:
                last_activation = None
                for j in range(start_index):
                    if commands[j].get("method") == "activate_window":
                        last_activation = commands[j]
                
                if last_activation:
                    args = last_activation.get("args", {})
                    activate_and_restore_window(
                        args.get("window_title", ""),
                        args.get("x", 0),
                        args.get("y", 0),
                        args.get("width", 0),
                        args.get("height", 0),
                        workflow_id,
                        args.get("launch_cmd", ""),
                        args.get("mapped_hwnd")
                    )
                    time.sleep(0.5)
            elif not screen_matched and is_browser_target:
                logger.info(f"[{workflow_id}] Screen did not match any recorded steps. Opening new tab for fresh browser search.")
                keyboard.press(Key.ctrl)
                keyboard.press('t')
                keyboard.release('t')
                keyboard.release(Key.ctrl)
                time.sleep(0.5)
                force_skip_match_until_enter = True
                
        except Exception as e:
            logger.warning(f"[{workflow_id}] Failed to determine start step by screen match: {e}")
            
        if last_win_args:
            current_win_x = last_win_args.get("x", 0)
            current_win_y = last_win_args.get("y", 0)
            current_win_w = last_win_args.get("width", 0)
            current_win_h = last_win_args.get("height", 0)
        
        i = start_index
        loop_stack = []
        
        while i < len(commands):
            _check_stop()
            cmd = commands[i]
                
            method = cmd.get("method")
            args = cmd.get("args", {}).copy()
            
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

            if method == "loop_start":
                loop_count = args.get("loop_count", 10)
                loop_stack.append({
                    "start_index": i,
                    "total_count": loop_count,
                    "current_iteration": 0
                })
                i += 1
                continue
                
            elif method == "loop_end":
                if loop_stack:
                    current_loop = loop_stack[-1]
                    current_loop["current_iteration"] += 1
                    if current_loop["current_iteration"] < current_loop["total_count"]:
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

            if method not in ["wait", "activate_window", "loop_start", "loop_end", "excel_action", "browser_action"] and raw_event_id:
                if force_skip_match_until_enter:
                    logger.info(f"[{workflow_id}] Skipping screen match for fresh browser search.")
                    if method == "press_key" and args.get("key") == "enter":
                        force_skip_match_until_enter = False
                elif loop_stack:
                    logger.info(f"[{workflow_id}] Skipping screen match inside loop.")
                    time.sleep(0.5)
                else:
                    match_eid = args.get("match_event_id") or raw_event_id
                    match_info = wait_for_screen_match(
                        target_dir, match_eid, current_win_x, current_win_y, current_win_w, current_win_h, 
                        workflow_id, status_callback, i, timeout=10.0, check_cancel_callback=lambda: _stop_requested
                    )
                    _check_stop()
                    step_log["match_info"] = match_info
                    update_ui(step_msg, False)
            
            _check_stop()
            
            if raw_event_id and target_id and method in ["click", "move"]:
                needs_recovery = False
                crop_image_path = target_dir / "images" / f"{raw_event_id}_crop.png"
                
                try:
                    from core.recorder.screen_capturer import take_screenshot
                    from core.healer.recovery_manager import attempt_recovery
                    import cv2
                    import numpy as np

                    current_img_pil, _ = take_screenshot()
                    _check_stop()

                    if crop_image_path.exists():
                        current_img_cv = cv2.cvtColor(np.array(current_img_pil), cv2.COLOR_RGB2BGR)
                        template_cv = cv2.imread(str(crop_image_path), cv2.IMREAD_COLOR)

                        if template_cv is not None:
                            res = cv2.matchTemplate(current_img_cv, template_cv, cv2.TM_CCOEFF_NORMED)
                            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                            
                            if max_val < 0.8:
                                logger.warning(f"[{workflow_id}] Template match failed (Confidence: {max_val:.2f}). Initiating Healer...")
                                needs_recovery = True
                            else:
                                match_center_x = max_loc[0] + template_cv.shape[1] // 2
                                match_center_y = max_loc[1] + template_cv.shape[0] // 2
                                
                                expected_x = args.get("x", 0)
                                expected_y = args.get("y", 0)
                                
                                dist = ((match_center_x - expected_x)**2 + (match_center_y - expected_y)**2)**0.5
                                
                                if dist > 20:
                                    logger.warning(f"[{workflow_id}] Target UI drifted by {dist:.1f} pixels. Initiating Healer...")
                                    needs_recovery = True
                        else:
                            needs_recovery = True
                    else:
                        logger.warning(f"[{workflow_id}] No crop image available. Initiating Healer...")
                        needs_recovery = True

                    if needs_recovery:
                        logger.warning(f"[{workflow_id}] Healer is disabled temporarily. Bypassing recovery and continuing.")
                        needs_recovery = False

                    if needs_recovery:
                        _check_stop()
                        if status_callback:
                            status_callback("自己修復中...", True)
                            
                        recovery_result = attempt_recovery(workflow_id, target_id)
                        _check_stop()
                        
                        step_log["recovery_info"] = recovery_result
                        if recovery_result.get("success"):
                            new_coords = recovery_result.get("new_coordinates")
                            if new_coords:
                                args["x"] = new_coords["x"]
                                args["y"] = new_coords["y"]
                                logger.info(f"[{workflow_id}] Healer successfully updated coordinates to ({args['x']}, {args['y']}).")
                                macro_needs_save = True
                        else:
                            logger.error(f"[{workflow_id}] Healer failed to recover target '{target_id}'. Aborting execution.")
                            if status_callback:
                                status_callback("実行中...", False)
                            execution_log["status"] = "failed"
                            execution_log["error"] = "Healer failed to recover target"
                            raise RuntimeError("対象のUIが見つからず、自己修復にも失敗したためマクロを安全停止しました。")
                        
                        if status_callback:
                            status_callback("実行中...", False)
                            
                except Exception as e:
                    logger.error(f"[{workflow_id}] Error during image validation/recovery: {e}")
                    if status_callback:
                        status_callback("実行中...", False)
                    if "安全のため" in str(e):
                        raise e
            
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

                duration = args.get("duration", 0.0)
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
                mapped_hwnd = args.get("mapped_hwnd")
                current_alias = args.get("window_alias")
                
                # Why: ループ内でのウィンドウ再アクティベート時もHWNDを追跡・固定
                act_hwnd = activate_and_restore_window(window_title, win_x, win_y, win_w, win_h, workflow_id, launch_cmd, mapped_hwnd)
                if act_hwnd:
                    args["mapped_hwnd"] = act_hwnd
                    last_win_args["mapped_hwnd"] = act_hwnd
                    if current_alias:
                        for future_cmd in commands[i+1:]:
                            if future_cmd.get("method") == "activate_window" and future_cmd.get("args", {}).get("window_alias") == current_alias:
                                future_cmd.setdefault("args", {})["mapped_hwnd"] = act_hwnd

            elif method in ["click", "move", "scroll", "type_text", "press_key"]:
                if last_win_args:
                    mapped_hwnd = last_win_args.get("mapped_hwnd")
                    if platform.system() == "Windows":
                        hwnd = ctypes.windll.user32.GetForegroundWindow()
                        needs_activation = False
                        
                        if mapped_hwnd:
                            if hwnd != mapped_hwnd:
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
                                mapped_hwnd
                            )

                target_hwnd_for_offset = (last_win_args.get("mapped_hwnd") if last_win_args else None) or (ctypes.windll.user32.GetForegroundWindow() if platform.system() == "Windows" else None)
                off_x, off_y = _get_window_offset(target_hwnd_for_offset, current_win_x, current_win_y)

                if method == "click":
                    x = args.get("x", 0) + off_x
                    y = args.get("y", 0) + off_y
                    button_str = args.get("button", "left")
                    clicks = args.get("clicks", 1)
                    excel_dest_cell = args.get("excel_dest_cell")
                    
                    skip_physical = False
                    if excel_dest_cell and platform.system() == "Windows":
                        try:
                            if excel_app_cache is None:
                                target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else None) or ctypes.windll.user32.GetForegroundWindow()
                                excel_app_cache = _get_excel_application(target_hwnd)
                            if excel_app_cache:
                                sheet = excel_app_cache.ActiveSheet
                                sheet.Range(excel_dest_cell).Select()
                                time.sleep(0.05)
                                skip_physical = True
                        except Exception as e:
                            logger.warning(f"[{workflow_id}] Failed to select Excel dest cell {excel_dest_cell}: {e}")
                            excel_app_cache = None
                    
                    # Why: スクロール後の微細な座標ズレをUIA要素探索で吸収し正確に補正
                    selector = args.get("selector")
                    elem_name = args.get("element_name")
                    if is_browser_target and (selector or elem_name):
                        try:
                            from core.executor.browser_controller import BrowserController
                            controller = BrowserController.get_instance()
                            target_elem = controller._find_uia_element(selector or elem_name, last_win_args)
                            if target_elem:
                                r = target_elem.rectangle()
                                if r.width() > 0 and r.height() > 0:
                                    x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                        except Exception:
                            pass

                    if not skip_physical:
                        btn = Button.right if button_str == "right" else Button.middle if button_str == "middle" else Button.left
                        _smooth_move(int(x), int(y))
                        # Why: クリック直前にカーソル位置を安定させホバー/展開UIの空振りを防止
                        time.sleep(0.08)
                        mouse.click(btn, clicks)

                elif method == "move":
                    x = args.get("x", 0) + off_x
                    y = args.get("y", 0) + off_y
                    excel_dest_cell = args.get("excel_dest_cell")
                    
                    skip_physical = False
                    if excel_dest_cell and platform.system() == "Windows":
                        try:
                            if excel_app_cache is None:
                                target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else None) or ctypes.windll.user32.GetForegroundWindow()
                                excel_app_cache = _get_excel_application(target_hwnd)
                            if excel_app_cache:
                                sheet = excel_app_cache.ActiveSheet
                                sheet.Range(excel_dest_cell).Select()
                                time.sleep(0.05)
                                skip_physical = True
                        except Exception as e:
                            logger.warning(f"[{workflow_id}] Failed to select Excel dest cell {excel_dest_cell}: {e}")
                            excel_app_cache = None
                    
                    if not skip_physical:
                        _smooth_move(int(x), int(y))
                        time.sleep(0.5)
                        
                elif method == "scroll":
                    dx = args.get("dx", 0.0)
                    dy = args.get("dy", 0.0)
                    x = args.get("x")
                    y = args.get("y")
                    
                    if x is not None and y is not None and (x != 0 or y != 0):
                        sx, sy = int(x + off_x), int(y + off_y)
                        if platform.system() == "Windows":
                            ctypes.windll.user32.SetCursorPos(sx, sy)
                            # Why: SetCursorPosだけではホバーが反映されないためMOVEで領域を捕捉
                            ctypes.windll.user32.mouse_event(0x0001, 0, 0, 0, 0)
                        else:
                            mouse.position = (sx, sy)
                        time.sleep(0.05)
                    
                    if platform.system() == "Windows":
                        # Why: 一括送信によるスクロールの間引きを防ぐため1ノッチずつ分割送信
                        if dy != 0.0:
                            direction = 1 if dy > 0 else -1
                            total_notches = max(1, int(round(abs(dy))))
                            raw_val = ctypes.c_ulong((direction * WHEEL_DELTA) & 0xFFFFFFFF).value
                            for _ in range(total_notches):
                                _check_stop()
                                ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, raw_val, 0)
                                time.sleep(0.035)
                        if dx != 0.0:
                            direction_x = 1 if dx > 0 else -1
                            total_notches_x = max(1, int(round(abs(dx))))
                            raw_val_x = ctypes.c_ulong((direction_x * WHEEL_DELTA) & 0xFFFFFFFF).value
                            for _ in range(total_notches_x):
                                _check_stop()
                                ctypes.windll.user32.mouse_event(MOUSEEVENTF_HWHEEL, 0, 0, raw_val_x, 0)
                                time.sleep(0.035)
                        # Why: スクロール後のスムーズアニメーションおよび描画完了を待機
                        time.sleep(0.35)
                    else:
                        steps = max(1, int(round(abs(dy))))
                        dir_y = 1 if dy > 0 else -1
                        for _ in range(steps):
                            mouse.scroll(0, dir_y)
                            time.sleep(0.035)
                        time.sleep(0.35)
                        
                elif method == "type_text":
                    text = args.get("text", "")
                    excel_cell = args.get("excel_cell")
                    
                    if text:
                        for key, val in variables.items():
                            text = text.replace(f"{{{{{key}}}}}", str(val)).replace(f"${{{key}}}", str(val))
                                
                        skip_physical = False
                        if excel_cell and platform.system() == "Windows":
                            try:
                                if excel_app_cache is None:
                                    target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else None) or ctypes.windll.user32.GetForegroundWindow()
                                    expected_t = last_win_args.get("window_title", "") if last_win_args else ""
                                    excel_app_cache = _get_excel_application(target_hwnd, expected_t)
                                if excel_app_cache:
                                    sheet = excel_app_cache.ActiveSheet
                                    sheet.Range(excel_cell).Select()
                                    sheet.Range(excel_cell).Value = text
                                    time.sleep(0.05)
                                    skip_physical = True
                            except Exception as e:
                                logger.warning(f"[{workflow_id}] Failed to set Excel cell value {excel_cell}: {e}")
                                excel_app_cache = None
                                
                        if not skip_physical:
                            # Why: 物理入力時にEXCEL7ワークシート領域をクリックしてフォーカスを確実に確立
                            if platform.system() == "Windows":
                                try:
                                    import win32gui
                                    target_h = (last_win_args.get("mapped_hwnd") if last_win_args else None) or ctypes.windll.user32.GetForegroundWindow()
                                    excel7_h = None
                                    def _focus_ex7(c, _):
                                        nonlocal excel7_h
                                        if win32gui.GetClassName(c) == "EXCEL7":
                                            excel7_h = c
                                            return False
                                        return True
                                    if win32gui.GetClassName(target_h) == "EXCEL7":
                                        excel7_h = target_h
                                    else:
                                        win32gui.EnumChildWindows(target_h, _focus_ex7, None)
                                    if excel7_h:
                                        ctypes.windll.user32.SetForegroundWindow(target_h)
                                        rect = win32gui.GetWindowRect(excel7_h)
                                        click_x = rect[0] + 50
                                        click_y = rect[1] + 50
                                        ctypes.windll.user32.SetCursorPos(click_x, click_y)
                                        time.sleep(0.02)
                                        ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)
                                        ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)
                                        time.sleep(0.05)
                                except Exception:
                                    pass
                            set_ime_state(text)
                            for char in text:
                                _check_stop()
                                keyboard.type(char)
                                time.sleep(0.03)
                            time.sleep(0.2)
                        
                elif method == "press_key":
                    key_str = args.get("key", "")
                    if key_str:
                        try:
                            if "+" in key_str:
                                keys = key_str.split("+")
                                pressed = []
                                for k in keys:
                                    k_name = k.lower().replace("key.", "")
                                    if k_name in ["win", "windows"]: k_name = "cmd"
                                    key_obj = getattr(Key, k_name, k_name)
                                    keyboard.press(key_obj)
                                    pressed.append(key_obj)
                                for key_obj in reversed(pressed):
                                    keyboard.release(key_obj)
                            else:
                                key_name = key_str.lower()
                                if key_name in ["win", "windows"]:
                                    key_name = "cmd"
                                    
                                if hasattr(Key, key_name):
                                    special_key = getattr(Key, key_name)
                                    keyboard.press(special_key)
                                    keyboard.release(special_key)
                                else:
                                    keyboard.press(key_str)
                                    keyboard.release(key_str)
                        except Exception as e:
                            logger.warning(f"Failed to press key {key_str}: {e}")
            elif method == "excel_action":
                try:
                    excel_app_cache, excel_res = _execute_excel_action(
                        args, variables, excel_app_cache, last_win_args, workflow_id
                    )
                    step_log["excel_result"] = excel_res
                except Exception as e:
                    logger.error(f"[{workflow_id}] Excel action execution failed: {e}")
                    raise
            elif method == "browser_action":
                try:
                    from core.executor.browser_controller import BrowserController
                    controller = BrowserController.get_instance()
                    browser_res = controller.execute_action(
                        args, variables, last_win_args, workflow_id
                    )
                    step_log["browser_result"] = browser_res
                except Exception as e:
                    logger.error(f"[{workflow_id}] Browser action execution failed: {e}")
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
    except Exception as e:
        execution_log["status"] = "failed"
        execution_log["error"] = str(e)
        logger.error(f"[{workflow_id}] Execution failed: {e}")
        raise
    finally:
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