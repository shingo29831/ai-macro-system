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
# AIにマクロ用JSONの構造（スキーマ）を提示し、型安全にパースするためインポート
from models.executable_macro import ExecutableMacro

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
            crop_path_str = images_data.get("Crop")
            
            if crop_path_str:
                full_crop_path = macros_root / crop_path_str
                if full_crop_path.exists():
                    logger.info(f"[{workflow_id}] Processing CV inference: {i+1}/{total_events} (Event: {event_id})...")
                    
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        future_yolo = executor.submit(detect_ui_elements, str(full_crop_path))
                        future_ocr = executor.submit(read_text_from_image, str(full_crop_path))
                        
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

            raw_diff = images_data.get("Diff", "0.0%")
            try:
                if isinstance(raw_diff, str) and raw_diff.endswith("%"):
                    diff_val = float(raw_diff.replace("%", "")) / 100.0
                else:
                    diff_val = float(raw_diff)
            except (ValueError, TypeError):
                diff_val = 0.0

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
            
            logger.info(f"[{workflow_id}] Sending prompt to LLM:\n{llm_prompt}")
            
            llm_response = llm_client.generate(prompt=llm_prompt)
            
            logger.info(f"[{workflow_id}] Raw LLM Response:\n{json.dumps(llm_response, indent=2, ensure_ascii=False)}")
            
            if llm_response and isinstance(llm_response, dict) and llm_response.get("success"):
                resp_data = llm_response.get("response", {})
                
                content = ""
                if isinstance(resp_data, dict) and "choices" in resp_data and len(resp_data["choices"]) > 0:
                    content = resp_data["choices"][0].get("message", {}).get("content", "")
                elif isinstance(resp_data, str):
                    content = resp_data
                    
                logger.info(f"[{workflow_id}] Extracted LLM Content:\n{content}")
                
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
            progress_callback(90, "ワークフローデータを保存中...")

        integrated_path = target_dir / "integrated.json"
        workflow_path = target_dir / "workflow.json"

        with open(integrated_path, 'w', encoding='utf-8') as f:
            json.dump([evt.model_dump() for evt in integrated_events], f, indent=4, ensure_ascii=False)

        with open(workflow_path, 'w', encoding='utf-8') as f:
            f.write(workflow.model_dump_json(indent=4))
            
        logger.info(f"[{workflow_id}] Successfully generated integrated.json and workflow.json with AI inference ({len(workflow_events)} events).")

        if progress_callback:
            progress_callback(95, "マクロ実行用コード(Executable Macro)を生成中...")

        # 生成済みの情報から、実行に必要な座標と意図だけを抽出してLLMに渡し、Executable Macroを生成させる
        try:
            schema_str = json.dumps(ExecutableMacro.model_json_schema(), ensure_ascii=False)
            
            macro_prompt_data = {
                "workflow": [
                    {
                        "event_id": e.event_id,
                        "action_type": e.action.type,
                        "button": e.action.button,
                        "semantic_role": e.context.interacted_element.semantic_role
                    } for e in workflow_events
                ],
                "integrated_data_coords": [
                    {
                        "event_id": e.id,
                        "window_coords": e.window.coordinates.model_dump(),
                        "relative_coords": e.window.UIs[0].action.cursorRelativeCoordinates.model_dump() if e.window.UIs and e.window.UIs[0].action and e.window.UIs[0].action.cursorRelativeCoordinates else None
                    } for e in integrated_events
                ]
            }

            executable_macro_prompt = (
                "You are an AI that converts macro workflow data into an Executable Macro JSON.\n"
                "Based on the following workflow intents and screen coordinates, generate a direct executable JSON.\n"
                "You MUST adhere strictly to the following JSON schema:\n"
                f"{schema_str}\n\n"
                "Input Data:\n"
                f"{json.dumps(macro_prompt_data, ensure_ascii=False)}\n\n"
                "Calculate the absolute coordinates by adding window_coords(x,y) and relative_coords(x,y).\n"
                "Output ONLY the raw valid JSON string, without any markdown formatting like ```json."
            )

            logger.info(f"[{workflow_id}] Sending Executable Macro prompt to LLM...")
            exec_macro_response = llm_client.generate(prompt=executable_macro_prompt)

            if exec_macro_response and isinstance(exec_macro_response, dict) and exec_macro_response.get("success"):
                exec_resp_data = exec_macro_response.get("response", {})
                exec_content = ""
                if isinstance(exec_resp_data, dict) and "choices" in exec_resp_data and len(exec_resp_data["choices"]) > 0:
                    exec_content = exec_resp_data["choices"][0].get("message", {}).get("content", "")
                elif isinstance(exec_resp_data, str):
                    exec_content = exec_resp_data

                exec_json_start = exec_content.find('{')
                exec_json_end = exec_content.rfind('}') + 1
                if exec_json_start != -1 and exec_json_end != -1:
                    exec_macro_json_str = exec_content[exec_json_start:exec_json_end]
                    parsed_exec_macro = ExecutableMacro.model_validate_json(exec_macro_json_str)
                    
                    executable_macro_path = target_dir / "executable_macro.json"
                    with open(executable_macro_path, 'w', encoding='utf-8') as f:
                        f.write(parsed_exec_macro.model_dump_json(indent=4))
                    logger.info(f"[{workflow_id}] Successfully generated executable_macro.json.")
                else:
                    logger.warning(f"[{workflow_id}] Could not find JSON object in LLM output for Executable Macro.")
            else:
                 logger.warning(f"[{workflow_id}] LLM failed to generate Executable Macro. Response: {exec_macro_response}")

        except Exception as e:
             logger.error(f"[{workflow_id}] Error generating Executable Macro: {e}")


        if temp_dir.exists() and temp_dir.is_dir():
            shutil.rmtree(temp_dir)
            logger.info(f"[{workflow_id}] Cleaned up temp directory.")

        if progress_callback:
            progress_callback(100, "完了")

    except Exception as e:
        logger.error(f"[{workflow_id}] Failed to generate macro workflow: {e}")
        raise