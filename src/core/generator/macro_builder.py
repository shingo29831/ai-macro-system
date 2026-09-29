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
    
    import re
    # Why: Web検索ページ（Google 検索等）がシェル検索窓として誤除外されるのを防止
    system_exact_windows = ["検索", "スタート", "start", "search", "タスクバー", "taskbar", "cortana", "ジャンプ リスト"]
    system_window_keywords = [
        "python", "unknown window", "マクロ生成中",
        "aiマクロ生成中", "ai macro system", "記録中", "停止中", "実行中", "設定", "ウィンドウの紐付け"
    ]

    def extract_app_name(title: str) -> str:
        if not title:
            return "App"
        parts = re.split(r"[\-—–―]", title)
        app = parts[-1].strip()
        return app if app else title.strip()

    step_idx = 1
    prev_window_name = None
    prev_win_rect = None
    
    window_alias_map = {}
    app_alias_counters = {}

    def get_window_alias(title, rect):
        app_name = extract_app_name(title)
        app_key = app_name.lower()
        # Why: 同一アプリのウィンドウ移動時（マルチモニタ間等）もエイリアスを共有し2重起動を防止
        for (reg_app, reg_rect), alias in window_alias_map.items():
            if reg_app == app_key:
                return alias
        app_alias_counters[app_name] = app_alias_counters.get(app_name, 0) + 1
        new_alias = f"{app_name}{app_alias_counters[app_name]}"
        window_alias_map[(app_key, rect)] = new_alias
        return new_alias
    
    for info in temp_workflow_info:
        if check_cancel_callback and check_cancel_callback():
            raise InterruptedError("Generation cancelled by user")
            
        raw_action = info["raw_action"]
        raw_type = info["raw_type"].lower()
        
        if raw_action in ["unknown", "uia_scan"] or "recording" in raw_type:
            continue

        event_id = info["event_id"]
        fallback_evts = info.get("fallback_events", [event_id])
        
        current_window = next((e.window.name for e in integrated_events if e.id == event_id or e.id in fallback_evts), info.get("window_name", "Unknown"))
        
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
            # Why: ブラウザ等の検索結果ページを保護し自作UI・シェル検索のみ除外
            cw_lower = current_window.lower().strip()
            is_browser_win = any(b in cw_lower for b in ["firefox", "chrome", "edge", "brave", "opera"])
            if not is_browser_win:
                if cw_lower in system_exact_windows or any(sw in cw_lower for sw in system_window_keywords):
                    continue
            elif any(sw in cw_lower for sw in system_window_keywords):
                continue

            win_ctx = next((e.window for e in integrated_events if e.id == event_id), None)
            # Why: 最適化処理(workflow_optimizer)で決定された最終ウィンドウ配置を優先しマルチモニタ移動を正しく反映
            win_x = info.get("win_x") if info.get("win_x") is not None else (win_ctx.coordinates.x if win_ctx else 0)
            win_y = info.get("win_y") if info.get("win_y") is not None else (win_ctx.coordinates.y if win_ctx else 0)
            win_w = info.get("win_w") if info.get("win_w") is not None else (win_ctx.size.width if win_ctx else 0)
            win_h = info.get("win_h") if info.get("win_h") is not None else (win_ctx.size.height if win_ctx else 0)
            is_max = info.get("is_maximized")
            if is_max is None:
                is_max = info.get("IsMaximized")
            if is_max is None and (win_x <= -8 and win_y <= -8 and win_w >= 1900):
                is_max = True
            
            current_win_rect = (win_x, win_y, win_w, win_h)
            
            curr_app = extract_app_name(current_window).lower()
            prev_app = extract_app_name(prev_window_name).lower() if prev_window_name else ""
            is_same_app = (curr_app == prev_app and curr_app != "")

            rect_changed = False
            if prev_win_rect and current_win_rect:
                px, py, pw, ph = prev_win_rect
                cx, cy, cw, ch = current_win_rect
                if abs(cx - px) > 15 or abs(cy - py) > 15 or abs(cw - pw) > 15 or abs(ch - ph) > 15:
                    rect_changed = True

            needs_activation = False
            if prev_window_name is None:
                needs_activation = True
            elif is_same_app:
                # Why: 同一アプリ移動時は別アクティベートを挿入せずリサイズ/移動のみ委譲
                needs_activation = False
            elif current_window != prev_window_name:
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
                    "window_alias": get_window_alias(current_window, current_win_rect),
                    "is_maximized": bool(is_max)
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
        
        if raw_action == "browser_action" or raw_type == "browser_action":
            cmd = "BROWSER_ACTION"
            b_act = info.get("action", "open_url")
            intent = f"BROWSER_{b_act.upper()}"
            desc = f"Execute browser action: {b_act}"
            app_ctx = info.get("app_context") or {}
            # Why: select_optionやset_checkboxの選択値をbrowser_valueフィールドへ確実にバインド
            b_val = info.get("value") if info.get("value") is not None else (info.get("text") if b_act in ["select_option", "set_checkbox"] else None)
            params = ActionParameters(
                browser_action=b_act,
                browser_url=info.get("url") or app_ctx.get("url"),
                browser_selector=info.get("selector") or app_ctx.get("css_selector") or app_ctx.get("xpath"),
                browser_selector_type=info.get("selector_type", "css"),
                text=info.get("text") or (final_semantic_role if final_semantic_role != "left_click" else ""),
                browser_value=b_val
            )
        elif raw_action == "excel_action" or raw_type == "excel_action":
            cmd = "EXCEL_ACTION"
            intent = f"EXCEL_{info.get('action', 'ACTION').upper()}"
            desc = f"Execute Excel action: {info.get('action')}"
            params = ActionParameters(
                excel_action=info.get("action"),
                excel_file_path=info.get("file_path"),
                excel_sheet=info.get("sheet_name"),
                excel_range=info.get("range_address") or info.get("cell"),
                excel_cell=info.get("cell"),
                excel_value=info.get("value"),
                excel_variable_name=info.get("variable_name"),
                excel_macro_name=info.get("macro_name")
            )
        elif raw_action == "click":
            cmd = "MOUSE_CLICK"
            intent = "CLICK_UI_ELEMENT"
            desc = f"Click on the {final_semantic_role} element."
            app_ctx = info.get("app_context") or {}
            params = ActionParameters(
                target=UniversalSelector(
                    semantic_role=final_semantic_role,
                    css_selector=app_ctx.get("css_selector"),
                    xpath=app_ctx.get("xpath"),
                    url_pattern=app_ctx.get("url")
                ),
                button=info["button"]
            )
        elif raw_action == "move":
            cmd = "MOUSE_MOVE"
            intent = "MOVE_CURSOR"
            desc = f"Move cursor to the {final_semantic_role} element."
            app_ctx = info.get("app_context") or {}
            params = ActionParameters(
                target=UniversalSelector(
                    semantic_role=final_semantic_role,
                    css_selector=app_ctx.get("css_selector"),
                    xpath=app_ctx.get("xpath"),
                    url_pattern=app_ctx.get("url")
                )
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
    workflow_info_map = {item["event_id"]: item for item in temp_workflow_info}
    
    for step in workflow_steps:
        if check_cancel_callback and check_cancel_callback():
            raise InterruptedError("Generation cancelled by user")
            
        raw_event_id = step.fallback_raw_events[0] if step.fallback_raw_events else None
        base_id = raw_event_id.replace("_nav_click", "").split("_chk_")[0].split("_submit_")[0] if raw_event_id else None
        integ_evt = next((e for e in integrated_events if e.id in [raw_event_id, base_id]), None)
        
        cmd_type = step.action.command
        
        if not integ_evt and cmd_type not in ["LOOP_END", "BROWSER_ACTION", "ACTIVATE_WINDOW"]:
            continue

        current_timestamp = integ_evt.timestamp if integ_evt else prev_timestamp or 0
        prev_step_cmd = raw_commands_data[-1].get("method") if raw_commands_data else ""
        if prev_step_cmd == "move":
            # Why: ホバー後のドロップダウンメニュー展開アニメーション時間を最低0.5秒保証
            calc_dur = max(0.5, min((current_timestamp - (prev_timestamp or current_timestamp)) / 1000.0, 1.5))
            raw_commands_data.append({
                "method": "wait",
                "args": {"duration": round(calc_dur, 3)}
            })
            prev_timestamp = current_timestamp
        elif prev_timestamp is not None:
            duration = (current_timestamp - prev_timestamp) / 1000.0
            if duration > 0.01:
                duration = min(duration, 1.5)
                raw_commands_data.append({
                    "method": "wait",
                    "args": {"duration": round(duration, 3)}
                })
            prev_timestamp = current_timestamp
        else:
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
                            "window_alias": win_info.get("window_alias"),
                            "is_maximized": win_info.get("is_maximized", False)
                        }
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse ACTIVATE_WINDOW params: {e}")
        cur_info = workflow_info_map.get(raw_event_id, {})
        app_ctx = cur_info.get("app_context") or {}
        elem_name = app_ctx.get("element_name") or cur_info.get("semantic_role") or ""
        css_sel = app_ctx.get("css_selector")
        xpath_sel = app_ctx.get("xpath")
        target_url = app_ctx.get("url")
        if not target_url or not str(target_url).startswith("http"):
            cand_url = app_ctx.get("text") or app_ctx.get("value")
            if cand_url and str(cand_url).startswith("http"):
                target_url = cand_url

        if cmd_type == "MOUSE_CLICK":
            # Why: UIA相対座標が欠落していても絶対座標フォールバックでクリック脱落を完全防止
            click_x = cur_info.get("cursor_x", 0)
            click_y = cur_info.get("cursor_y", 0)
            if integ_evt.window.UIs and integ_evt.window.UIs[0].action and integ_evt.window.UIs[0].action.cursorRelativeCoordinates:
                win_c = integ_evt.window.coordinates
                rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
                click_x = win_c.x + rel_c.x
                click_y = win_c.y + rel_c.y

            # Why: ウィンドウ枠外やタイトルバーの無効座標クリックを除外し別ウィンドウ誤操作を完全防止
            w_y = cur_info.get("win_y", 0)
            if click_y - w_y <= 45 or click_y < -50:
                logger.info(f"Omitted off-screen/title bar click at ({click_x}, {click_y}) for event {raw_event_id}")
                continue

            cmd_args = {
                "x": click_x,
                "y": click_y,
                "button": params.button or "left",
                "clicks": 1,
                "target_id": target_id_for_healer,
                "raw_event_id": raw_event_id
            }
            if cur_info.get("match_event_id"):
                cmd_args["match_event_id"] = cur_info["match_event_id"]
            if elem_name: cmd_args["element_name"] = elem_name
            if css_sel:
                cmd_args["selector"] = css_sel
                cmd_args["selector_type"] = "css"
            elif xpath_sel:
                cmd_args["selector"] = xpath_sel
                cmd_args["selector_type"] = "xpath"
            if target_url and str(target_url).startswith("http"):
                cmd_args["url"] = target_url
            if params.excel_dest_cell:
                cmd_args["excel_dest_cell"] = params.excel_dest_cell
            raw_commands_data.append({
                "method": "click",
                "args": cmd_args
            })
        elif cmd_type == "MOUSE_MOVE":
            # Why: 相対座標欠落時も絶対座標フォールバックでホバー脱落を完全防止
            move_x = cur_info.get("cursor_x", 0)
            move_y = cur_info.get("cursor_y", 0)
            if integ_evt.window.UIs and integ_evt.window.UIs[0].action and integ_evt.window.UIs[0].action.cursorRelativeCoordinates:
                win_c = integ_evt.window.coordinates
                rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
                move_x = win_c.x + rel_c.x
                move_y = win_c.y + rel_c.y
            cmd_args = {
                "x": move_x,
                "y": move_y,
                "target_id": target_id_for_healer,
                "raw_event_id": raw_event_id
            }
            if elem_name: cmd_args["element_name"] = elem_name
            if css_sel:
                cmd_args["selector"] = css_sel
                cmd_args["selector_type"] = "css"
            elif xpath_sel:
                cmd_args["selector"] = xpath_sel
                cmd_args["selector_type"] = "xpath"
            if target_url and str(target_url).startswith("http"):
                cmd_args["url"] = target_url
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
                    if dx_val != 0.0 or dy_val != 0.0:
                        raw_commands_data.append({
                            "method": "scroll",
                            "args": {
                                "dx": dx_val,
                                "dy": dy_val,
                                "x": int(x_val),
                                "y": int(y_val),
                                "raw_event_id": raw_event_id
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
                cx = cur_info.get("cursor_x")
                cy = cur_info.get("cursor_y")
                # Why: (0, 0)等の無効座標を排除し本物の入力座標のみ付与
                if cx is not None and cy is not None and (cx > 20 or cy > 20):
                    cmd_args["x"] = cx
                    cmd_args["y"] = cy
                if elem_name and elem_name != params.text and not elem_name.startswith("{{"):
                    cmd_args["element_name"] = elem_name
                if css_sel:
                    cmd_args["selector"] = css_sel
                    cmd_args["selector_type"] = "css"
                elif xpath_sel:
                    cmd_args["selector"] = xpath_sel
                    cmd_args["selector_type"] = "xpath"
                    
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
        elif cmd_type == "BROWSER_ACTION":
            elem_name_val = cur_info.get("element_name") or (cur_info.get("app_context") or {}).get("element_name")
            b_act = params.browser_action or "open_url"
            b_val = params.browser_value if params.browser_value is not None else cur_info.get("value")
            b_text = params.text or (str(b_val) if b_val is not None else "")
            # Why: select_option時のleft_click誤認を防止し選択テキストをvalueへフォールバック補完
            if b_act == "select_option" and (b_val is None or b_val == "left_click"):
                b_val = b_text if b_text and b_text != "left_click" else cur_info.get("value")
            b_args = {
                "action": b_act,
                "url": params.browser_url,
                "selector": params.browser_selector,
                "selector_type": params.browser_selector_type or "css",
                "text": b_text,
                "value": b_val,
                "element_name": elem_name_val,
                "timeout_sec": 10.0,
                "target_id": target_id_for_healer,
                "raw_event_id": raw_event_id
            }
            # Why: セレクタ特定失敗時も記録時の物理座標へ安全にフォールバック可能にする(枠外座標は除外)
            cx = cur_info.get("cursor_x")
            cy = cur_info.get("cursor_y")
            w_y = cur_info.get("win_y", 0)
            if cx is not None and cy is not None and cy >= w_y and cy > -50:
                b_args["x"] = cx
                b_args["y"] = cy
            raw_commands_data.append({
                "method": "browser_action",
                "args": b_args
            })
        elif cmd_type == "EXCEL_ACTION":
            raw_commands_data.append({
                "method": "excel_action",
                "args": {
                    "action": params.excel_action,
                    "file_path": params.excel_file_path,
                    "sheet_name": params.excel_sheet,
                    "cell": params.excel_cell,
                    "range_address": params.excel_range,
                    "value": params.excel_value,
                    "variable_name": params.excel_variable_name,
                    "macro_name": params.excel_macro_name,
                    "target_id": target_id_for_healer,
                    "raw_event_id": raw_event_id
                }
            })

    commands_data = []
    for cmd in raw_commands_data:
        if not commands_data:
            commands_data.append(cmd)
            continue
        
        if cmd["method"] == "scroll":
            c_dx = cmd["args"].get("dx", 0.0)
            c_dy = cmd["args"].get("dy", 0.0)
            if c_dx == 0.0 and c_dy == 0.0:
                continue

            merged = False
            last_cmd = commands_data[-1]
            
            if last_cmd["method"] == "scroll":
                l_dx = last_cmd["args"].get("dx", 0.0)
                l_dy = last_cmd["args"].get("dy", 0.0)
                # Why: 連続スクロールは逆方向の戻しも合算して正味移動量へ集約
                if abs(last_cmd["args"]["x"] - cmd["args"]["x"]) <= 200 and abs(last_cmd["args"]["y"] - cmd["args"]["y"]) <= 300:
                    new_dx = round(l_dx + c_dx, 2)
                    new_dy = round(l_dy + c_dy, 2)
                    if new_dx == 0.0 and new_dy == 0.0:
                        commands_data.pop()
                    else:
                        last_cmd["args"]["dx"] = new_dx
                        last_cmd["args"]["dy"] = new_dy
                        if not last_cmd["args"].get("raw_event_id") and cmd["args"].get("raw_event_id"):
                            last_cmd["args"]["raw_event_id"] = cmd["args"]["raw_event_id"]
                    merged = True
            elif last_cmd["method"] == "wait" and len(commands_data) >= 2:
                prev_cmd = commands_data[-2]
                if prev_cmd["method"] == "scroll":
                    p_dx = prev_cmd["args"].get("dx", 0.0)
                    p_dy = prev_cmd["args"].get("dy", 0.0)
                    # Why: スクロール間の短時間待機も挟んだ戻しスクロールを正味量へ集約
                    if last_cmd["args"]["duration"] < 1.2 and abs(prev_cmd["args"]["x"] - cmd["args"]["x"]) <= 200 and abs(prev_cmd["args"]["y"] - cmd["args"]["y"]) <= 300:
                        new_dx = round(p_dx + c_dx, 2)
                        new_dy = round(p_dy + c_dy, 2)
                        commands_data.pop()
                        if new_dx == 0.0 and new_dy == 0.0:
                            commands_data.pop()
                        else:
                            prev_cmd["args"]["dx"] = new_dx
                            prev_cmd["args"]["dy"] = new_dy
                            if not prev_cmd["args"].get("raw_event_id") and cmd["args"].get("raw_event_id"):
                                prev_cmd["args"]["raw_event_id"] = cmd["args"]["raw_event_id"]
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