# @role: temp/ に保存された一時生データ（入力ログ・画像）とローカルAI（YOLO/OCR）の解析結果を統合し、意味を理解した実行可能なワークフローを生成する。

import json
import logging
import shutil
import os
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional

from models.data_types import (
    AppConfig, Workflow, WorkflowEvent, WorkflowAction, 
    EventContext, InteractedElementContext,
    IntegratedEvent, WindowContext, Size, Coordinates,
    InteractedUiElement, BoundingBox, ActionDetail,
    ContextComponent
)
from engines.yolo.detector import detect_ui_elements
from engines.ocr.reader import read_text_from_image

logger = logging.getLogger(__name__)

def generate_macro_workflow(
    workflow_id: str, 
    config: AppConfig, 
    progress_callback: Optional[Callable[[int, str], None]] = None,
    check_cancel_callback: Optional[Callable[[], bool]] = None
) -> None:
    logger.info(f"[{workflow_id}] Starting log integration and AI workflow generation...")
    
    try:
        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id
        temp_dir = target_dir / "temp"
        input_logs_path = temp_dir / "input_logs.json"

        if not input_logs_path.exists():
            raise FileNotFoundError(f"Missing input_logs.json at {input_logs_path}")

        with open(input_logs_path, 'r', encoding='utf-8') as f:
            raw_logs = json.load(f)

        log_entries = []
        if isinstance(raw_logs, dict) and "Logs" in raw_logs:
            log_entries = raw_logs["Logs"]
        elif isinstance(raw_logs, dict) and "source_logs" in raw_logs and "Logs" in raw_logs["source_logs"]:
            log_entries = raw_logs["source_logs"]["Logs"]
        elif isinstance(raw_logs, list):
            log_entries = raw_logs
        else:
            raise ValueError(f"Unsupported JSON structure: {type(raw_logs)}")

        integrated_events = []
        workflow_events = []
        
        total_events = len(log_entries)

        for i, log_entry in enumerate(log_entries):
            # ユーザーからのキャンセル要求をフックして即時中断
            if check_cancel_callback and check_cancel_callback():
                logger.info(f"[{workflow_id}] Generation cancelled by user.")
                raise InterruptedError("Generation cancelled by user")

            if progress_callback:
                progress = int((i / total_events) * 100)
                progress_callback(progress, f"AI解析中... ({i+1}/{total_events})")

            if not isinstance(log_entry, dict):
                continue
                
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

            window_name = log_entry.get("WindowName") or "Unknown Window"
            win_size_data = log_entry.get("WindowSize") or {"width": 0, "height": 0}
            win_coord_data = log_entry.get("WindowCoordinates") or {"x": 0, "y": 0}
            cursor_coord_data = log_entry.get("CursorCoordinates") or {"x": 0, "y": 0}

            win_x = win_coord_data.get("x", 0)
            win_y = win_coord_data.get("y", 0)
            cursor_x = cursor_coord_data.get("x", 0)
            cursor_y = cursor_coord_data.get("y", 0)

            rel_x = cursor_x - win_x
            rel_y = cursor_y - win_y

            raw_type = str(log_entry.get("Type", ""))
            content_data = log_entry.get("Content") or {}
            
            button_val = "left"
            input_val = "unknown"
            
            if isinstance(content_data, dict):
                button_val = content_data.get("button", "left")
                input_val = content_data.get("key", "") or content_data.get("text", "") or f"{button_val}_click"
            else:
                input_val = str(content_data)

            action_type = "click" if "click" in raw_type.lower() else "key_down" if "key" in raw_type.lower() else "unknown"

            ui_type = "unknown"
            semantic_role = input_val
            context_components = []
            
            images_data = log_entry.get("Images", {})
            crop_path = images_data.get("Crop")
            
            if crop_path and os.path.exists(crop_path):
                logger.info(f"[{workflow_id}] Processing AI inference: {i+1}/{total_events} (Event: {event_id})...")
                
                yolo_results = detect_ui_elements(crop_path)
                if yolo_results:
                    best_yolo = max(yolo_results, key=lambda x: x.confidence)
                    ui_type = best_yolo.type
                
                ocr_results = read_text_from_image(crop_path)
                if ocr_results:
                    best_ocr = max(ocr_results, key=lambda x: x.confidence)
                    if best_ocr.content and action_type == "click":
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
                diffRatio=0.0
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

            integrated_events.append(IntegratedEvent(
                id=event_id,
                timestamp=safe_timestamp,
                window=window_context
            ))

            action = WorkflowAction(
                type=action_type,
                button=button_val,
                modifiers=[]
            )
            
            context = EventContext(
                interacted_element=InteractedElementContext(
                    element_id=f"el_{event_id}",
                    ui_type=ui_type,
                    semantic_role=semantic_role,
                    location_context="screen"
                )
            )
            
            workflow_events.append(WorkflowEvent(
                event_id=event_id,
                timestamp=safe_timestamp,
                action=action,
                context=context
            ))

        workflow = Workflow(
            workflow_ID=workflow_id,
            target_ID="primary_application",
            events=workflow_events
        )

        if progress_callback:
            progress_callback(95, "ワークフローを保存中...")

        integrated_path = target_dir / "integrated.json"
        workflow_path = target_dir / "workflow.json"

        with open(integrated_path, 'w', encoding='utf-8') as f:
            json.dump([evt.model_dump() for evt in integrated_events], f, indent=4, ensure_ascii=False)

        with open(workflow_path, 'w', encoding='utf-8') as f:
            f.write(workflow.model_dump_json(indent=4))
            
        logger.info(f"[{workflow_id}] Successfully generated integrated.json and workflow.json with AI inference ({len(workflow_events)} events).")

        if temp_dir.exists() and temp_dir.is_dir():
            shutil.rmtree(temp_dir)
            logger.info(f"[{workflow_id}] Cleaned up temp directory.")

        if progress_callback:
            progress_callback(100, "完了")

    except Exception as e:
        logger.error(f"[{workflow_id}] Failed to generate macro workflow: {e}")
        raise