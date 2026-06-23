# @role: 生成されたワークフローをローカルで自律実行し、即時ポーリングによる最速化を実現する。

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
    """指定されたIDのマクロを読み込み、自律実行を開始する"""
    global _is_running, _stop_requested
    _is_running = True
    _stop_requested = False
    
    logger.info(f"[{workflow_id}] Starting workflow execution...")
    mouse = MouseController()
    keyboard = KeyboardController()
    
    try:
        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id
        
        integrated_path = target_dir / "integrated.json"
        workflow_path = target_dir / "workflow.json"
        
        if not integrated_path.exists() or not workflow_path.exists():
            raise FileNotFoundError(f"Missing required data files in {target_dir}")
            
        with open(integrated_path, 'r', encoding='utf-8') as f:
            integrated_data = json.load(f)
            
        with open(workflow_path, 'r', encoding='utf-8') as f:
            workflow_data = json.load(f)
            
        event_dict = {evt.get("id"): evt for evt in integrated_data if isinstance(evt, dict)}
        events = workflow_data.get("events", [])
        
        for i, event in enumerate(events):
            if _stop_requested:
                logger.warning(f"[{workflow_id}] Execution aborted by user emergency stop.")
                break
                
            event_id = event.get("event_id")
            action = event.get("action", {})
            action_type = action.get("type", "unknown")
            
            logger.info(f"[{workflow_id}] Executing {event_id}: {action_type}")
            
            # --- マウスクリックの実行 ---
            if action_type == "click":
                integrated_evt = event_dict.get(event_id)
                if not integrated_evt:
                    continue
                    
                window = integrated_evt.get("window", {})
                coords = window.get("coordinates", {"x": 0, "y": 0})
                win_x, win_y = coords.get("x", 0), coords.get("y", 0)
                
                uis = window.get("UIs", [])
                if not uis:
                    continue
                    
                ui_element = uis[0]
                ui_action = ui_element.get("action", {})
                rel_coords = ui_action.get("cursorRelativeCoordinates", {"x": 0, "y": 0})
                rel_x, rel_y = rel_coords.get("x", 0), rel_coords.get("y", 0)
                
                target_x = win_x + rel_x
                target_y = win_y + rel_y
                
                time.sleep(0.5) # クリック前の待機（人間らしさ/画面遷移待ち）
                
                button_str = action.get("button", "left")
                btn = Button.right if button_str == "right" else Button.left
                
                mouse.position = (target_x, target_y)
                time.sleep(0.05)
                mouse.click(btn, 1)
                
            # --- キーボード入力の実行 ---
            elif action_type == "key_down":
                # workflow.json の semantic_role から入力キーを取得 ("k", "Key.enter" など)
                key_str = event.get("context", {}).get("interacted_element", {}).get("semantic_role", "")
                
                if key_str:
                    time.sleep(0.1) # タイピング間の自然なディレイ
                    if str(key_str).startswith("Key."):
                        # 特殊キーの処理 (例: "Key.enter" -> Key.enter)
                        key_name = key_str.split(".")[1]
                        try:
                            special_key = getattr(Key, key_name)
                            keyboard.press(special_key)
                            keyboard.release(special_key)
                        except AttributeError:
                            logger.warning(f"Unknown special key: {key_str}")
                    else:
                        # 通常の文字入力
                        keyboard.type(key_str)
                
        logger.info(f"[{workflow_id}] Workflow execution finished successfully.")
        
    except Exception as e:
        logger.error(f"[{workflow_id}] Execution failed: {e}")
        raise
    finally:
        _is_running = False
        _stop_requested = False

def stop_workflow():
    """実行中のマクロに対して緊急停止（キルスイッチ）シグナルを送る"""
    global _stop_requested
    _stop_requested = True
    logger.warning("Emergency stop signal activated by user.")