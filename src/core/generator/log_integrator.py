# @role: temp/ に保存された一時生データ（入力ログ・画像）とローカルAI（YOLO/OCR/LLM）の解析結果を統合し、意味を理解した実行可能なワークフローを生成する。

import json
import logging
import shutil
import os
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor

from models.data_types import (
    AppConfig, Workflow, WorkflowEvent, WorkflowAction, 
    EventContext, InteractedElementContext,
    IntegratedEvent, WindowContext, Size, Coordinates,
    InteractedUiElement, BoundingBox, ActionDetail,
    ContextComponent
)
from engines.yolo.detector import detect_ui_elements
from engines.ocr.reader import read_text_from_image
from engines.llm.client import LLMClient

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
        temp_workflow_info: List[Dict[str, Any]] = []
        
        total_events = len(log_entries)

        for i, log_entry in enumerate(log_entries):
            if check_cancel_callback and check_cancel_callback():
                logger.info(f"[{workflow_id}] Generation cancelled by user.")
                raise InterruptedError("Generation cancelled by user")

            if progress_callback:
                progress = int((i / total_events) * 80)
                progress_callback(progress, f"AI解析中(CV)... ({i+1}/{total_events})")

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
                logger.info(f"[{workflow_id}] Processing CV inference: {i+1}/{total_events} (Event: {event_id})...")
                
                # YOLOとOCRの推論APIリクエストを並列化し、直列実行による遅延を防止
                with ThreadPoolExecutor(max_workers=2) as executor:
                    future_yolo = executor.submit(detect_ui_elements, crop_path)
                    future_ocr = executor.submit(read_text_from_image, crop_path)
                    
                    yolo_results = future_yolo.result()
                    ocr_results = future_ocr.result()

                if yolo_results:
                    best_yolo = max(yolo_results, key=lambda x: x.confidence)
                    ui_type = best_yolo.type
                
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

            temp_workflow_info.append({
                "event_id": event_id,
                "timestamp": safe_timestamp,
                "raw_action": action_type,
                "button": button_val,
                "ui_type": ui_type,
                "semantic_role": semantic_role
            })

        # N+1問題を防止するため、LLMには全イベントの要約を一括で送信し意味解析を実行
        if progress_callback:
            progress_callback(85, "AI解析中(LLM)...")
            
        llm_client = LLMClient(host=config.llm_host, port=int(config.llm_port))
        llm_enhanced_data = {}
        
        try:
            summary_for_llm = [{"id": info["event_id"], "ui": info["ui_type"], "text": info["semantic_role"]} for info in temp_workflow_info]
            llm_prompt = (
                "Analyze the following UI interaction sequence. "
                "Return a JSON array where each object contains the original 'id', and an improved 'semantic_role' "
                "based on the context of the entire sequence.\n"
                f"{json.dumps(summary_for_llm, ensure_ascii=False)}"
            )
            
            # --- 追加: LLMへ送信するプロンプトのログ ---
            logger.info(f"[{workflow_id}] Sending prompt to LLM:\n{llm_prompt}")
            
            llm_response = llm_client.generate(prompt=llm_prompt)
            
            # --- 追加: LLMからの生レスポンスのログ ---
            logger.info(f"[{workflow_id}] Raw LLM Response:\n{json.dumps(llm_response, indent=2, ensure_ascii=False)}")
            
            if llm_response and isinstance(llm_response, dict) and llm_response.get("success"):
                resp_data = llm_response.get("response", {})
                
                # llama_cpp の chat_completion の構造からテキストコンテンツを抽出
                content = ""
                if isinstance(resp_data, dict) and "choices" in resp_data and len(resp_data["choices"]) > 0:
                    content = resp_data["choices"][0].get("message", {}).get("content", "")
                elif isinstance(resp_data, str):
                    content = resp_data
                    
                # --- 追加: 抽出したテキストのログ ---
                logger.info(f"[{workflow_id}] Extracted LLM Content:\n{content}")
                
                # JSON部分の抽出（プレーンテキストに混ざっている場合を考慮）
                json_start = content.find('[')
                json_end = content.rfind(']') + 1
                if json_start != -1 and json_end != -1:
                    parsed_array = json.loads(content[json_start:json_end])
                    for item in parsed_array:
                        if "id" in item and "semantic_role" in item:
                            llm_enhanced_data[item["id"]] = item["semantic_role"]
                else:
                    logger.warning(f"[{workflow_id}] Could not find JSON array in LLM output.")
        except Exception as e:
            logger.warning(f"[{workflow_id}] LLM inference failed or returned invalid format. Falling back to CV results. Error: {e}")

        # LLMの解析結果を結合して最終的なWorkflowEventを構築
        workflow_events = []
        for info in temp_workflow_info:
            event_id = info["event_id"]
            final_semantic_role = llm_enhanced_data.get(event_id, info["semantic_role"])
            
            action = WorkflowAction(
                type=info["raw_action"],
                button=info["button"],
                modifiers=[]
            )
            
            context = EventContext(
                interacted_element=InteractedElementContext(
                    element_id=f"el_{event_id}",
                    ui_type=info["ui_type"],
                    semantic_role=final_semantic_role,
                    location_context="screen"
                )
            )
            
            workflow_events.append(WorkflowEvent(
                event_id=event_id,
                timestamp=info["timestamp"],
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