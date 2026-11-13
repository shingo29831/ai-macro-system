"""Module: @role: マクロエディタキャンバスにおけるループ構造解析、ID補完、整合性検証、およびアクションテンプレート生成を担当する。"""

from pathlib import Path
from typing import Any, Dict, List, Optional


def get_action_template(action_type: str) -> Dict[str, Any]:
    templates = {
        "browser_action": {"method": "browser_action", "args": {"action": "open_url", "url": "https://", "timeout_sec": 10.0}},
        "browser_open_url": {"method": "browser_action", "args": {"action": "open_url", "url": "https://", "timeout_sec": 10.0}},
        "browser_click": {"method": "browser_action", "args": {"action": "click_element", "selector": "", "timeout_sec": 10.0}},
        "browser_type": {"method": "browser_action", "args": {"action": "type_text", "selector": "", "text": "", "timeout_sec": 10.0}},
        "browser_read": {"method": "browser_action", "args": {"action": "read_text", "selector": "", "variable_name": "extracted_val", "timeout_sec": 10.0}},
        "browser_wait": {"method": "browser_action", "args": {"action": "wait_element", "selector": "", "timeout_sec": 10.0}},
        "browser_close": {"method": "browser_action", "args": {"action": "close_tab", "timeout_sec": 5.0}},
        "excel_action": {"method": "excel_action", "args": {"action": "write_cell", "cell": "A1", "value": ""}},
        "excel_write": {"method": "excel_action", "args": {"action": "write_cell", "cell": "A1", "value": ""}},
        "excel_read": {"method": "excel_action", "args": {"action": "read_cell", "cell": "A1", "variable_name": "cell_val"}},
        "excel_open": {"method": "excel_action", "args": {"action": "open_workbook", "file_path": ""}},
        "excel_save": {"method": "excel_action", "args": {"action": "save_workbook"}},
        "excel_sheet": {"method": "excel_action", "args": {"action": "select_sheet", "sheet_name": "Sheet1"}},
        "click": {"method": "click", "args": {"x": 0, "y": 0, "button": "left", "clicks": 1}},
        "move": {"method": "move", "args": {"x": 0, "y": 0}},
        "scroll": {"method": "scroll", "args": {"dx": 0.0, "dy": -5.0, "x": 0, "y": 0}},
        "type_text": {"method": "type_text", "args": {"text": ""}},
        "wait": {"method": "wait", "args": {"duration": 1.0}},
        "press_key": {"method": "press_key", "args": {"key": "enter"}},
    }
    return templates.get(action_type, {"method": action_type, "args": {}})


def validate_loops(commands: List[Dict[str, Any]]) -> bool:
    loop_dict: Dict[str, Dict[str, int]] = {}
    for i, cmd in enumerate(commands):
        method = cmd.get("method")
        if method in ["loop_start", "loop_end"]:
            loop_id = cmd.get("loop_id")
            if not loop_id:
                continue
            info = loop_dict.setdefault(loop_id, {})
            if method == "loop_start":
                info["start"] = i
            else:
                info["end"] = i

    for info in loop_dict.values():
        if "start" in info and "end" in info:
            top_idx = min(info["start"], info["end"])
            bottom_idx = max(info["start"], info["end"])
            has_action = any(
                commands[j].get("method") not in ["loop_start", "loop_end"]
                for j in range(top_idx + 1, bottom_idx)
            )
            if not has_action:
                return False
    return True


def ensure_loop_ids(commands: List[Dict[str, Any]]) -> None:
    stack: List[str] = []
    loop_counter = 0

    for cmd in commands:
        if "loop_id" in cmd:
            try:
                num = int(str(cmd["loop_id"]).split("_")[1])
                loop_counter = max(loop_counter, num + 1)
            except (IndexError, ValueError):
                pass

    for cmd in commands:
        method = cmd.get("method")
        if method == "loop_start":
            if "loop_id" not in cmd:
                cmd["loop_id"] = f"loop_{loop_counter}"
                loop_counter += 1
            stack.append(cmd["loop_id"])
        elif method == "loop_end":
            if "loop_id" not in cmd:
                if stack:
                    cmd["loop_id"] = stack.pop()
                else:
                    cmd["loop_id"] = f"loop_{loop_counter}_orphan"
                    loop_counter += 1
            elif stack and stack[-1] == cmd["loop_id"]:
                stack.pop()


def analyze_loops(commands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ensure_loop_ids(commands)

    loop_dict: Dict[str, Dict[str, Any]] = {}
    for i, cmd in enumerate(commands):
        method = cmd.get("method")
        if method in ["loop_start", "loop_end"]:
            loop_id = cmd.get("loop_id")
            if not loop_id:
                continue
            info = loop_dict.setdefault(loop_id, {"loop_count": 1})
            if method == "loop_start":
                info["loop_start_idx"] = i
                info["loop_count"] = cmd.get("args", {}).get("loop_count", 1)
            else:
                info["loop_end_idx"] = i

    loops: List[Dict[str, Any]] = []
    for info in loop_dict.values():
        if "loop_start_idx" in info and "loop_end_idx" in info:
            start_idx = info["loop_start_idx"]
            end_idx = info["loop_end_idx"]
            is_reversed = start_idx > end_idx
            top_idx = min(start_idx, end_idx)
            bottom_idx = max(start_idx, end_idx)

            first_action_idx = -1
            last_action_idx = -1
            for j in range(top_idx + 1, bottom_idx):
                if commands[j].get("method") not in ["loop_start", "loop_end"]:
                    if first_action_idx == -1:
                        first_action_idx = j
                    last_action_idx = j

            if first_action_idx != -1 and last_action_idx != -1:
                info["start_action_idx"] = first_action_idx
                info["end_action_idx"] = last_action_idx
                info["size"] = last_action_idx - first_action_idx
                info["is_reversed"] = is_reversed
                loops.append(info)

    loops.sort(key=lambda x: x["size"], reverse=True)
    placed_loops: List[Dict[str, Any]] = []
    for loop in loops:
        overlapping_lanes = [
            other["lane"]
            for other in placed_loops
            if not (loop["end_action_idx"] < other["start_action_idx"] or loop["start_action_idx"] > other["end_action_idx"])
        ]
        loop["lane"] = max(overlapping_lanes) + 1 if overlapping_lanes else 0
        placed_loops.append(loop)

    return loops


def resolve_loop_excel_columns(loop_cmd: Dict[str, Any], workflow_dir: Path) -> Optional[Dict[str, str]]:
    args = loop_cmd.setdefault("args", {})
    cols = args.get("columns_map")
    if not cols and args.get("data_source") == "excel" and args.get("file_path"):
        from .loop_widgets import get_excel_columns_map

        cols = get_excel_columns_map(args.get("file_path"), args.get("sheet_name"), workflow_dir)
        args["columns_map"] = cols
    return cols


def find_default_excel_columns(commands: List[Dict[str, Any]], workflow_dir: Path) -> Optional[Dict[str, str]]:
    for cmd in commands:
        if cmd.get("method") == "loop_start" and cmd.get("args", {}).get("data_source") == "excel" and cmd.get("args", {}).get("file_path"):
            cols = resolve_loop_excel_columns(cmd, workflow_dir)
            if cols:
                return cols
    return None
