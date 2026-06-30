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
        
        for i, cmd in enumerate(commands):
            if _stop_requested:
                logger.warning(f"[{workflow_id}] Execution aborted by user emergency stop.")
                break
                
            method = cmd.get("method")
            args = cmd.get("args", {})
            
            logger.info(f"[{workflow_id}] Executing command {i+1}/{len(commands)}: {method}")
            
            # === Stage 1: 画像テンプレートマッチングによる高精度なズレ検知と自己修復(Healer)の起動 ===
            raw_event_id = args.get("raw_event_id")
            step_id = args.get("step_id")
            
            if raw_event_id and step_id and method in ["click", "hover", "scroll", "type_text"]:
                needs_recovery = False
                
                crop_image_path = target_dir / "images" / f"{raw_event_id}_crop.png"
                pre_image_path = target_dir / "images" / f"{raw_event_id}_pre.png"
                
                try:
                    from PIL import Image
                    from core.recorder.screen_capturer import take_screenshot, calculate_diff_percent
                    from core.healer.recovery_manager import attempt_recovery
                    import cv2
                    import numpy as np

                    current_img_pil, _ = take_screenshot()

                    if crop_image_path.exists():
                        # 記録時のUIの切り抜き画像を使って現在の画面から正確な位置を探す
                        current_img_cv = cv2.cvtColor(np.array(current_img_pil), cv2.COLOR_RGB2BGR)
                        template_cv = cv2.imread(str(crop_image_path), cv2.IMREAD_COLOR)

                        if template_cv is not None:
                            res = cv2.matchTemplate(current_img_cv, template_cv, cv2.TM_CCOEFF_NORMED)
                            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                            
                            # 類似度が80%未満の場合はUIの見た目が変化した・消失したと判定
                            if max_val < 0.8:
                                logger.warning(f"[{workflow_id}] Template match failed (Confidence: {max_val:.2f}). Initiating Healer...")
                                needs_recovery = True
                            else:
                                # マッチした位置の中心座標を計算
                                match_center_x = max_loc[0] + template_cv.shape[1] // 2
                                match_center_y = max_loc[1] + template_cv.shape[0] // 2
                                
                                expected_x = args.get("x", 0)
                                expected_y = args.get("y", 0)
                                
                                # 本来クリックする予定の座標と、現在UIが存在する実際の座標の距離を測る
                                dist = ((match_center_x - expected_x)**2 + (match_center_y - expected_y)**2)**0.5
                                
                                # 20ピクセル以上ズレていればクリックミスを防ぐために修復を起動
                                if dist > 20:
                                    logger.warning(f"[{workflow_id}] Target UI drifted by {dist:.1f} pixels. Initiating Healer...")
                                    needs_recovery = True
                        else:
                            needs_recovery = True
                            
                    elif pre_image_path.exists():
                        # 切り抜き画像がない場合は、全画面差分の閾値を 15.0% -> 2.0% に大幅に下げて敏感に検知する
                        original_img = Image.open(pre_image_path)
                        diff_str = calculate_diff_percent(original_img, current_img_pil)
                        diff_val = float(diff_str.replace("%", ""))
                        
                        if diff_val > 2.0: 
                            logger.warning(f"[{workflow_id}] Visual drift detected (Diff: {diff_val}%). Initiating Healer...")
                            needs_recovery = True

                    # ズレ検知時の自己修復 (Stage 2: YOLO/OCRによる意味的再検索) の実行
                    if needs_recovery:
                        if status_callback:
                            status_callback("自己修復中...", True)
                            
                        recovery_result = attempt_recovery(workflow_id, step_id)
                        
                        if recovery_result.get("success"):
                            new_coords = recovery_result.get("new_coordinates")
                            if new_coords and method in ["click", "hover", "scroll"]:
                                # 記録時のテキストやYOLO情報から新しく見つけ出した座標でアクションを上書きする
                                args["x"] = new_coords["x"]
                                args["y"] = new_coords["y"]
                                logger.info(f"[{workflow_id}] Healer successfully updated coordinates to ({args['x']}, {args['y']}).")
                        else:
                            logger.warning(f"[{workflow_id}] Healer failed to recover. Proceeding with original coordinates.")
                        
                        if status_callback:
                            status_callback("実行中...", False)
                            
                except Exception as e:
                    logger.error(f"[{workflow_id}] Error during image validation/recovery: {e}")
                    if status_callback:
                        status_callback("実行中...", False)
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
                    
            elif method == "click":
                x = args.get("x", 0)
                y = args.get("y", 0)
                button_str = args.get("button", "left")
                clicks = args.get("clicks", 1)
                
                btn = Button.right if button_str == "right" else Button.middle if button_str == "middle" else Button.left
                
                mouse.position = (x, y)
                time.sleep(0.05)
                mouse.click(btn, clicks)

            elif method == "hover":
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