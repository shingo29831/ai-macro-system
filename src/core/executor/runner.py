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

def run_workflow(workflow_id: str, config: AppConfig, status_callback=None):
    global _is_running, _stop_requested
    _is_running = True
    _stop_requested = False
    reset_browser_activation_flag()
    
    logger.info(f"[{workflow_id}] Starting executable macro execution...")
    mouse = MouseController()
    keyboard = KeyboardController()

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
        
        if not executable_macro_path.exists():
            raise FileNotFoundError(f"Missing executable_macro.json in {target_dir}")
            
        with open(executable_macro_path, 'r', encoding='utf-8') as f:
            macro_data = json.load(f)

        variables = {}
        if variables_path.exists():
            try:
                with open(variables_path, 'r', encoding='utf-8') as f:
                    variables = json.load(f)
            except Exception as e:
                logger.warning(f"[{workflow_id}] Failed to load variables.json: {e}")
            
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
            first_activate_cmd = next((cmd for cmd in commands if cmd.get("method") == "activate_window"), None)
            if first_activate_cmd:
                args = first_activate_cmd.get("args", {})
                window_title = args.get("window_title", "")
                app_name = window_title.split("—")[-1].split("-")[-1].strip().lower()
                is_browser_target = any(b in app_name for b in ["firefox", "chrome", "edge", "brave", "opera"])
                
                activate_and_restore_window(
                    window_title,
                    args.get("x", 0),
                    args.get("y", 0),
                    args.get("width", 0),
                    args.get("height", 0),
                    workflow_id,
                    args.get("launch_cmd", "")
                )
                time.sleep(1.0)
                
                import cv2
                import numpy as np
                from core.recorder.screen_capturer import take_screenshot
                
                logger.info(f"[{workflow_id}] Buffering initial frames to detect dynamic regions (e.g., videos)...")
                initial_frames = []
                for _ in range(5):
                    img_pil, curr_monitor = take_screenshot()
                    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2GRAY)
                    initial_frames.append(img_cv)
                    time.sleep(0.1)
                
                curr_img_cv = initial_frames[-1]
                offset_x = curr_monitor.get("left", 0) if isinstance(curr_monitor, dict) else 0
                offset_y = curr_monitor.get("top", 0) if isinstance(curr_monitor, dict) else 0

                std_dev_global = np.std(initial_frames, axis=0)
                global_dynamic_mask = (std_dev_global > 20).astype(np.uint8) * 255
                
                edges_list = [cv2.Canny(f, 50, 150) for f in initial_frames]
                static_edges = edges_list[0]
                for e in edges_list[1:]:
                    static_edges = cv2.bitwise_and(static_edges, e)
                
                kernel_protect = np.ones((3, 3), np.uint8)
                static_edges_dilated = cv2.dilate(static_edges, kernel_protect, iterations=1)
                
                global_dynamic_mask[static_edges_dilated == 255] = 0
                
                if np.count_nonzero(global_dynamic_mask) / global_dynamic_mask.size > 0.3:
                    global_dynamic_mask = np.zeros_like(global_dynamic_mask)

                last_win_args = args
                for i, cmd in enumerate(commands):
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
                        args.get("launch_cmd", "")
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
            cmd = commands[i]
            if _stop_requested:
                logger.warning(f"[{workflow_id}] Execution aborted by user emergency stop.")
                break
                
            method = cmd.get("method")
            args = cmd.get("args", {}).copy()
            
            if loop_stack and method in ["click", "move", "activate_window"]:
                current_loop = loop_stack[-1]
                iteration = current_loop["current_iteration"]
                variables = current_loop["variables"]
                
                if "y_offset" in variables and "y" in args:
                    args["y"] += variables["y_offset"] * iteration
                if "x_offset" in variables and "x" in args:
                    args["x"] += variables["x_offset"] * iteration
            
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
                loop_variables = args.get("loop_variables", {})
                loop_stack.append({
                    "start_index": i,
                    "total_count": loop_count,
                    "current_iteration": 0,
                    "variables": loop_variables
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

            if method not in ["wait", "activate_window", "loop_start", "loop_end"] and raw_event_id:
                if force_skip_match_until_enter:
                    logger.info(f"[{workflow_id}] Skipping screen match for fresh browser search.")
                    if method == "press_key" and args.get("key") == "enter":
                        force_skip_match_until_enter = False
                elif loop_stack:
                    logger.info(f"[{workflow_id}] Skipping screen match inside loop.")
                    time.sleep(0.5)
                else:
                    match_info = wait_for_screen_match(
                        target_dir, raw_event_id, current_win_x, current_win_y, current_win_w, current_win_h, 
                        workflow_id, status_callback, i, timeout=10.0, check_cancel_callback=lambda: _stop_requested
                    )
                    step_log["match_info"] = match_info
                    update_ui(step_msg, False)
            
            if raw_event_id and target_id and method in ["click", "move"]:
                needs_recovery = False
                crop_image_path = target_dir / "images" / f"{raw_event_id}_crop.png"
                
                try:
                    from core.recorder.screen_capturer import take_screenshot
                    from core.healer.recovery_manager import attempt_recovery
                    import cv2
                    import numpy as np

                    current_img_pil, _ = take_screenshot()

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
                        if status_callback:
                            status_callback("自己修復中...", True)
                            
                        recovery_result = attempt_recovery(workflow_id, target_id)
                        
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
                next_has_event = False
                for j in range(i + 1, len(commands)):
                    if commands[j].get("method") not in ["wait", "activate_window", "loop_start", "loop_end"]:
                        if commands[j].get("args", {}).get("raw_event_id"):
                            next_has_event = True
                        break
                
                if next_has_event:
                    logger.info(f"[{workflow_id}] Skipping fixed wait in favor of screen matching for the next action.")
                    i += 1
                    continue

                duration = args.get("duration", 0.0)
                sleep_intervals = int(duration * 10)
                for _ in range(sleep_intervals):
                    if _stop_requested:
                        break
                    time.sleep(0.1)
                remainder = duration - (sleep_intervals * 0.1)
                if remainder > 0 and not _stop_requested:
                    time.sleep(remainder)
                    
            elif method == "activate_window":
                last_win_args = args
                window_title = args.get("window_title", "")
                win_x = args.get("x", 0)
                win_y = args.get("y", 0)
                win_w = args.get("width", 0)
                win_h = args.get("height", 0)
                launch_cmd = args.get("launch_cmd", "")
                
                activate_and_restore_window(window_title, win_x, win_y, win_w, win_h, workflow_id, launch_cmd)

            elif method in ["click", "move", "scroll", "type_text", "press_key"]:
                if last_win_args:
                    window_title = last_win_args.get("window_title", "")
                    app_name = window_title.split("—")[-1].split("-")[-1].strip()
                    if app_name and platform.system() == "Windows":
                        hwnd = ctypes.windll.user32.GetForegroundWindow()
                        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                        buff = ctypes.create_unicode_buffer(length + 1)
                        ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                        current_fg_title = buff.value
                        
                        if app_name.lower() not in current_fg_title.lower():
                            logger.info(f"[{workflow_id}] Window '{app_name}' is not in foreground. Activating...")
                            activate_and_restore_window(
                                window_title,
                                last_win_args.get("x", 0),
                                last_win_args.get("y", 0),
                                last_win_args.get("width", 0),
                                last_win_args.get("height", 0),
                                workflow_id,
                                last_win_args.get("launch_cmd", "")
                            )

                if method == "click":
                    x = args.get("x", 0)
                    y = args.get("y", 0)
                    button_str = args.get("button", "left")
                    clicks = args.get("clicks", 1)
                    excel_dest_cell = args.get("excel_dest_cell")
                    
                    if excel_dest_cell and platform.system() == "Windows":
                        try:
                            import win32com.client
                            if excel_app_cache is None:
                                excel_app_cache = win32com.client.GetActiveObject("Excel.Application")
                            sheet = excel_app_cache.ActiveSheet
                            sheet.Range(excel_dest_cell).Select()
                            time.sleep(0.05)
                            continue
                        except Exception as e:
                            logger.warning(f"[{workflow_id}] Failed to select Excel dest cell {excel_dest_cell}: {e}")
                            excel_app_cache = None
                    
                    btn = Button.right if button_str == "right" else Button.middle if button_str == "middle" else Button.left
                    
                    if platform.system() == "Windows":
                        ctypes.windll.user32.SetCursorPos(int(x), int(y))
                    else:
                        mouse.position = (x, y)
                        
                    time.sleep(0.05)
                    mouse.click(btn, clicks)

                elif method == "move":
                    x = args.get("x", 0)
                    y = args.get("y", 0)
                    excel_dest_cell = args.get("excel_dest_cell")
                    
                    if excel_dest_cell and platform.system() == "Windows":
                        try:
                            import win32com.client
                            if excel_app_cache is None:
                                excel_app_cache = win32com.client.GetActiveObject("Excel.Application")
                            sheet = excel_app_cache.ActiveSheet
                            sheet.Range(excel_dest_cell).Select()
                            time.sleep(0.05)
                            continue
                        except Exception as e:
                            logger.warning(f"[{workflow_id}] Failed to select Excel dest cell {excel_dest_cell}: {e}")
                            excel_app_cache = None
                    
                    if platform.system() == "Windows":
                        ctypes.windll.user32.SetCursorPos(int(x), int(y))
                    else:
                        mouse.position = (x, y)
                        
                    time.sleep(0.5)
                    
                elif method == "scroll":
                    dx = args.get("dx", 0.0)
                    dy = args.get("dy", 0.0)
                    x = args.get("x")
                    y = args.get("y")
                    
                    if x is not None and y is not None and (x != 0 or y != 0):
                        if platform.system() == "Windows":
                            ctypes.windll.user32.SetCursorPos(int(x), int(y))
                        else:
                            mouse.position = (x, y)
                        time.sleep(0.01)
                    
                    if platform.system() == "Windows":
                        if dy != 0.0:
                            scroll_amount = int(dy * WHEEL_DELTA)
                            ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, scroll_amount, 0)
                        if dx != 0.0:
                            scroll_amount_x = int(dx * WHEEL_DELTA)
                            ctypes.windll.user32.mouse_event(MOUSEEVENTF_HWHEEL, 0, 0, scroll_amount_x, 0)
                    else:
                        mouse.scroll(dx, dy)
                        
                elif method == "type_text":
                    text = args.get("text", "")
                    seq_val = args.get("sequence_value")
                    excel_cell = args.get("excel_cell")
                    
                    if seq_val and loop_stack:
                        current_loop = loop_stack[-1]
                        iteration = current_loop["current_iteration"]
                        start_val = seq_val.get("start", 1)
                        step_val = seq_val.get("step", 1)
                        text = str(start_val + step_val * iteration)
                        
                    if text:
                        for key, val in variables.items():
                            placeholder = f"{{{{{key}}}}}"
                            if placeholder in text:
                                text = text.replace(placeholder, str(val))
                                
                        if excel_cell and platform.system() == "Windows":
                            try:
                                import win32com.client
                                if excel_app_cache is None:
                                    excel_app_cache = win32com.client.GetActiveObject("Excel.Application")
                                sheet = excel_app_cache.ActiveSheet
                                sheet.Range(excel_cell).Value = text
                                time.sleep(0.05)
                                continue
                            except Exception as e:
                                logger.warning(f"[{workflow_id}] Failed to set Excel cell value {excel_cell}: {e}")
                                excel_app_cache = None
                                
                        set_ime_state(text)
                        for char in text:
                            if _stop_requested:
                                break
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
            else:
                logger.warning(f"Unknown method: {method}")
                
            execution_log["steps"].append(step_log)
            i += 1
                
        if not _stop_requested:
            if macro_needs_save:
                try:
                    with open(executable_macro_path, 'w', encoding='utf-8') as f:
                        json.dump(macro_data, f, indent=4, ensure_ascii=False)
                    logger.info(f"[{workflow_id}] Successfully saved healed coordinates to executable_macro.json for future runs.")
                except Exception as e:
                    logger.error(f"[{workflow_id}] Failed to save healed macro to file: {e}")

            execution_log["status"] = "success"
            logger.info(f"[{workflow_id}] Macro execution finished successfully.")
        else:
            execution_log["status"] = "stopped"
        
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