# src/core/generator/log_integrator.py
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
        if progress_callback:
            progress_callback(0, "初期化中... ワークフローディレクトリの確認")

        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id
        temp_dir = target_dir / "temp"
        input_logs_path = temp_dir / "input_logs.json"

        if not input_logs_path.exists():
            raise FileNotFoundError(f"Missing input_logs.json at {input_logs_path}")

        if progress_callback:
            progress_callback(2, "入力ログの読み込み中...")

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

        with ThreadPoolExecutor(max_workers=4) as cv_executor:
            for i, log_entry in enumerate(log_entries):
                if check_cancel_callback and check_cancel_callback():
                    logger.info(f"[{workflow_id}] Generation cancelled by user.")
                    raise InterruptedError("Generation cancelled by user")

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
                
                win_x = win_coord_data.get("x", 0)
                win_y = win_coord_data.get("y", 0)

                raw_type = str(log_entry.get("Type", ""))
                content_data = log_entry.get("Content") or {}
                
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
                
                button_val = "left"
                input_val = "unknown"
                
                if isinstance(content_data, dict):
                    button_val = content_data.get("button", "left")
                    input_val = content_data.get("combo") or content_data.get("key") or content_data.get("text") or f"{button_val}_click"
                else:
                    input_val = str(content_data)

                raw_type_lower = raw_type.lower()
                is_scroll = "scroll" in raw_type_lower
                is_move = "hover" in raw_type_lower or "move" in raw_type_lower
                is_click = ("click" in raw_type_lower or "mouse" in raw_type_lower) and not (is_scroll or is_move)
                is_key = "key" in raw_type_lower

                dx = 0.0
                dy = 0.0

                if is_scroll:
                    action_type = "scroll"
                    if isinstance(content_data, dict):
                        dx = content_data.get("dx", 0.0)
                        dy = content_data.get("dy", 0.0)
                elif is_move:
                    action_type = "move"
                elif is_click:
                    action_type = "click"
                elif is_key:
                    action_type = "key_down"
                else:
                    action_type = "unknown"

                if progress_callback:
                    progress = int((i / total_events) * 70)
                    action_name = action_type if action_type != "unknown" else raw_type
                    progress_callback(progress, f"画像解析中(CV)... {action_name}イベントの処理 ({i+1}/{total_events})")

                ui_type = "unknown"
                semantic_role = input_val
                context_components = []
                
                images_data = log_entry.get("Images", {})
                crop_path_str = images_data.get("Crop")

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
                        continue
                    if diff_val < 0.001:
                        continue
                
                if crop_path_str and crop_path_str != "切り抜き失敗":
                    full_crop_path = macros_root / crop_path_str
                    if full_crop_path.exists():
                        logger.info(f"[{workflow_id}] Processing CV inference: {i+1}/{total_events} (Event: {event_id})...")
                        
                        future_yolo = cv_executor.submit(detect_ui_elements, str(full_crop_path))
                        future_ocr = cv_executor.submit(read_text_from_image, str(full_crop_path))
                        
                        yolo_results = future_yolo.result()
                        ocr_results = future_ocr.result()

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
                    "diff_val": diff_val,
                    "dx": dx,
                    "dy": dy,
                    "cursor_x": cursor_x,
                    "cursor_y": cursor_y
                })

        if progress_callback:
            progress_callback(75, "入力ログの最適化... 変数候補の抽出とグループ化")

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

        if progress_callback:
            progress_callback(80, "AI推論準備(LLM)... 文脈データの構築中")
            
        llm_client = LLMClient(host=config.llm_host, port=int(config.llm_port))
        llm_enhanced_data = {}
        
        try:
            summary_for_llm = [
                {"id": info["event_id"], "ui": info["ui_type"], "text": info["semantic_role"]} 
                for info in temp_workflow_info
                if info["raw_action"] in ["click", "move"]
            ]
            
            if summary_for_llm:
                llm_prompt = (
                    "Analyze the following UI interaction sequence. "
                    "Return a JSON array where each object contains the original 'id', and an improved 'semantic_role' "
                    "based on the context of the entire sequence.\n"
                    f"{json.dumps(summary_for_llm, ensure_ascii=False)}"
                )
                
                max_retries = 3
                is_valid_response = False
                
                for attempt in range(max_retries):
                    if progress_callback:
                        retry_text = f" (再生成 {attempt}/{max_retries})" if attempt > 0 else ""
                        progress_callback(80 + attempt * 2, f"AI推論中(LLM)... UIの役割を解釈中{retry_text}")

                    logger.info(f"[{workflow_id}] Sending prompt to LLM (Attempt {attempt+1}/{max_retries})...")
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
                        
                        if progress_callback:
                            progress_callback(86 + attempt, f"AI推論の検証中(LLM)... ハルシネーション検査{retry_text}")

                        if json_start != -1 and json_end != -1:
                            try:
                                parsed_array = json.loads(content[json_start:json_end])
                                
                                if len(parsed_array) != len(summary_for_llm):
                                    raise ValueError(f"Array length mismatch. Expected {len(summary_for_llm)}, got {len(parsed_array)}")
                                
                                temp_enhanced_data = {}
                                for item in parsed_array:
                                    if "id" not in item or "semantic_role" not in item:
                                        raise ValueError("Missing 'id' or 'semantic_role' in JSON object")
                                    
                                    role = str(item["semantic_role"])
                                    temp_enhanced_data[item["id"]] = role
                                
                                llm_enhanced_data = temp_enhanced_data
                                is_valid_response = True
                                logger.info(f"[{workflow_id}] LLM inference successful and validated.")
                                break
                                
                            except json.JSONDecodeError:
                                logger.warning(f"[{workflow_id}] JSON parsing failed on attempt {attempt+1}")
                            except ValueError as ve:
                                logger.warning(f"[{workflow_id}] Validation failed on attempt {attempt+1}: {ve}")
                
                if not is_valid_response:
                    logger.warning(f"[{workflow_id}] All LLM retry attempts failed due to hallucination. Falling back to raw CV data.")
                    
        except Exception as e:
            logger.warning(f"[{workflow_id}] LLM inference encountered fatal error. Falling back to CV results. Error: {e}")

        if progress_callback:
            progress_callback(90, "ワークフロー生成中... アクションの最適化とマッピング")

        workflow_steps = []
        ui_targets_dict = {}
        
        start_time = integrated_events[0].timestamp if integrated_events else 0
        end_time = integrated_events[-1].timestamp if integrated_events else 0
        
        screen_size = Size(width=1920, height=1080)
        
        step_idx = 1
        for info in temp_workflow_info:
            raw_action = info["raw_action"]
            raw_type = info["raw_type"].lower()
            
            if raw_action == "unknown" or "recording" in raw_type:
                continue

            event_id = info["event_id"]
            fallback_evts = info.get("fallback_events", [event_id])
            
            final_semantic_role = llm_enhanced_data.get(event_id, info["semantic_role"])
            
            # --- 修正: キーボード入力は画像検索の対象から外す ---
            target_id = None
            if raw_action in ["click", "move"]:
                target_id = f"tgt_{step_idx}"
                ui_targets_dict[target_id] = {
                    "semantic_role": final_semantic_role,
                    "ui_type": info.get("ui_type", "unknown")
                }
            
            if raw_action == "click":
                cmd = "MOUSE_CLICK"
                intent = "CLICK_UI_ELEMENT"
                desc = f"Click on the {final_semantic_role} element."
                params = ActionParameters(
                    target=UniversalSelector(semantic_role=final_semantic_role),
                    button=info["button"]
                )
            elif raw_action == "move":
                cmd = "MOUSE_MOVE"
                intent = "MOVE_CURSOR"
                desc = f"Move cursor to the {final_semantic_role} element."
                params = ActionParameters(
                    target=UniversalSelector(semantic_role=final_semantic_role)
                )
            elif raw_action == "type_text":
                cmd = "TYPE_TEXT"
                intent = "INPUT_TEXT"
                desc = f"Type the text: '{final_semantic_role}'"
                params = ActionParameters(text=final_semantic_role)
            elif raw_action == "scroll":
                cmd = "MOUSE_SCROLL"
                intent = "SCROLL_WINDOW"
                desc = f"Scroll window (dx: {info['dx']}, dy: {info['dy']})"
                cursor_x = info.get("cursor_x", 0)
                cursor_y = info.get("cursor_y", 0)
                params = ActionParameters(text=f"{info['dx']},{info['dy']},{cursor_x},{cursor_y}")
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
            progress_callback(95, "ファイル出力中... integrated.json / workflow.json / ui_targets.json")

        integrated_path = target_dir / "integrated.json"
        workflow_path = target_dir / "workflow.json"
        variables_path = target_dir / "variables.json"
        ui_targets_path = target_dir / "ui_targets.json"

        with open(integrated_path, 'w', encoding='utf-8') as f:
            json.dump([evt.model_dump() for evt in integrated_events], f, indent=4, ensure_ascii=False)

        with open(workflow_path, 'w', encoding='utf-8') as f:
            f.write(workflow.model_dump_json(indent=4))
            
        with open(variables_path, 'w', encoding='utf-8') as f:
            json.dump(variables, f, indent=4, ensure_ascii=False)
            
        with open(ui_targets_path, 'w', encoding='utf-8') as f:
            json.dump(ui_targets_dict, f, indent=4, ensure_ascii=False)

        logger.info(f"[{workflow_id}] Successfully generated integrated, workflow v2.0, variables, and ui_targets.json.")

        if progress_callback:
            progress_callback(98, "実行エンジンのビルド中... executable_macro.json の決定論的生成")

        try:
            commands_data = []
            prev_timestamp = None
            
            for step in workflow_steps:
                raw_event_id = step.fallback_raw_events[0] if step.fallback_raw_events else None
                integ_evt = next((e for e in integrated_events if e.id == raw_event_id), None)
                
                if not integ_evt:
                    continue

                current_timestamp = integ_evt.timestamp
                if prev_timestamp is not None:
                    duration = (current_timestamp - prev_timestamp) / 1000.0
                    if duration > 0.05:
                        duration = min(duration, 1.5)
                        commands_data.append({
                            "method": "wait",
                            "args": {"duration": round(duration, 3)}
                        })
                prev_timestamp = current_timestamp
                
                cmd_type = step.action.command
                params = step.action.parameters
                target_id_for_healer = f"tgt_{step.step_id}"

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
                                "clicks": 1,
                                "target_id": target_id_for_healer,
                                "raw_event_id": raw_event_id
                            }
                        })
                elif cmd_type == "MOUSE_MOVE":
                    if integ_evt.window.UIs and integ_evt.window.UIs[0].action and integ_evt.window.UIs[0].action.cursorRelativeCoordinates:
                        win_c = integ_evt.window.coordinates
                        rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
                        commands_data.append({
                            "method": "move",
                            "args": {
                                "x": win_c.x + rel_c.x,
                                "y": win_c.y + rel_c.y,
                                "target_id": target_id_for_healer,
                                "raw_event_id": raw_event_id
                            }
                        })
                elif cmd_type == "MOUSE_SCROLL":
                    if params.text:
                        try:
                            parts = params.text.split(',')
                            dx_val = float(parts[0])
                            dy_val = float(parts[1])
                            x_val = float(parts[2]) if len(parts) > 2 else 0.0
                            y_val = float(parts[3]) if len(parts) > 3 else 0.0
                            commands_data.append({
                                "method": "scroll",
                                "args": {
                                    "dx": dx_val,
                                    "dy": dy_val,
                                    "x": int(x_val),
                                    "y": int(y_val)
                                }
                            })
                        except Exception:
                            pass
                elif cmd_type == "KEYBOARD_SHORTCUT":
                    if params.key:
                        commands_data.append({
                            "method": "press_key",
                            "args": {
                                "key": params.key
                            }
                        })
                elif cmd_type == "TYPE_TEXT":
                    if params.text:
                        commands_data.append({
                            "method": "type_text",
                            "args": {
                                "text": params.text
                            }
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

        if progress_callback:
            progress_callback(100, "完了")

    except Exception as e:
        logger.error(f"[{workflow_id}] Failed to generate macro workflow: {e}")
        raise