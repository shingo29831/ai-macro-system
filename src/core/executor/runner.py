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
from pathlib import Path
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key

from models.data_types import AppConfig

logger = logging.getLogger(__name__)

_is_running = False
_stop_requested = False

def run_workflow(workflow_id: str, config: AppConfig):
    global _is_running, _stop_requested
    _is_running = True
    _stop_requested = False
    
    logger.info(f"[{workflow_id}] Starting executable macro execution...")
    mouse = MouseController()
    keyboard = KeyboardController()
    
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
        _is_running = False
        _stop_requested = False

def stop_workflow():
    global _stop_requested
    _stop_requested = True
    logger.warning("Emergency stop signal activated by user.")