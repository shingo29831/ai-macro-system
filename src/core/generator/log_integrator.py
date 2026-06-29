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
    AppConfig, Workflow, WorkflowStep, WorkflowCommandAction, 
    WorkflowStepContext, ActionParameters, UniversalSelector, WorkflowMetadata,
    IntegratedEvent, WindowContext, Size, Coordinates,
    InteractedUiElement, BoundingBox, ActionDetail,
    ContextComponent
)
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

            raw_type_lower = raw_type.lower()
            is_scroll = "scroll" in raw_type_lower
            is_click = ("click" in raw_type_lower or "mouse" in raw_type_lower) and not is_scroll
            is_key = "key" in raw_type_lower

            if is_scroll:
                action_type = "scroll"
            elif is_click:
                action_type = "click"
            elif is_key:
                action_type = "key_down"
            else:
                action_type = "unknown"

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
                "raw_type": raw_type,
                "raw_action": action_type,
                "button": button_val,
                "ui_type": ui_type,
                "semantic_role": semantic_role,
                "diff_val": diff_val
            })

        # --- 変数抽出ロジック（連続する文字入力で全体的にDiffが低いものをグループ化） ---
        variables = {}
        processed_info = []
        current_group = []

        def flush_group():
            if not current_group:
                return
            if len(current_group) == 1:
                processed_info.append(current_group[0])
                current_group.clear()
                return

            avg_diff = sum(item["diff_val"] for item in current_group) / len(current_group)
            
            # 平均diffが30%未満の場合は連続する文字列入力（変数候補）とみなす
            if avg_diff < 0.3:
                text = "".join([str(item["semantic_role"]) for item in current_group])
                var_name = f"search_query_{len(variables) + 1}"
                variables[var_name] = text
                
                rep = current_group[0].copy()
                rep["semantic_role"] = f"{{{{{var_name}}}}}"
                rep["raw_action"] = "type_text"
                rep["fallback_events"] = [item["event_id"] for item in current_group]
                processed_info.append(rep)
            else:
                processed_info.extend(current_group)
            
            current_group.clear()

        for info in temp_workflow_info:
            if info["raw_action"] == "key_down":
                role_lower = str(info["semantic_role"]).lower() if info["semantic_role"] else ""
                is_special = False
                if role_lower.startswith("key.") or role_lower in ["enter", "space", "tab", "esc", "backspace", "delete", "shift", "ctrl", "alt", "cmd", "win", "windows", "up", "down", "left", "right"]:
                    is_special = True
                
                if not is_special:
                    current_group.append(info)
                else:
                    flush_group()
                    processed_info.append(info)
            else:
                flush_group()
                processed_info.append(info)
                
        flush_group()
        temp_workflow_info = processed_info

        # --- LLM推論フェーズ ---
        if progress_callback:
            progress_callback(85, "AI解析中(LLM)...")
            
        llm_client = LLMClient(host=config.llm_host, port=int(config.llm_port))
        llm_enhanced_data = {}
        
        try:
            # クリック操作のみをLLMに推論させる（キー入力や変数をLLMのハルシネーションで上書きさせないため）
            summary_for_llm = [
                {"id": info["event_id"], "ui": info["ui_type"], "text": info["semantic_role"]} 
                for info in temp_workflow_info
                if info["raw_action"] == "click"
            ]
            
            if summary_for_llm:
                llm_prompt = (
                    "Analyze the following UI interaction sequence. "
                    "Return a JSON array where each object contains the original 'id', and an improved 'semantic_role' "
                    "based on the context of the entire sequence.\n"
                    f"{json.dumps(summary_for_llm, ensure_ascii=False)}"
                )
                
                logger.info(f"[{workflow_id}] Sending prompt to LLM:\n{llm_prompt}")
                
                llm_response = llm_client.generate(prompt=llm_prompt)
                
                if llm_response and isinstance(llm_response, dict) and llm_response.get("success"):
                    resp_data = llm_response.get("response", {})
                    
                    content = ""
                    if isinstance(resp_data, dict) and "choices" in resp_data and len(resp_data["choices"]) > 0:
                        content = resp_data["choices"][0].get("message", {}).get("content", "")
                    elif isinstance(resp_data, str):
                        content = resp_data
                        
                    json_start = content.find('[')
                    json_end = content.rfind(']') + 1
                    if json_start != -1 and json_end != -1:
                        parsed_array = json.loads(content[json_start:json_end])
                        for item in parsed_array:
                            if "id" in item and "semantic_role" in item:
                                llm_enhanced_data[item["id"]] = item["semantic_role"]
        except Exception as e:
            logger.warning(f"[{workflow_id}] LLM inference failed or returned invalid format. Falling back to CV results. Error: {e}")

        # === ワークフロー(Omnipotent Workflow)の構築 ===
        workflow_steps = []
        start_time = integrated_events[0].timestamp if integrated_events else 0
        end_time = integrated_events[-1].timestamp if integrated_events else 0
        
        screen_size = Size(width=1920, height=1080)
        
        step_idx = 1
        for info in temp_workflow_info:
            raw_action = info["raw_action"]
            raw_type = info["raw_type"].lower()
            
            if raw_action in ["unknown", "scroll"] or "recording" in raw_type:
                continue

            event_id = info["event_id"]
            fallback_evts = info.get("fallback_events", [event_id])
            final_semantic_role = llm_enhanced_data.get(event_id, info["semantic_role"])
            
            if raw_action == "click":
                cmd = "MOUSE_CLICK"
                intent = "CLICK_UI_ELEMENT"
                desc = f"Click on the {final_semantic_role} element."
                params = ActionParameters(
                    target=UniversalSelector(semantic_role=final_semantic_role),
                    button=info["button"]
                )
            elif raw_action == "type_text":
                cmd = "TYPE_TEXT"
                intent = "INPUT_TEXT"
                desc = f"Type the text: '{final_semantic_role}'"
                params = ActionParameters(text=final_semantic_role)
            else:
                role_lower = final_semantic_role.lower() if final_semantic_role else ""
                is_special_key = False
                parsed_key = role_lower
                
                if role_lower.startswith("key."):
                    is_special_key = True
                    parsed_key = role_lower.replace("key.", "")
                elif role_lower in ["enter", "space", "tab", "esc", "backspace", "delete", "shift", "ctrl", "alt", "cmd", "win", "windows", "up", "down", "left", "right"]:
                    is_special_key = True

                if is_special_key:
                    cmd = "KEYBOARD_SHORTCUT"
                    intent = "PRESS_SPECIAL_KEY"
                    desc = f"Press the {parsed_key} key."
                    params = ActionParameters(key=parsed_key)
                else:
                    cmd = "TYPE_TEXT"
                    intent = "INPUT_TEXT"
                    desc = f"Type the text: '{final_semantic_role}'"
                    params = ActionParameters(text=final_semantic_role)

            step_context = WorkflowStepContext(
                active_window_name=next((e.window.name for e in integrated_events if e.id == event_id), "Unknown")
            )

            workflow_steps.append(WorkflowStep(
                step_id=step_idx,
                intent=intent,
                description=desc,
                context=step_context,
                action=WorkflowCommandAction(command=cmd, parameters=params),
                fallback_raw_events=fallback_evts
            ))
            step_idx += 1

        workflow = Workflow(
            version="2.0",
            workflow_ID=workflow_id,
            metadata=WorkflowMetadata(
                os="Windows",
                resolution=screen_size,
                duration_ms=max(0, end_time - start_time)
            ),
            steps=workflow_steps
        )

        if progress_callback:
            progress_callback(90, "ワークフローデータを保存中...")

        integrated_path = target_dir / "integrated.json"
        workflow_path = target_dir / "workflow.json"
        variables_path = target_dir / "variables.json"

        with open(integrated_path, 'w', encoding='utf-8') as f:
            json.dump([evt.model_dump() for evt in integrated_events], f, indent=4, ensure_ascii=False)

        with open(workflow_path, 'w', encoding='utf-8') as f:
            f.write(workflow.model_dump_json(indent=4))
            
        with open(variables_path, 'w', encoding='utf-8') as f:
            json.dump(variables, f, indent=4, ensure_ascii=False)

        logger.info(f"[{workflow_id}] Successfully generated integrated, workflow v2.0, and variables.json ({len(workflow_steps)} steps).")

        if progress_callback:
            progress_callback(95, "マクロ実行用コード(Executable Macro)を生成中...")

        # === Executable Macro の決定論的生成 ===
        try:
            commands_data = []
            prev_timestamp = None
            
            for step in workflow_steps:
                raw_event_id = step.fallback_raw_events[0] if step.fallback_raw_events else None
                integ_evt = next((e for e in integrated_events if e.id == raw_event_id), None)
                
                if not integ_evt:
                    continue

                # 1. 待機コマンドの生成
                current_timestamp = integ_evt.timestamp
                if prev_timestamp is not None:
                    duration = (current_timestamp - prev_timestamp) / 1000.0
                    if duration > 0.05:
                        duration = min(duration, 60.0)
                        commands_data.append({
                            "method": "wait",
                            "args": {"duration": round(duration, 3)}
                        })
                prev_timestamp = current_timestamp
                
                # 2. アクションコマンドの生成
                cmd_type = step.action.command
                params = step.action.parameters

                if cmd_type == "MOUSE_CLICK":
                    if integ_evt.window.UIs and integ_evt.window.UIs[0].action and integ_evt.window.UIs[0].action.cursorRelativeCoordinates:
                        win_c = integ_evt.window.coordinates
                        rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
                        commands_data.append({
                            "method": "click",
                            "args": {
                                "x": win_c.x + rel_c.x,
                                "y": win_c.y + rel_c.y,
                                "button": params.button or "left",
                                "clicks": 1
                            }
                        })
                elif cmd_type == "KEYBOARD_SHORTCUT":
                    if params.key:
                        commands_data.append({
                            "method": "press_key",
                            "args": {"key": params.key}
                        })
                elif cmd_type == "TYPE_TEXT":
                    if params.text:
                        commands_data.append({
                            "method": "type_text",
                            "args": {"text": params.text}
                        })

            exec_macro_dict = {
                "macro_id": workflow_id,
                "target_application": "auto_generated",
                "commands": commands_data
            }
            
            executable_macro_path = target_dir / "executable_macro.json"
            with open(executable_macro_path, 'w', encoding='utf-8') as f:
                json.dump(exec_macro_dict, f, indent=4, ensure_ascii=False)
                
            logger.info(f"[{workflow_id}] Successfully generated executable_macro.json deterministically.")

        except Exception as e:
             logger.error(f"[{workflow_id}] Error generating Executable Macro: {e}")

# 一時的にコメントアウト
        # if temp_dir.exists() and temp_dir.is_dir():
        #     shutil.rmtree(temp_dir)
        #     logger.info(f"[{workflow_id}] Cleaned up temp directory.")

        if progress_callback:
            progress_callback(100, "完了")

    except Exception as e:
        logger.error(f"[{workflow_id}] Failed to generate macro workflow: {e}")
        raise