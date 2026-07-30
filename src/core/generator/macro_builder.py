# Role: 最適化されたイベント情報からWorkflowモデルとExecutableMacroを構築し、JSONファイルとして保存するモジュール

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional

from models.data_types import (
    Workflow, WorkflowStep, WorkflowCommandAction, 
    WorkflowStepContext, ActionParameters, UniversalSelector, WorkflowMetadata,
    IntegratedEvent, Size
)

logger = logging.getLogger(__name__)

def build_and_save_macro(
    temp_workflow_info: List[Dict[str, Any]],
    integrated_events: List[IntegratedEvent],
    variables: Dict[str, str],
    workflow_id: str,
    target_dir: Path,
    check_cancel_callback: Optional[Callable[[], bool]] = None
) -> None:
    llm_enhanced_data = {} 
    workflow_steps = []
    ui_targets_dict = {}
    
    start_time = integrated_events[0].timestamp if integrated_events else 0
    end_time = integrated_events[-1].timestamp if integrated_events else 0
    
    screen_size = Size(width=1920, height=1080)
    
    step_idx = 1
    prev_window_name = None
    prev_win_rect = None
    
    window_alias_map = {}
    app_alias_counters = {}

    def get_window_alias(title, rect):
        app_name = title.split("—")[-1].split("-")[-1].strip()
        if not app_name:
            app_name = "App"
        key = (title, rect)
        if key not in window_alias_map:
            app_alias_counters[app_name] = app_alias_counters.get(app_name, 0) + 1
            window_alias_map[key] = f"{app_name}{app_alias_counters[app_name]}"
        return window_alias_map[key]
    
    for info in temp_workflow_info:
        if check_cancel_callback and check_cancel_callback():
            raise InterruptedError("Generation cancelled by user")
            
        raw_action = info["raw_action"]
        raw_type = info["raw_type"].lower()
        
        if raw_action in ["unknown", "uia_scan"] or "recording" in raw_type:
            continue

        event_id = info["event_id"]
        fallback_evts = info.get("fallback_events", [event_id])
        
        current_window = next((e.window.name for e in integrated_events if e.id == event_id), "Unknown")
        
        if raw_type == "meta_loop_start":
            loop_vars = info.get("loop_variables", {"y_offset": 30})
            workflow_steps.append(WorkflowStep(
                step_id=step_idx,
                intent="START_LOOP",
                description="Start repeating actions.",
                context=WorkflowStepContext(active_window_name=current_window),
                action=WorkflowCommandAction(
                    command="LOOP_START",
                    parameters=ActionParameters(loop_count=10, loop_variables=loop_vars)
                ),
                fallback_raw_events=[event_id]
            ))
            step_idx += 1
            continue
            
        elif raw_type == "meta_loop_end":
            workflow_steps.append(WorkflowStep(
                step_id=step_idx,
                intent="END_LOOP",
                description="End repeating actions.",
                context=WorkflowStepContext(active_window_name=current_window),
                action=WorkflowCommandAction(
                    command="LOOP_END",
                    parameters=ActionParameters()
                ),
                fallback_raw_events=[event_id]
            ))
            step_idx += 1
            continue

        if current_window != "Unknown":
            win_ctx = next((e.window for e in integrated_events if e.id == event_id), None)
            win_x = win_ctx.coordinates.x if win_ctx else info.get("win_x", 0)
            win_y = win_ctx.coordinates.y if win_ctx else info.get("win_y", 0)
            win_w = win_ctx.size.width if win_ctx else info.get("win_w", 0)
            win_h = win_ctx.size.height if win_ctx else info.get("win_h", 0)
            
            current_win_rect = (win_x, win_y, win_w, win_h)
            
            needs_activation = False
            if current_window != prev_window_name:
                needs_activation = True
            elif prev_win_rect:
                px, py, pw, ph = prev_win_rect
                if abs(win_x - px) > 10 or abs(win_y - py) > 10 or abs(win_w - pw) > 10 or abs(win_h - ph) > 10:
                    needs_activation = True
                    
            if needs_activation:
                command_line = info.get("command_line", "")
                win_info_json = json.dumps({
                    "title": current_window,
                    "x": win_x,
                    "y": win_y,
                    "width": win_w,
                    "height": win_h,
                    "launch_cmd": command_line,
                    "window_alias": get_window_alias(current_window, current_win_rect)
                }, ensure_ascii=False)

                workflow_steps.append(WorkflowStep(
                    step_id=step_idx,
                    intent="ACTIVATE_WINDOW",
                    description=f"Activate and resize window: {current_window}",
                    context=WorkflowStepContext(active_window_name=current_window),
                    action=WorkflowCommandAction(
                        command="ACTIVATE_WINDOW",
                        parameters=ActionParameters(text=win_info_json)
                    ),
                    fallback_raw_events=[event_id]
                ))
                step_idx += 1
                prev_window_name = current_window
                prev_win_rect = current_win_rect
        
        final_semantic_role = llm_enhanced_data.get(event_id, info["semantic_role"])
        
        target_id = None
        if raw_action in ["click", "move", "type_text", "key_down"]:
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
            if "sequence_value" in info:
                params.sequence_value = info["sequence_value"]
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
            elif role_lower in ["enter", "space", "tab", "esc", "backspace", "delete", "shift", "ctrl", "alt", "cmd", "win", "windows", "up", "down", "left", "right"] or "+" in role_lower:
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

        if "excel_cell" in info:
            params.excel_cell = info["excel_cell"]
        if "excel_dest_cell" in info:
            params.excel_dest_cell = info["excel_dest_cell"]

        step_context = WorkflowStepContext(
            active_window_name=current_window
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

    raw_commands_data = []
    prev_timestamp = None
    
    for step in workflow_steps:
        if check_cancel_callback and check_cancel_callback():
            raise InterruptedError("Generation cancelled by user")
            
        raw_event_id = step.fallback_raw_events[0] if step.fallback_raw_events else None
        integ_evt = next((e for e in integrated_events if e.id == raw_event_id), None)
        
        cmd_type = step.action.command
        
        if not integ_evt and cmd_type != "LOOP_END":
            continue

        current_timestamp = integ_evt.timestamp if integ_evt else prev_timestamp or 0
        if prev_timestamp is not None:
            duration = (current_timestamp - prev_timestamp) / 1000.0
            if duration > 0.01:
                duration = min(duration, 1.5)
                raw_commands_data.append({
                    "method": "wait",
                    "args": {"duration": round(duration, 3)}
                })
        prev_timestamp = current_timestamp
        
        params = step.action.parameters
        target_id_for_healer = None

        if cmd_type == "ACTIVATE_WINDOW":
            if params.text:
                try:
                    win_info = json.loads(params.text)
                    window_title = win_info.get("title", "")
                    win_x = win_info.get("x", 0)
                    win_y = win_info.get("y", 0)
                    win_w = win_info.get("width", 0)
                    win_h = win_info.get("height", 0)
                    launch_cmd = win_info.get("launch_cmd", "")
                    
                    raw_commands_data.append({
                        "method": "activate_window",
                        "args": {
                            "window_title": window_title,
                            "x": win_x,
                            "y": win_y,
                            "width": win_w,
                            "height": win_h,
                            "launch_cmd": launch_cmd,
                            "target_id": target_id_for_healer,
                            "raw_event_id": raw_event_id,
                            "window_alias": win_info.get("window_alias")
                        }
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse ACTIVATE_WINDOW params: {e}")
        elif cmd_type == "MOUSE_CLICK":
            if integ_evt.window.UIs and integ_evt.window.UIs[0].action and integ_evt.window.UIs[0].action.cursorRelativeCoordinates:
                win_c = integ_evt.window.coordinates
                rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
                cmd_args = {
                    "x": win_c.x + rel_c.x,
                    "y": win_c.y + rel_c.y,
                    "button": params.button or "left",
                    "clicks": 1,
                    "target_id": target_id_for_healer,
                    "raw_event_id": raw_event_id
                }
                if params.excel_dest_cell:
                    cmd_args["excel_dest_cell"] = params.excel_dest_cell
                raw_commands_data.append({
                    "method": "click",
                    "args": cmd_args
                })
        elif cmd_type == "MOUSE_MOVE":
            if integ_evt.window.UIs and integ_evt.window.UIs[0].action and integ_evt.window.UIs[0].action.cursorRelativeCoordinates:
                win_c = integ_evt.window.coordinates
                rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
                cmd_args = {
                    "x": win_c.x + rel_c.x,
                    "y": win_c.y + rel_c.y,
                    "target_id": target_id_for_healer,
                    "raw_event_id": raw_event_id
                }
                if params.excel_dest_cell:
                    cmd_args["excel_dest_cell"] = params.excel_dest_cell
                raw_commands_data.append({
                    "method": "move",
                    "args": cmd_args
                })
        elif cmd_type == "MOUSE_SCROLL":
            if params.text:
                try:
                    parts = params.text.split(',')
                    dx_val = float(parts[0])
                    dy_val = float(parts[1])
                    x_val = float(parts[2]) if len(parts) > 2 else 0.0
                    y_val = float(parts[3]) if len(parts) > 3 else 0.0
                    raw_commands_data.append({
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
                raw_commands_data.append({
                    "method": "press_key",
                    "args": {
                        "key": params.key,
                        "target_id": target_id_for_healer,
                        "raw_event_id": raw_event_id
                    }
                })
        elif cmd_type == "TYPE_TEXT":
            if params.text:
                cmd_args = {
                    "text": params.text,
                    "target_id": target_id_for_healer,
                    "raw_event_id": raw_event_id
                }
                if params.sequence_value:
                    cmd_args["sequence_value"] = params.sequence_value
                if params.excel_cell:
                    cmd_args["excel_cell"] = params.excel_cell
                    
                raw_commands_data.append({
                    "method": "type_text",
                    "args": cmd_args
                })
        elif cmd_type == "LOOP_START":
            raw_commands_data.append({
                "method": "loop_start",
                "args": {
                    "loop_count": params.loop_count or 10,
                    "loop_variables": params.loop_variables or {}
                }
            })
        elif cmd_type == "LOOP_END":
            raw_commands_data.append({
                "method": "loop_end",
                "args": {}
            })

    commands_data = []
    for cmd in raw_commands_data:
        if not commands_data:
            commands_data.append(cmd)
            continue
        
        if cmd["method"] == "scroll":
            merged = False
            last_cmd = commands_data[-1]
            
            if last_cmd["method"] == "scroll":
                if last_cmd["args"]["x"] == cmd["args"]["x"] and last_cmd["args"]["y"] == cmd["args"]["y"]:
                    last_cmd["args"]["dx"] = round(last_cmd["args"]["dx"] + cmd["args"]["dx"], 2)
                    last_cmd["args"]["dy"] = round(last_cmd["args"]["dy"] + cmd["args"]["dy"], 2)
                    merged = True
            elif last_cmd["method"] == "wait" and len(commands_data) >= 2:
                prev_cmd = commands_data[-2]
                if prev_cmd["method"] == "scroll":
                    if last_cmd["args"]["duration"] < 1.0 and prev_cmd["args"]["x"] == cmd["args"]["x"] and prev_cmd["args"]["y"] == cmd["args"]["y"]:
                        prev_cmd["args"]["dx"] = round(prev_cmd["args"]["dx"] + cmd["args"]["dx"], 2)
                        prev_cmd["args"]["dy"] = round(prev_cmd["args"]["dy"] + cmd["args"]["dy"], 2)
                        commands_data.pop()
                        merged = True
                        
            if not merged:
                commands_data.append(cmd)
        else:
            commands_data.append(cmd)

    exec_macro_dict = {
        "macro_id": workflow_id,
        "target_application": "auto_generated",
        "commands": commands_data
    }
    
    executable_macro_path = target_dir / "executable_macro.json"
    with open(executable_macro_path, 'w', encoding='utf-8') as f:
        json.dump(exec_macro_dict, f, indent=4, ensure_ascii=False)
        
    logger.info(f"[{workflow_id}] Successfully generated executable_macro.json deterministically.")