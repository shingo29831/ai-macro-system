# @role: アクションログ（JSON）の構造構築と、記録終了時の最終データ保存処理を担う。

import json
from datetime import datetime
from core.recorder import process_monitor, screen_capturer
from core.recorder.state import state

def build_base_log(event_no: str, dt: datetime, input_type: str, content: dict | None, window_info: dict, cursor_x: int | None = None, cursor_y: int | None = None) -> dict:
    window_fields = process_monitor.build_recording_window_fields(window_info, cursor_x=cursor_x, cursor_y=cursor_y)
    return {
        "EventNo": event_no,
        "TimeStamp": dt.isoformat(),
        "Type": input_type,
        "Content": content,
        "WindowName": window_fields["WindowName"],
        "WindowSize": window_fields["WindowSize"],
        "WindowCoordinates": window_fields["WindowCoordinates"],
        "CursorCoordinates": window_fields["CursorCoordinates"],
        "Images": {"Pre": None, "Crop": None, "Diff": None},
    }

def build_scroll_log(event_no: str, dt: datetime, x: int, y: int, dx: float, dy: float) -> dict:
    point_window = process_monitor.get_window_title_at_point(x, y)
    direction = "none"
    if dy > 0: direction = "up"
    elif dy < 0: direction = "down"
    elif dx > 0: direction = "right"
    elif dx < 0: direction = "left"

    return {
        "EventNo": event_no,
        "TimeStamp": dt.isoformat(),
        "Type": "mouse_scroll",
        "Content": {
            "dx": round(float(dx), 3),
            "dy": round(float(dy), 3),
            "direction": direction,
            "screen_coordinates": {"x": int(x), "y": int(y)},
        },
        "WindowName": point_window.get("title", ""),
        "WindowSize": None,
        "WindowCoordinates": None,
        "CursorCoordinates": None,
        "Images": {"Pre": None, "Crop": None, "Diff": None},
    }

def create_end_log(diff_str: str, end_ref: str) -> dict:
    from core.recorder.utils import now_datetime
    event_no = "End"
    dt = now_datetime()
    window_info = process_monitor.get_foreground_window_info()
    window_fields = process_monitor.build_recording_window_fields(window_info, cursor_x=None, cursor_y=None)

    return {
        "EventNo": event_no,
        "TimeStamp": dt.isoformat(),
        "Type": "recording_end",
        "Content": None,
        "WindowName": window_fields["WindowName"],
        "WindowSize": window_fields["WindowSize"],
        "WindowCoordinates": None,
        "CursorCoordinates": None,
        "Images": {"Pre": end_ref, "Crop": None, "Diff": diff_str},
    }

def event_no_sort_key(log: dict):
    event_no = log.get("EventNo")
    if event_no == "End": return 999999999
    try: return int(event_no)
    except (TypeError, ValueError): return 999999998

def save_input_logs():
    temp_dir = screen_capturer.get_temp_dir()
    json_path = temp_dir / "input_logs.json"

    with state.input_logs_lock:
        logs_copy = list(state.input_logs)

    logs_copy.sort(key=event_no_sort_key)
    output = {
        "MacroName": state.recording_dirs["macro_name"] if state.recording_dirs else None,
        "Logs": logs_copy,
    }

    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2, default=str)

    print(f"input_logs.json 保存: {json_path}")