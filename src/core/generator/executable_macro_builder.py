"""Module: @role: Workflowステップ列から実行エンジン向けのコマンド列を構築し、待機時間補完とスクロール統合を行う。"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from models.data_types import IntegratedEvent, WorkflowStep

logger = logging.getLogger(__name__)

_INVALID_UI_LABELS = {"NDLOCR未設定", "切り抜き失敗", "left_click", "move", "click"}
_NO_IMAGE_MATCH_COMMANDS = {
    "LOOP_END",
    "BROWSER_ACTION",
    "ACTIVATE_WINDOW",
    "MOUSE_SCROLL",
    "MOUSE_MOVE",
    "LOOP_START",
}


def _append_wait_if_needed(
    raw_commands_data: List[Dict[str, Any]],
    current_timestamp: int,
    prev_timestamp: Optional[int],
) -> int:
    prev_step_cmd = raw_commands_data[-1].get("method") if raw_commands_data else ""
    if prev_step_cmd == "move":
        # Why: ホバー後のドロップダウンメニュー展開アニメーション時間を最低0.5秒保証
        calc_dur = max(0.5, min((current_timestamp - (prev_timestamp or current_timestamp)) / 1000.0, 1.5))
        raw_commands_data.append({"method": "wait", "args": {"duration": round(calc_dur, 3)}})
    elif prev_timestamp is not None:
        duration = (current_timestamp - prev_timestamp) / 1000.0
        if duration > 0.01:
            raw_commands_data.append({"method": "wait", "args": {"duration": round(min(duration, 1.5), 3)}})
    return current_timestamp


def _resolve_pointer_coords(cur_info: Dict[str, Any], integ_evt: Optional[IntegratedEvent]) -> tuple[int, int]:
    # Why: UIA相対座標が欠落していても絶対座標フォールバックでクリック/移動脱落を完全防止
    px = cur_info.get("cursor_x", 0)
    py = cur_info.get("cursor_y", 0)
    if (
        integ_evt
        and integ_evt.window.UIs
        and integ_evt.window.UIs[0].action
        and integ_evt.window.UIs[0].action.cursorRelativeCoordinates
    ):
        win_c = integ_evt.window.coordinates
        rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
        px = win_c.x + rel_c.x
        py = win_c.y + rel_c.y
    return px, py


def _attach_selector_metadata(
    cmd_args: Dict[str, Any],
    elem_name: str,
    css_sel: Optional[str],
    xpath_sel: Optional[str],
    target_url: Optional[str],
    excel_dest_cell: Optional[str],
) -> None:
    if elem_name:
        cmd_args["element_name"] = elem_name
    if css_sel:
        cmd_args["selector"] = css_sel
        cmd_args["selector_type"] = "css"
    elif xpath_sel:
        cmd_args["selector"] = xpath_sel
        cmd_args["selector_type"] = "xpath"
    if target_url and str(target_url).startswith("http"):
        cmd_args["url"] = target_url
    if excel_dest_cell:
        cmd_args["excel_dest_cell"] = excel_dest_cell


def _build_browser_command(
    params: Any,
    cur_info: Dict[str, Any],
    target_id_for_healer: str,
    raw_event_id: Optional[str],
) -> Dict[str, Any]:
    elem_name_val = cur_info.get("element_name") or (cur_info.get("app_context") or {}).get("element_name") or params.text
    if elem_name_val in _INVALID_UI_LABELS:
        elem_name_val = ""
    b_act = params.browser_action or "open_url"
    b_val = params.browser_value if params.browser_value is not None else cur_info.get("value")
    b_text = params.text or (str(b_val) if b_val is not None else "")
    if b_text in _INVALID_UI_LABELS:
        b_text = ""
    # Why: select_option時のleft_click誤認を防止し選択テキストをvalueへフォールバック補完
    if b_act == "select_option" and (b_val is None or b_val == "left_click"):
        b_val = b_text if b_text and b_text != "left_click" else cur_info.get("value")

    b_args: Dict[str, Any] = {
        "action": b_act,
        "url": params.browser_url,
        "selector": params.browser_selector,
        "selector_type": params.browser_selector_type or "css",
        "text": b_text,
        "value": b_val,
        "element_name": elem_name_val,
        "timeout_sec": 10.0,
        "target_id": target_id_for_healer,
        "raw_event_id": raw_event_id,
    }
    cx = cur_info.get("cursor_x") if cur_info.get("cursor_x") is not None else cur_info.get("x")
    cy = cur_info.get("cursor_y") if cur_info.get("cursor_y") is not None else cur_info.get("y")
    if (cx is None or cy is None or cx <= 20 or cy <= 20) and params.browser_selector:
        snap_c = (cur_info.get("app_context") or {}).get("form_snapshot", {}).get("__elements__", [])
        for el in snap_c:
            if el.get("selector") == params.browser_selector and el.get("x", 0) > 20:
                cx, cy = el["x"], el["y"]
                break
    w_y = cur_info.get("win_y", -50)
    if cx is not None and cy is not None and cy >= w_y and cy > -50 and cx > 20:
        b_args["x"] = cx
        b_args["y"] = cy
    return {"method": "browser_action", "args": b_args}


def _merge_scroll_commands(raw_commands_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    commands_data: List[Dict[str, Any]] = []
    for cmd in raw_commands_data:
        if not commands_data or cmd["method"] != "scroll":
            commands_data.append(cmd)
            continue

        c_dx = cmd["args"].get("dx", 0.0)
        c_dy = cmd["args"].get("dy", 0.0)
        if c_dx == 0.0 and c_dy == 0.0:
            continue

        merged = False
        last_cmd = commands_data[-1]
        if last_cmd["method"] == "scroll":
            l_dx = last_cmd["args"].get("dx", 0.0)
            l_dy = last_cmd["args"].get("dy", 0.0)
            # Why: 同方向スクロールのみ集約し逆方向の意図的操作は相殺せず保護
            is_same_dir = (l_dy * c_dy >= 0) and (l_dx * c_dx >= 0)
            if is_same_dir and abs(last_cmd["args"]["x"] - cmd["args"]["x"]) <= 200 and abs(last_cmd["args"]["y"] - cmd["args"]["y"]) <= 300:
                last_cmd["args"]["dx"] = round(l_dx + c_dx, 2)
                last_cmd["args"]["dy"] = round(l_dy + c_dy, 2)
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
    return commands_data


def build_executable_commands(
    workflow_steps: List[WorkflowStep],
    temp_workflow_info: List[Dict[str, Any]],
    integrated_events: List[IntegratedEvent],
    check_cancel_callback: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    raw_commands_data: List[Dict[str, Any]] = []
    prev_timestamp: Optional[int] = None
    workflow_info_map = {item["event_id"]: item for item in temp_workflow_info}

    for step in workflow_steps:
        if check_cancel_callback and check_cancel_callback():
            raise InterruptedError("Generation cancelled by user")

        raw_event_id = step.fallback_raw_events[0] if step.fallback_raw_events else None
        base_id = raw_event_id.replace("_nav_click", "").split("_chk_")[0].split("_submit_")[0] if raw_event_id else None
        integ_evt = next((e for e in integrated_events if e.id in [raw_event_id, base_id]), None)

        cmd_type = step.action.command
        if not integ_evt and cmd_type not in _NO_IMAGE_MATCH_COMMANDS:
            continue

        current_timestamp = integ_evt.timestamp if integ_evt else (prev_timestamp or 0)
        prev_timestamp = _append_wait_if_needed(raw_commands_data, current_timestamp, prev_timestamp)

        params = step.action.parameters
        target_id_for_healer = f"tgt_{step.step_id}"

        if cmd_type == "MOUSE_DRAG":
            start_x, start_y = params.drag_start_x, params.drag_start_y
            end_x, end_y = params.drag_end_x, params.drag_end_y
            if None in (start_x, start_y, end_x, end_y):
                logger.warning("Skipping drag command for step %s: missing coordinates", step.step_id)
                continue
            raw_commands_data.append(
                {
                    "method": "drag",
                    "args": {
                        "start_x": int(start_x),
                        "start_y": int(start_y),
                        "end_x": int(end_x),
                        "end_y": int(end_y),
                        "button": params.button or "left",
                        "target_id": target_id_for_healer,
                        "raw_event_id": raw_event_id,
                    },
                }
            )
            continue

        if cmd_type == "ACTIVATE_WINDOW" and params.text:
            try:
                win_info = json.loads(params.text)
                raw_commands_data.append(
                    {
                        "method": "activate_window",
                        "args": {
                            "window_title": win_info.get("title", ""),
                            "x": win_info.get("x", 0),
                            "y": win_info.get("y", 0),
                            "width": win_info.get("width", 0),
                            "height": win_info.get("height", 0),
                            "launch_cmd": win_info.get("launch_cmd", ""),
                            "target_id": target_id_for_healer,
                            "raw_event_id": raw_event_id,
                            "window_alias": win_info.get("window_alias"),
                            "is_maximized": win_info.get("is_maximized", False),
                        },
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to parse ACTIVATE_WINDOW params: {e}")

        cur_info = next(
            (workflow_info_map[eid] for eid in step.fallback_raw_events if eid in workflow_info_map),
            workflow_info_map.get(raw_event_id, {}),
        )
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
            click_x, click_y = _resolve_pointer_coords(cur_info, integ_evt)
            # Why: 画面外の無効座標のみを除外（タブやメニューバーは保護）
            if click_y < -50 or click_x < -50:
                logger.info(f"Omitted off-screen click at ({click_x}, {click_y}) for event {raw_event_id}")
                continue
            cmd_args: Dict[str, Any] = {
                "x": click_x,
                "y": click_y,
                "button": params.button or "left",
                "clicks": 1,
                "target_id": target_id_for_healer,
                "raw_event_id": raw_event_id,
            }
            if cur_info.get("match_event_id"):
                cmd_args["match_event_id"] = cur_info["match_event_id"]
            _attach_selector_metadata(cmd_args, elem_name, css_sel, xpath_sel, target_url, params.excel_dest_cell)
            raw_commands_data.append({"method": "click", "args": cmd_args})
        elif cmd_type == "MOUSE_MOVE":
            move_x, move_y = _resolve_pointer_coords(cur_info, integ_evt)
            cmd_args = {
                "x": move_x,
                "y": move_y,
                "target_id": target_id_for_healer,
                "raw_event_id": raw_event_id,
            }
            _attach_selector_metadata(cmd_args, elem_name, css_sel, xpath_sel, target_url, params.excel_dest_cell)
            raw_commands_data.append({"method": "move", "args": cmd_args})
        elif cmd_type == "MOUSE_SCROLL" and params.text:
            try:
                parts = params.text.split(",")
                dx_val = float(parts[0])
                dy_val = float(parts[1])
                x_val = float(parts[2]) if len(parts) > 2 else 0.0
                y_val = float(parts[3]) if len(parts) > 3 else 0.0
                if dx_val != 0.0 or dy_val != 0.0:
                    raw_commands_data.append(
                        {
                            "method": "scroll",
                            "args": {
                                "dx": dx_val,
                                "dy": dy_val,
                                "x": int(x_val),
                                "y": int(y_val),
                                "raw_event_id": raw_event_id,
                            },
                        }
                    )
            except Exception:
                pass
        elif cmd_type == "KEYBOARD_SHORTCUT" and params.key:
            raw_commands_data.append(
                {
                    "method": "press_key",
                    "args": {
                        "key": params.key,
                        "target_id": target_id_for_healer,
                        "raw_event_id": raw_event_id,
                    },
                }
            )
        elif cmd_type == "TYPE_TEXT" and params.text:
            cmd_args = {
                "text": params.text,
                "target_id": target_id_for_healer,
                "raw_event_id": raw_event_id,
            }
            if params.sequence_value:
                cmd_args["sequence_value"] = params.sequence_value
            if params.excel_cell:
                cmd_args["excel_cell"] = params.excel_cell
            cx, cy = cur_info.get("cursor_x"), cur_info.get("cursor_y")
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
            raw_commands_data.append({"method": "type_text", "args": cmd_args})
        elif cmd_type == "LOOP_START":
            raw_commands_data.append(
                {
                    "method": "loop_start",
                    "args": {
                        "loop_count": params.loop_count or 10,
                        "loop_variables": params.loop_variables or {},
                    },
                }
            )
        elif cmd_type == "LOOP_END":
            raw_commands_data.append({"method": "loop_end", "args": {}})
        elif cmd_type == "BROWSER_ACTION":
            raw_commands_data.append(_build_browser_command(params, cur_info, target_id_for_healer, raw_event_id))
        elif cmd_type == "EXCEL_ACTION":
            raw_commands_data.append(
                {
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
                        "raw_event_id": raw_event_id,
                    },
                }
            )

    return _merge_scroll_commands(raw_commands_data)
