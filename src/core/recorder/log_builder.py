# @role: アクションログ（JSON）の構造構築と、記録終了時の最終データ保存処理を担う。

import json
import logging
from datetime import datetime
from core.recorder import macro_path_manager, process_monitor, screen_capturer
from core.recorder.state import state
from core.shared.app_log_profile import (
    detect_app_profile,
    resolve_log_channels,
    sanitize_context_for_profile,
)
from core.shared.os_env import get_wheel_scroll_settings
from core.shared.screen_capture import calculate_diff_percent, take_screenshot

logger = logging.getLogger(__name__)

def build_base_log(event_no: str, dt: datetime, input_type: str, content: dict | None, window_info: dict, cursor_x: int | None = None, cursor_y: int | None = None, app_specific_context: dict | None = None) -> dict:
    window_fields = process_monitor.build_recording_window_fields(window_info, cursor_x=cursor_x, cursor_y=cursor_y)
    profile_source = {
        "process_name": window_fields.get("ProcessName") or window_info.get("process_name", ""),
        "command_line": window_fields.get("WindowCommandLine") or window_info.get("command_line", ""),
        "title": window_fields.get("WindowName", ""),
        "Type": input_type,
        "AppSpecificContext": app_specific_context,
    }
    app_profile = detect_app_profile(profile_source)
    sanitized_ctx = sanitize_context_for_profile(app_profile, app_specific_context, source_info=profile_source)
    primary_channel, active_channels = resolve_log_channels(
        input_type, app_profile, sanitized_ctx, source=profile_source
    )
    return {
        "EventNo": event_no,
        "TimeStamp": dt.isoformat(),
        "Type": input_type,
        "Content": content,
        "WindowName": window_fields["WindowName"],
        "ProcessName": window_fields.get("ProcessName") or window_info.get("process_name", ""),
        "WindowCommandLine": window_fields.get("WindowCommandLine") or window_info.get("command_line", ""),
        "AppProfile": app_profile.value,
        "LogChannel": primary_channel.value,
        "ActiveChannels": active_channels,
        "WindowSize": window_fields["WindowSize"],
        "WindowCoordinates": window_fields["WindowCoordinates"],
        "CursorCoordinates": window_fields["CursorCoordinates"],
        "AppSpecificContext": sanitized_ctx,
        "Images": {"Pre": None, "Crop": None, "Diff": None},
    }

def build_scroll_log(event_no: str, dt: datetime, x: int, y: int, dx: float, dy: float, point_window: dict | None = None) -> dict:
    if point_window is None or not point_window.get("title"):
        point_window = process_monitor.get_window_title_at_point(x, y)
    if not point_window or not point_window.get("title"):
        point_window = process_monitor.get_foreground_window_info()

    window_fields = process_monitor.build_recording_window_fields(point_window, cursor_x=x, cursor_y=y)
    profile_source = {
        "process_name": window_fields.get("ProcessName") or (point_window or {}).get("process_name", ""),
        "command_line": window_fields.get("WindowCommandLine") or (point_window or {}).get("command_line", ""),
        "title": window_fields.get("WindowName", ""),
        "Type": "mouse_scroll",
    }
    app_profile = detect_app_profile(profile_source)
    primary_channel, active_channels = resolve_log_channels(
        "mouse_scroll", app_profile, None, source=profile_source
    )

    direction = "none"
    if dy > 0: direction = "up"
    elif dy < 0: direction = "down"
    elif dx > 0: direction = "right"
    elif dx < 0: direction = "left"

    # Why: 先頭でcore.shared.os_envから取得済みのため存在しない旧モジュール参照を除去
    scroll_settings = get_wheel_scroll_settings()

    return {
        "EventNo": event_no,
        "TimeStamp": dt.isoformat(),
        "Type": "mouse_scroll",
        "Content": {
            "dx": round(float(dx), 3),
            "dy": round(float(dy), 3),
            "direction": direction,
            "screen_coordinates": {"x": int(x), "y": int(y)},
            "scroll_lines": scroll_settings.get("scroll_lines", 3),
            "is_page_scroll": scroll_settings.get("is_page_scroll", False),
        },
        "WindowName": window_fields["WindowName"],
        "ProcessName": window_fields.get("ProcessName") or (point_window or {}).get("process_name", ""),
        "WindowCommandLine": window_fields.get("WindowCommandLine") or (point_window or {}).get("command_line", ""),
        "AppProfile": app_profile.value,
        "LogChannel": primary_channel.value,
        "ActiveChannels": active_channels,
        "WindowSize": window_fields["WindowSize"],
        "WindowCoordinates": window_fields["WindowCoordinates"],
        "CursorCoordinates": window_fields["CursorCoordinates"],
        "AppSpecificContext": None,
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
        "AppSpecificContext": None,
        "Images": {"Pre": end_ref, "Crop": None, "Diff": diff_str},
    }

def event_no_sort_key(log: dict):
    event_no = log.get("EventNo")
    if event_no == "End": return 999999999
    try: return int(event_no)
    except (TypeError, ValueError): return 999999998

def save_input_logs():
    temp_dir = macro_path_manager.get_temp_dir()
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

    logger.info(f"Saved input_logs.json: {json_path}")


def finalize_recording_logs() -> None:
    """録画終了時のスクリーンショット撮影・Endログ追記・input_logs.json永続化を実行する。"""
    end_img, _ = take_screenshot()
    end_ref = screen_capturer.save_pre_image_from_pil(event_no="End", img=end_img)
    diff_str = "100%"
    with state.previous_screenshot_lock:
        if state.previous_screenshot_img is not None:
            diff_str = calculate_diff_percent(state.previous_screenshot_img, end_img)

    state.append_log(create_end_log(diff_str, end_ref))
    save_input_logs()
