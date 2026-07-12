# src/core/executor/runner.py
# @role: 生成されたExecutable Macro (executable_macro.json) を読み込み、ローカルで自律実行する実行エンジン。
#
# 【参照元 (呼ばれる側)】
#   - ui/viewmodels/* (ユーザーの実行ボタン押下時)
#
# 【参照先 (呼ぶ側)】
#   - models/data_types (設定ファイルの型参照)

import json
import logging
import time
import platform
import ctypes
from pathlib import Path
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key, Listener as KeyboardListener

from models.data_types import AppConfig

logger = logging.getLogger(__name__)

# Windows API Constants for Scroll Simulation
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120

# OSレベルのDPIスケーリングによるマウス座標のズレを防止
def _set_dpi_awareness():
    if platform.system() == "Windows":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2) # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

_set_dpi_awareness()

_is_running = False
_stop_requested = False

def run_workflow(workflow_id: str, config: AppConfig, status_callback=None):
    global _is_running, _stop_requested
    _is_running = True
    _stop_requested = False
    
    logger.info(f"[{workflow_id}] Starting executable macro execution...")
    mouse = MouseController()
    keyboard = KeyboardController()

    # --- 緊急停止用ホットキー監視 (Ctrl + \) ---
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
    # ---------------------------------------------
    
    try:
        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id
        
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
        
        for i, cmd in enumerate(commands):
            if _stop_requested:
                logger.warning(f"[{workflow_id}] Execution aborted by user emergency stop.")
                break
                
            method = cmd.get("method")
            args = cmd.get("args", {})
            
            logger.info(f"[{workflow_id}] Executing command {i+1}/{len(commands)}: {method}")
            
            # === Stage 1: UI部品(Crop)の局所的なテンプレートマッチングによる高精度なズレ検知 ===
            raw_event_id = args.get("raw_event_id")
            target_id = args.get("target_id")
            
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

                    # 一時的に自己修復機能をバイパスし、元の座標で続行する
                    if needs_recovery:
                        logger.warning(f"[{workflow_id}] Healer is disabled temporarily. Bypassing recovery and continuing.")
                        needs_recovery = False

                    if needs_recovery:
                        if status_callback:
                            status_callback("自己修復中...", True)
                            
                        recovery_result = attempt_recovery(workflow_id, target_id)
                        
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
                            raise RuntimeError("対象のUIが見つからず、自己修復にも失敗したためマクロを安全停止しました。")
                        
                        if status_callback:
                            status_callback("実行中...", False)
                            
                except Exception as e:
                    logger.error(f"[{workflow_id}] Error during image validation/recovery: {e}")
                    if status_callback:
                        status_callback("実行中...", False)
                    if "安全のため" in str(e):
                        raise e
            # =======================================
            
            if method == "wait":
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
                window_title = args.get("window_title", "")
                win_x = args.get("x", 0)
                win_y = args.get("y", 0)
                win_w = args.get("width", 0)
                win_h = args.get("height", 0)
                
                if window_title and platform.system() == "Windows":
                    try:
                        import pywinauto
                        import re
                        import subprocess
                        
                        desktop = pywinauto.Desktop(backend="uia")
                        safe_title = re.escape(window_title)
                        windows = desktop.windows(title_re=f".*{safe_title}.*", visible_only=True)
                        
                        # タイトルからアプリ名（末尾の - 以降）を抽出
                        app_name = window_title.split("—")[-1].split("-")[-1].strip()
                        
                        if not windows and app_name:
                            safe_app_name = re.escape(app_name)
                            windows = desktop.windows(title_re=f".*{safe_app_name}.*", visible_only=True)
                                
                        if not windows:
                            logger.warning(f"[{workflow_id}] Window not found: {window_title}. Attempting to launch...")
                            # アプリが立ち上がっていない場合の起動試行
                            lower_app_name = app_name.lower()
                            launch_cmd = None
                            if "firefox" in lower_app_name:
                                launch_cmd = "start firefox"
                            elif "chrome" in lower_app_name:
                                launch_cmd = "start chrome"
                            elif "edge" in lower_app_name:
                                launch_cmd = "start msedge"
                            elif "excel" in lower_app_name:
                                launch_cmd = "start excel"
                            else:
                                # 汎用的なフォールバック
                                launch_cmd = f"start \"\" \"{app_name}\""
                                
                            if launch_cmd:
                                subprocess.Popen(launch_cmd, shell=True)
                                time.sleep(4.0) # 起動待ち
                                
                                # 再検索
                                windows = desktop.windows(title_re=f".*{safe_app_name}.*", visible_only=True)
                                
                        if windows:
                            win = windows[0]
                            # 最小化されている場合は元に戻す
                            if win.is_minimized():
                                win.restore()
                            win.set_focus()
                            
                            # ウィンドウサイズと位置の復元
                            if win_w > 0 and win_h > 0:
                                try:
                                    import ctypes
                                    hwnd = win.handle
                                    # SWP_NOZORDER = 0x0004 (Zオーダーを変更しない)
                                    ctypes.windll.user32.SetWindowPos(hwnd, 0, win_x, win_y, win_w, win_h, 0x0004)
                                except Exception as e:
                                    logger.warning(f"Failed to resize window: {e}")
                                    
                            time.sleep(0.5)
                        else:
                            logger.error(f"[{workflow_id}] Failed to find or launch window: {window_title}")
                            # 見つからない場合はエラーにしてマクロを安全停止する
                            raise RuntimeError(f"対象のアプリ（{app_name}）が起動できず、ウィンドウが見つかりません。")
                            
                    except Exception as e:
                        logger.warning(f"[{workflow_id}] Failed to activate window {window_title}: {e}")
                        if "対象のアプリ" in str(e):
                            raise e

            elif method == "click":
                x = args.get("x", 0)
                y = args.get("y", 0)
                button_str = args.get("button", "left")
                clicks = args.get("clicks", 1)
                
                btn = Button.right if button_str == "right" else Button.middle if button_str == "middle" else Button.left
                
                mouse.position = (x, y)
                time.sleep(0.05)
                mouse.click(btn, clicks)

            elif method == "move":
                x = args.get("x", 0)
                y = args.get("y", 0)
                
                mouse.position = (x, y)
                time.sleep(0.5)
                
            elif method == "scroll":
                dx = args.get("dx", 0.0)
                dy = args.get("dy", 0.0)
                x = args.get("x")
                y = args.get("y")
                
                if x is not None and y is not None and (x != 0 or y != 0):
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
                if text:
                    for key, val in variables.items():
                        placeholder = f"{{{{{key}}}}}"
                        if placeholder in text:
                            text = text.replace(placeholder, str(val))
                            
                    # 半角/全角の自動制御 (Windows)
                    if platform.system() == "Windows":
                        import unicodedata
                        def contains_zenkaku(s: str) -> bool:
                            for c in s:
                                # 'F' (Fullwidth), 'W' (Wide) のみを全角と判定
                                # 'A' (Ambiguous) は環境依存のため除外（誤判定防止）
                                if unicodedata.east_asian_width(c) in ('F', 'W'):
                                    return True
                            return False
                        
                        try:
                            hwnd = ctypes.windll.user32.GetForegroundWindow()
                            # 別プロセスのIMEを制御するためには DefaultIMEWnd にメッセージを送る必要がある
                            default_ime_wnd = ctypes.windll.imm32.ImmGetDefaultIMEWnd(hwnd)
                            if default_ime_wnd:
                                is_zenkaku = contains_zenkaku(text)
                                WM_IME_CONTROL = 0x0283
                                IMC_SETOPENSTATUS = 0x0006
                                ctypes.windll.user32.SendMessageW(default_ime_wnd, WM_IME_CONTROL, IMC_SETOPENSTATUS, 1 if is_zenkaku else 0)
                                time.sleep(0.05) # IMEの状態が反映されるまで少し待つ
                        except Exception as e:
                            logger.warning(f"Failed to set IME state: {e}")

                    keyboard.type(text)
                    
            elif method == "press_key":
                key_str = args.get("key", "")
                if key_str:
                    try:
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
                
        if not _stop_requested:
            if macro_needs_save:
                try:
                    with open(executable_macro_path, 'w', encoding='utf-8') as f:
                        json.dump(macro_data, f, indent=4, ensure_ascii=False)
                    logger.info(f"[{workflow_id}] Successfully saved healed coordinates to executable_macro.json for future runs.")
                except Exception as e:
                    logger.error(f"[{workflow_id}] Failed to save healed macro to file: {e}")

            logger.info(f"[{workflow_id}] Macro execution finished successfully.")
        
    except Exception as e:
        logger.error(f"[{workflow_id}] Execution failed: {e}")
        raise
    finally:
        listener.stop()
        _is_running = False
        _stop_requested = False

def stop_workflow():
    global _stop_requested
    _stop_requested = True
    logger.warning("Emergency stop signal activated by user.")