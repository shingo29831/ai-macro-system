"""Module: @role: 個別のWorkflowStepを実行エンジン向け単一コマンド辞書へ変換するマッパー。"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

from models.data_types import IntegratedEvent, WorkflowStep

logger = logging.getLogger(__name__)

_INVALID_UI_LABELS = {"NDLOCR未設定", "切り抜き失敗", "left_click", "move", "click"}
NO_IMAGE_MATCH_COMMANDS = {
    "LOOP_END",
    "BROWSER_ACTION",
    "ACTIVATE_WINDOW",
    "MOUSE_SCROLL",
    "MOUSE_MOVE",
    "LOOP_START",
}


def resolve_pointer_coords(cur_info: Dict[str, Any], integ_evt: Optional[IntegratedEvent]) -> Tuple[int, int]:
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


def attach_selector_metadata(
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


def build_browser_command(
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


def convert_step_to_command(
    step: WorkflowStep,
    cur_info: Dict[str, Any],
    integ_evt: Optional[IntegratedEvent],
    raw_event_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    cmd_type = step.action.command
    params = step.action.parameters
    target_id_for_healer = f"tgt_{step.step_id}"

    if cmd_type == "MOUSE_DRAG":
        start_x, start_y = params.drag_start_x, params.drag_start_y
        end_x, end_y = params.drag_end_x, params.drag_end_y
        if None in (start_x, start_y, end_x, end_y):
            logger.warning("Skipping drag command for step %s: missing coordinates", step.step_id)
            return None
        return {
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

    if cmd_type == "ACTIVATE_WINDOW" and params.text:
        try:
            win_info = json.loads(params.text)
            return {
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
        except Exception as e:
            logger.warning(f"Failed to parse ACTIVATE_WINDOW params: {e}")
            return None

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
        click_x, click_y = resolve_pointer_coords(cur_info, integ_evt)
        # Why: 画面外の無効座標のみを除外（タブやメニューバーは保護）
        if click_y < -50 or click_x < -50:
            logger.info(f"Omitted off-screen click at ({click_x}, {click_y}) for event {raw_event_id}")
            return None
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
        attach_selector_metadata(cmd_args, elem_name, css_sel, xpath_sel, target_url, params.excel_dest_cell)
        return {"method": "click", "args": cmd_args}

    if cmd_type == "MOUSE_MOVE":
        move_x, move_y = resolve_pointer_coords(cur_info, integ_evt)
        cmd_args = {
            "x": move_x,
            "y": move_y,
            "target_id": target_id_for_healer,
            "raw_event_id": raw_event_id,
        }
        attach_selector_metadata(cmd_args, elem_name, css_sel, xpath_sel, target_url, params.excel_dest_cell)
        return {"method": "move", "args": cmd_args}

    if cmd_type == "MOUSE_SCROLL" and params.text:
        try:
            parts = params.text.split(",")
            dx_val = float(parts[0])
            dy_val = float(parts[1])
            x_val = float(parts[2]) if len(parts) > 2 else 0.0
            y_val = float(parts[3]) if len(parts) > 3 else 0.0
            if dx_val != 0.0 or dy_val != 0.0:
                return {
                    "method": "scroll",
                    "args": {
                        "dx": dx_val,
                        "dy": dy_val,
                        "x": int(x_val),
                        "y": int(y_val),
                        "raw_event_id": raw_event_id,
                    },
                }
        except Exception:
            pass
        return None

    if cmd_type == "KEYBOARD_SHORTCUT" and params.key:
        return {
            "method": "press_key",
            "args": {
                "key": params.key,
                "target_id": target_id_for_healer,
                "raw_event_id": raw_event_id,
            },
        }

    if cmd_type == "TYPE_TEXT" and params.text:
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
        return {"method": "type_text", "args": cmd_args}

    if cmd_type == "LOOP_START":
        return {
            "method": "loop_start",
            "args": {
                "loop_count": params.loop_count or 10,
                "loop_variables": params.loop_variables or {},
            },
        }

    if cmd_type == "LOOP_END":
        return {"method": "loop_end", "args": {}}

    if cmd_type == "BROWSER_ACTION":
        return build_browser_command(params, cur_info, target_id_for_healer, raw_event_id)

    if cmd_type == "EXCEL_ACTION":
        return {
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

    return None
