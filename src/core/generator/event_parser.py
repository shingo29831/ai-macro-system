# Role: 生ログエントリをパースし、YOLO/OCRによるCV解析を行って統合イベント情報を生成するモジュール

import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Callable

from models.data_types import (
    IntegratedEvent, WindowContext, Size, Coordinates,
    InteractedUiElement, BoundingBox, ActionDetail,
    ContextComponent
)
from engines.yolo.detector import detect_ui_elements
from engines.ocr.reader import read_text_from_image

logger = logging.getLogger(__name__)

def parse_raw_event(log_entry: dict, i: int, total_events: int, workflow_id: str, macros_root: Path, progress_callback: Optional[Callable[[int, str], None]]) -> Optional[Dict[str, Any]]:
    """
    1件の生ログエントリをパースし、CV解析を行って統合イベント情報を返す。
    不要なシステムウィンドウ等の場合は None を返す。
    """
    if not isinstance(log_entry, dict):
        return None
        
    window_name = log_entry.get("WindowName") or "Unknown Window"
    command_line = log_entry.get("WindowCommandLine", "")
    system_windows = ["python", "unknown window", "検索", "スタート", "start", "search", "taskbar", "タスクバー", "cortana", "ジャンプ リスト"]
    if not window_name.strip() or any(sw in window_name.lower() for sw in system_windows):
        return None
        
    event_no = log_entry.get("EventNo", f"{i+1:03d}")
    event_id = f"evt_{event_no}" if not str(event_no).startswith("evt_") else str(event_no)
    
    ts_val = log_entry.get("TimeStamp", 0)
    try:
        if isinstance(ts_val, str):
            dt = datetime.fromisoformat(ts_val.replace('Z', '+00:00'))
            safe_timestamp = int(dt.timestamp() * 1000)
        else:
            safe_timestamp = int(ts_val)
    except Exception:
        safe_timestamp = 0

    win_size_data = log_entry.get("WindowSize") or {"width": 0, "height": 0}
    win_coord_data = log_entry.get("WindowCoordinates") or {"x": 0, "y": 0}
    
    win_x = win_coord_data.get("x", 0)
    win_y = win_coord_data.get("y", 0)

    raw_type = str(log_entry.get("Type", ""))
    content_data = log_entry.get("Content") or {}
    app_context = log_entry.get("AppSpecificContext") or log_entry.get("appSpecificContext") or {}
    
    raw_screen_coords = content_data.get("screen_coordinates") if isinstance(content_data, dict) else None
    if raw_screen_coords:
        cursor_x = raw_screen_coords.get("x", 0)
        cursor_y = raw_screen_coords.get("y", 0)
    else:
        cursor_coord_data = log_entry.get("CursorCoordinates") or {"x": 0, "y": 0}
        cursor_x = cursor_coord_data.get("x", 0)
        cursor_y = cursor_coord_data.get("y", 0)

    rel_x = cursor_x - win_x
    rel_y = cursor_y - win_y
    
    raw_type_lower = raw_type.lower()
    is_scroll = "scroll" in raw_type_lower
    is_move = "hover" in raw_type_lower or "move" in raw_type_lower
    is_click = ("click" in raw_type_lower or "mouse" in raw_type_lower) and not (is_scroll or is_move)
    is_key = "key" in raw_type_lower
    is_uia = "uia" in raw_type_lower
    is_meta = "meta" in raw_type_lower
    is_office = "office" in raw_type_lower

    dx = 0.0
    dy = 0.0

    if is_scroll:
        action_type = "scroll"
        if isinstance(content_data, dict):
            dx = content_data.get("dx", 0.0)
            dy = content_data.get("dy", 0.0)
    elif is_office:
        action_type = "office_event"
    elif is_meta:
        action_type = "meta"
    elif is_move:
        action_type = "move"
    elif is_click:
        action_type = "click"
    elif is_key:
        action_type = "key_down"
    elif is_uia:
        action_type = "uia_scan"
    else:
        action_type = "unknown"

    button_val = "left"
    input_val = "unknown"
    ime_active = False
    
    if isinstance(content_data, dict):
        button_val = content_data.get("button", "left")
        ime_active = content_data.get("ime_active", False)
        
        if action_type == "click":
            input_val = f"{button_val}_click"
        elif action_type == "key_down":
            input_val = content_data.get("combo") or content_data.get("key") or content_data.get("text") or "unknown_key"
        elif action_type == "scroll":
            input_val = "scroll"
        elif action_type == "move":
            input_val = "move"
        elif action_type == "uia_scan":
            input_val = content_data.get("action") or "uia_scan"
        elif action_type == "office_event":
            input_val = content_data.get("office_info", {}).get("message", "")
        else:
            input_val = content_data.get("combo") or content_data.get("key") or content_data.get("text") or "unknown"
    else:
        input_val = str(content_data)

    if progress_callback and i % max(1, total_events // 20) == 0:
        progress = 2 + int((i / total_events) * 68)
        action_name = action_type if action_type != "unknown" else raw_type
        progress_callback(progress, f"画像解析中(CV)... {action_name}イベントの処理 ({i+1}/{total_events})")

    ui_type = "unknown"
    semantic_role = input_val
    context_components = []
    
    images_data = log_entry.get("Images", {})
    crop_path_str = images_data.get("Crop")
    pre_img_path_str = images_data.get("Pre")

    raw_diff = images_data.get("Diff", "0.0%")
    try:
        if isinstance(raw_diff, str) and raw_diff.endswith("%"):
            diff_val = float(raw_diff.replace("%", "")) / 100.0
        else:
            diff_val = float(raw_diff)
    except (ValueError, TypeError):
        diff_val = 0.0

    if action_type == "move":
        if crop_path_str and "delete_" in crop_path_str:
            return None
        if diff_val < 0.05:
            return None
    
    if crop_path_str and crop_path_str != "切り抜き失敗":
        context_text = app_context.get("text") or app_context.get("value") or app_context.get("url")
        if context_text:
            logger.info(f"[{workflow_id}] Found app_specific_context for Event {event_id}. Skipping CV inference.")
            semantic_role = context_text
            ui_type = app_context.get("type", "unknown")
        else:
            full_crop_path = macros_root / crop_path_str
            if full_crop_path.exists():
                logger.info(f"[{workflow_id}] Processing CV inference: {i+1}/{total_events} (Event: {event_id})...")
                
                yolo_results = detect_ui_elements(str(full_crop_path))
                ocr_results = read_text_from_image(str(full_crop_path))

                if yolo_results:
                    best_yolo = max(yolo_results, key=lambda x: x.confidence)
                    ui_type = best_yolo.type
                
                if ocr_results:
                    best_ocr = max(ocr_results, key=lambda x: x.confidence)
                    if best_ocr.content and action_type in ["click", "move"]:
                        semantic_role = best_ocr.content
                        
                    for ocr_res in ocr_results:
                        context_components.append(ContextComponent(
                            type="text",
                            content=ocr_res.content,
                            relativeBoundingBox=ocr_res.boundingBox,
                            confidence=ocr_res.confidence,
                            parentRelevance=1.0
                        ))

    action_detail = ActionDetail(
        inputType=raw_type,
        inputValue=input_val,
        cursorRelativeCoordinates=Coordinates(x=rel_x, y=rel_y),
        diffRatio=diff_val
    )

    ui_element = InteractedUiElement(
        type=ui_type,
        relativeBoundingBox=BoundingBox(x=rel_x, y=rel_y, width=0, height=0),
        confidence=1.0,
        action=action_detail,
        context=context_components
    )

    window_context = WindowContext(
        name=window_name,
        size=Size(width=win_size_data.get("width", 0), height=win_size_data.get("height", 0)),
        coordinates=Coordinates(x=win_x, y=win_y),
        UIs=[ui_element]
    )

    integrated_event = IntegratedEvent(
        id=event_id,
        timestamp=safe_timestamp,
        window=window_context
    )

    workflow_info = {
        "event_id": event_id,
        "timestamp": safe_timestamp,
        "raw_type": raw_type,
        "raw_action": action_type,
        "button": button_val,
        "ui_type": ui_type,
        "semantic_role": semantic_role,
        "diff_val": diff_val,
        "dx": dx,
        "dy": dy,
        "cursor_x": cursor_x,
        "cursor_y": cursor_y,
        "pre_img_path": pre_img_path_str,
        "win_x": win_x,
        "win_y": win_y,
        "win_w": win_size_data.get("width", 0),
        "win_h": win_size_data.get("height", 0),
        "ime_active": ime_active,
        "app_context": app_context,
        "window_name": window_name,
        "command_line": command_line,
        "integrated_event": integrated_event
    }
    
    return workflow_info