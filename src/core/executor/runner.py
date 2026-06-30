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

def run_workflow(workflow_id: str, config: AppConfig):
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

        # To resolve variables safely
        variables = {}
        if variables_path.exists():
            try:
                with open(variables_path, 'r', encoding='utf-8') as f:
                    variables = json.load(f)
                logger.info(f"[{workflow_id}] Loaded variables.json successfully.")
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
            
            if method == "wait":
                duration = args.get("duration", 0.0)
                # 緊急停止（stop_workflow）に即座に反応できるよう、細かく分割してスリープ
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
                time.sleep(0.05) # 移動直後の入力を安定させるための微小ウェイト
                mouse.click(btn, clicks)

            elif method == "hover":
                x = args.get("x", 0)
                y = args.get("y", 0)
                
                mouse.position = (x, y)
                time.sleep(0.5) # ホバー後、UI（ドロップダウン等）が展開されるのを待つ
                
            elif method == "scroll":
                dx = args.get("dx", 0.0)
                dy = args.get("dy", 0.0)
                x = args.get("x")
                y = args.get("y")
                
                # スクロール対象の要素（特定のサイドバー等）を正確に狙うため、実行前にマウス位置を復元する
                if x is not None and y is not None and (x != 0 or y != 0):
                    mouse.position = (x, y)
                    time.sleep(0.01) # 移動直後のウェイト
                
                if platform.system() == "Windows":
                    # pynputの内部補正を回避し、Windows API (mouse_event) を直接叩いてネイティブなスクロール量を再現する
                    if dy != 0.0:
                        # 縦スクロール (1.0 = 120, -1.0 = -120)
                        scroll_amount = int(dy * WHEEL_DELTA)
                        ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, scroll_amount, 0)
                    if dx != 0.0:
                        # 横スクロール
                        scroll_amount_x = int(dx * WHEEL_DELTA)
                        ctypes.windll.user32.mouse_event(MOUSEEVENTF_HWHEEL, 0, 0, scroll_amount_x, 0)
                else:
                    mouse.scroll(dx, dy)
                    
            elif method == "type_text":
                text = args.get("text", "")
                if text:
                    # To apply variables to the text
                    for key, val in variables.items():
                        placeholder = f"{{{{{key}}}}}"
                        if placeholder in text:
                            text = text.replace(placeholder, str(val))
                    keyboard.type(text)
                    
            elif method == "press_key":
                key_str = args.get("key", "")
                if key_str:
                    try:
                        # Map special key strings (e.g., cmd, enter) to pynput Key enum
                        key_name = key_str.lower()
                        # pynputではWindowsキーは'cmd'として扱う
                        if key_name in ["win", "windows"]:
                            key_name = "cmd"
                            
                        if hasattr(Key, key_name):
                            special_key = getattr(Key, key_name)
                            keyboard.press(special_key)
                            keyboard.release(special_key)
                        else:
                            # Fallback for normal character keys sent to press_key by mistake
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