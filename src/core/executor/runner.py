# src/core/executor/runner.py
# @role: 生成されたExecutable Macro (executable_macro.json) を読み込み、ローカルで自律実行する実行エンジン。

import json
import logging
import time
import platform
import ctypes
import re
import subprocess
from pathlib import Path
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key, Listener as KeyboardListener

from models.data_types import AppConfig

logger = logging.getLogger(__name__)

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120

_is_running = False
_stop_requested = False
_browser_activated_once = False

def _set_dpi_awareness():
    if platform.system() == "Windows":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

_set_dpi_awareness()

def _set_ime_state(text: str):
    """テキストに全角文字が含まれるか判定し、WindowsのIMEを自動でオン/オフする"""
    if platform.system() != "Windows":
        return
    import unicodedata
    def contains_zenkaku(s: str) -> bool:
        for c in s:
            if unicodedata.east_asian_width(c) in ('F', 'W'):
                return True
        return False
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        default_ime_wnd = ctypes.windll.imm32.ImmGetDefaultIMEWnd(hwnd)
        if default_ime_wnd:
            is_zenkaku = contains_zenkaku(text)
            WM_IME_CONTROL = 0x0283
            IMC_SETOPENSTATUS = 0x0006
            ctypes.windll.user32.SendMessageW(default_ime_wnd, WM_IME_CONTROL, IMC_SETOPENSTATUS, 1 if is_zenkaku else 0)
            time.sleep(0.05)
    except Exception as e:
        logger.warning(f"Failed to set IME state: {e}")

def _activate_and_restore_window(window_title: str, win_x: int, win_y: int, win_w: int, win_h: int, keyboard, workflow_id: str):
    """対象のウィンドウをアクティブにし、必要に応じてアプリを起動・サイズ復元を行う"""
    global _browser_activated_once
    if not window_title or platform.system() != "Windows":
        return

    import pywinauto
    desktop = pywinauto.Desktop(backend="uia")
    safe_title = re.escape(window_title)
    windows = desktop.windows(title_re=f".*{safe_title}.*", visible_only=True)
    
    app_name = window_title.split("—")[-1].split("-")[-1].strip()
    
    if not windows and app_name:
        safe_app_name = re.escape(app_name)
        windows = desktop.windows(title_re=f".*{safe_app_name}.*", visible_only=True)
            
    if not windows:
        logger.warning(f"[{workflow_id}] Window not found: {window_title}. Attempting to launch...")
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
            
        if launch_cmd:
            creationflags = 0x08000000 # CREATE_NO_WINDOW (cmd画面を非表示)
            subprocess.Popen(launch_cmd, shell=True, creationflags=creationflags)
            time.sleep(4.0)
            windows = desktop.windows(title_re=f".*{safe_app_name}.*", visible_only=True)
            
        is_browser = any(b in lower_app_name for b in ["firefox", "chrome", "edge", "brave", "opera"])
        if is_browser:
            _browser_activated_once = True
            
    if windows:
        win = windows[0]
        if win.is_minimized():
            win.restore()
        win.set_focus()
        
        if win_w > 0 and win_h > 0:
            try:
                hwnd = win.handle
                ctypes.windll.user32.SetWindowPos(hwnd, 0, win_x, win_y, win_w, win_h, 0x0004)
            except Exception as e:
                logger.warning(f"Failed to resize window: {e}")
                
        time.sleep(0.5)
        
        lower_app_name = app_name.lower()
        is_browser = any(b in lower_app_name for b in ["firefox", "chrome", "edge", "brave", "opera"])
        if is_browser and not _browser_activated_once:
            _browser_activated_once = True
            logger.info(f"[{workflow_id}] Opening new tab for fresh browser search.")
            keyboard.press(Key.ctrl)
            keyboard.press('t')
            keyboard.release('t')
            keyboard.release(Key.ctrl)
            time.sleep(0.5)
    else:
        logger.error(f"[{workflow_id}] Failed to find or launch window: {window_title}")
        raise RuntimeError(f"対象のアプリ（{app_name}）が起動できず、ウィンドウが見つかりません。")

def _wait_for_screen_match(target_dir: Path, raw_event_id: str, win_x: int, win_y: int, win_w: int, win_h: int, workflow_id: str, status_callback):
    """記録時のスクリーンショットと現在の画面を比較し、変化率が閾値以下になるまで待機する"""
    global _stop_requested
    if not raw_event_id or win_w <= 0 or win_h <= 0:
        return

    pre_image_path = target_dir / "images" / f"{raw_event_id}_pre.png"
    if not pre_image_path.exists():
        return

    def update_ui(text, is_warning):
        if status_callback:
            status_callback(text, is_warning)
        else:
            try:
                from ui.views.running_dialog import RunningDialog
                RunningDialog.set_status(text, is_warning)
            except Exception:
                pass

    try:
        import cv2
        import numpy as np
        from core.recorder.screen_capturer import take_screenshot
        
        pre_img_cv = cv2.imread(str(pre_image_path), cv2.IMREAD_GRAYSCALE)
        if pre_img_cv is None:
            return
            
        img_h, img_w = pre_img_cv.shape
        
        # マルチモニターのオフセットを考慮するため、ダミー呼び出しでモニター情報を取得
        _, monitor_info = take_screenshot()
        offset_x = monitor_info.get("left", 0) if isinstance(monitor_info, dict) else 0
        offset_y = monitor_info.get("top", 0) if isinstance(monitor_info, dict) else 0
        
        x1 = max(0, win_x - offset_x)
        y1 = max(0, win_y - offset_y)
        x2 = min(img_w, win_x - offset_x + win_w)
        y2 = min(img_h, win_y - offset_y + win_h)
        
        if x2 <= x1 or y2 <= y1:
            x1, y1 = max(0, win_x), max(0, win_y)
            x2, y2 = min(img_w, win_x + win_w), min(img_h, win_y + win_h)
            if x2 <= x1 or y2 <= y1:
                return
            
        pre_crop = pre_img_cv[y1:y2, x1:x2]
        pre_crop_small = cv2.resize(pre_crop, (128, 128), interpolation=cv2.INTER_AREA)
        
        waiting_logged = False
        frame_buffer = [] # 直近のフレームを保持するバッファ
        
        while not _stop_requested:
            curr_img_pil, curr_monitor = take_screenshot()
            curr_img_cv = cv2.cvtColor(np.array(curr_img_pil), cv2.COLOR_RGB2GRAY)
            
            c_offset_x = curr_monitor.get("left", 0) if isinstance(curr_monitor, dict) else 0
            c_offset_y = curr_monitor.get("top", 0) if isinstance(curr_monitor, dict) else 0
            
            curr_h, curr_w = curr_img_cv.shape
            cx1 = max(0, win_x - c_offset_x)
            cy1 = max(0, win_y - c_offset_y)
            cx2 = min(curr_w, win_x - c_offset_x + win_w)
            cy2 = min(curr_h, win_y - c_offset_y + win_h)
            
            if cx2 <= cx1 or cy2 <= cy1:
                cx1, cy1 = max(0, win_x), max(0, win_y)
                cx2, cy2 = min(curr_w, win_x + win_w), min(curr_h, win_y + win_h)
                
            if cx2 > cx1 and cy2 > cy1:
                curr_crop = curr_img_cv[cy1:cy2, cx1:cx2]
                curr_crop_small = cv2.resize(curr_crop, (128, 128), interpolation=cv2.INTER_AREA)
                
                # フレームバッファの更新（直近5フレームを保持）
                frame_buffer.append(curr_crop_small)
                if len(frame_buffer) > 5:
                    frame_buffer.pop(0)
                
                dynamic_mask = np.zeros((128, 128), dtype=np.uint8)
                
                # 3フレーム以上蓄積されたら、動画領域（動的マスク）を計算
                if len(frame_buffer) >= 3:
                    # ピクセルごとの標準偏差（変化の激しさ）を計算
                    std_dev = np.std(frame_buffer, axis=0)
                    # 標準偏差が一定以上（変化し続けている）箇所を動画領域としてマスク
                    dynamic_mask = (std_dev > 10).astype(np.uint8) * 255
                    
                    # マスクを少し膨張させて、動画の境界の揺らぎをカバーする
                    kernel = np.ones((5, 5), np.uint8)
                    dynamic_mask = cv2.dilate(dynamic_mask, kernel, iterations=1)
                
                # 記録時画像と現在画像の差分を計算
                diff = cv2.absdiff(pre_crop_small, curr_crop_small)
                _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
                
                # ★動画領域（動的マスク）を差分から除外（無視）する
                static_thresh = cv2.bitwise_and(thresh, cv2.bitwise_not(dynamic_mask))
                
                # 静的領域（比較対象となるUI部分）のピクセル数
                static_area_size = (128 * 128) - np.count_nonzero(dynamic_mask)
                
                # 静的領域が極端に少ない（画面のほぼ全体が動画）場合を除き、静的領域のみで変化率を計算
                if static_area_size > 1000: 
                    diff_ratio = np.count_nonzero(static_thresh) / static_area_size
                else:
                    diff_ratio = np.count_nonzero(thresh) / (128 * 128)
                
                # 静的領域の変化率が10%以下なら「同じ画面」と判定
                if diff_ratio <= 0.10:
                    if waiting_logged:
                        update_ui("マクロを再開します。", False)
                        logger.info(f"[{workflow_id}] Screen matched (static diff: {diff_ratio:.1%}). Resuming macro.")
                        time.sleep(1.5)
                        update_ui("実行中...", False)
                    break
                else:
                    if not waiting_logged:
                        update_ui("記録時と同じ画面にしてください。", True)
                        logger.info(f"[{workflow_id}] Waiting for screen to match... (static diff: {diff_ratio:.1%})")
                        waiting_logged = True
            else:
                if not waiting_logged:
                    update_ui("記録時と同じ画面にしてください。", True)
                    logger.info(f"[{workflow_id}] Waiting for screen to match... (size mismatch)")
                    waiting_logged = True
            
            time.sleep(0.5)
    except Exception as e:
        logger.warning(f"Error during screen match waiting: {e}")

def run_workflow(workflow_id: str, config: AppConfig, status_callback=None):
    global _is_running, _stop_requested, _browser_activated_once
    _is_running = True
    _stop_requested = False
    _browser_activated_once = False
    
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
                
                _activate_and_restore_window(window_title, win_x, win_y, win_w, win_h, keyboard, workflow_id)
                _wait_for_screen_match(target_dir, raw_event_id, win_x, win_y, win_w, win_h, workflow_id, status_callback)

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
                            
                    _set_ime_state(text)
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