# @role: モジュール外部（UI等）から呼び出される記録開始・停止のインターフェースを提供するファサード。

import logging
import threading
from pynput import keyboard, mouse

from core.recorder import log_builder, macro_path_manager, mouse_event_handler, process_monitor
from core.recorder.event_processor import (
    process_office_event,
    process_uia_event,
    start_key_event_worker,
    start_mouse_event_worker,
    stop_key_event_worker,
    stop_mouse_event_worker,
)
from core.recorder.input_listener import on_click, on_move, on_press, on_release, on_scroll
from core.recorder.key_hook import KeyHookManager
from core.recorder.office_monitor import OfficeMonitorManager
from core.recorder.state import state
from core.recorder.utils import now_datetime
from core.recorder.win_scroll import start_native_scroll_hook, stop_native_scroll_hook
from core.shared.os_env import restore_system_cursor, set_system_cursor

logger = logging.getLogger(__name__)

_mouse_listener = None
_keyboard_listener = None
_key_hook_manager = None
_office_monitor_manager = None


def set_shortcut_stop_callback(callback):
    state.shortcut_stop_callback = callback

def get_current_workflow_id() -> str | None:
    if state.recording_dirs and "macro_name" in state.recording_dirs:
        return state.recording_dirs["macro_name"]
    return None


def toggle_loop_recording() -> bool:
    """繰り返し記録モードを切り替え、メタログを追記して新しい状態を返す。"""
    if not state.is_recording:
        return False
    state.is_loop_recording = not state.is_loop_recording
    event_no = state.get_next_event_no()
    dt = now_datetime()
    window_info = {
        "title": "System_Meta",
        "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
    }
    input_type = "meta_loop_start" if state.is_loop_recording else "meta_loop_end"
    log = log_builder.build_base_log(
        event_no=event_no,
        dt=dt,
        input_type=input_type,
        content={"status": "active" if state.is_loop_recording else "inactive"},
        window_info=window_info,
    )
    state.append_log(log)
    return state.is_loop_recording


def start_recording():
    global _mouse_listener, _keyboard_listener, _key_hook_manager, _office_monitor_manager

    if state.is_recording:
        logger.warning("Already recording; ignoring duplicate start_recording call.")
        return

    try:
        state.recording_dirs = macro_path_manager.make_directory()
        state.input_logs = []
        state.event_index = 0
        state.previous_screenshot_img = None
        state.pressed_keys.clear()
        state.logged_combo_keys.clear()
        state.pending_click_event = None
        state.pending_click_timer = None
        state.latest_mouse_down_event = None
        state.is_stopping = False
        state.is_click_processing = False
        state.default_stop_callback = stop_recording
        # Why: 前回録画のループ状態をリセットし、次回録画初回トグルの誤記録を防止
        state.is_loop_recording = False

        process_monitor.start_process_monitors()
        state.is_recording = True

        start_key_event_worker()
        start_mouse_event_worker()
        start_native_scroll_hook()

        _key_hook_manager = KeyHookManager(on_tab_pressed_callback=process_uia_event)
        _office_monitor_manager = OfficeMonitorManager(callback=process_office_event)
        _key_hook_manager.start()
        _office_monitor_manager.start()

        _mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
        _keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        _mouse_listener.start()
        _keyboard_listener.start()
        set_system_cursor("record_idle")

        logger.info(
            "Recording started (macro=%s, native_scroll_hook=%s)",
            state.recording_dirs["macro_name"],
            state.native_scroll_hook_active,
        )

    except Exception as e:
        state.is_recording = False
        state.is_stopping = False
        if _key_hook_manager:
            try:
                _key_hook_manager.stop()
            except Exception as stop_err:
                logger.debug(f"KeyHookManager stop failed during cleanup: {stop_err}")
            _key_hook_manager = None
        if _office_monitor_manager:
            try:
                _office_monitor_manager.stop()
            except Exception as stop_err:
                logger.debug(f"OfficeMonitorManager stop failed during cleanup: {stop_err}")
            _office_monitor_manager = None
        stop_native_scroll_hook()
        stop_mouse_event_worker()
        stop_key_event_worker()
        process_monitor.stop_process_monitors()
        restore_system_cursor(force=True)
        logger.error(f"Failed to start recording: {e}", exc_info=True)
        raise


def stop_recording():
    global _mouse_listener, _keyboard_listener, _key_hook_manager, _office_monitor_manager

    if not state.is_recording:
        return

    try:
        state.is_recording = False
        state.cancel_hover()
        restore_system_cursor(force=True)

        mouse_event_handler.flush_incomplete_clicks_on_stop()

        try:
            if _mouse_listener:
                _mouse_listener.stop()
                _mouse_listener = None
            if _keyboard_listener:
                _keyboard_listener.stop()
                _keyboard_listener = None
            if _key_hook_manager:
                _key_hook_manager.stop()
                _key_hook_manager = None
            if _office_monitor_manager:
                _office_monitor_manager.stop()
                _office_monitor_manager = None
        except Exception as e:
            logger.warning(f"Error while stopping input listeners: {e}")

        stop_native_scroll_hook()
        stop_key_event_worker()
        stop_mouse_event_worker()

        max_wait_cycles = 500
        wait_cycles = 0
        while state.is_click_processing and wait_cycles < max_wait_cycles:
            threading.Event().wait(0.01)
            wait_cycles += 1

        if wait_cycles >= max_wait_cycles:
            logger.warning("Timed out waiting for click processing; proceeding with stop finalization.")

        log_builder.finalize_recording_logs()
        process_monitor.stop_process_monitors()
        restore_system_cursor()
        logger.info("Recording stopped and logs finalized.")

    except Exception as e:
        restore_system_cursor()
        logger.error(f"Failed to stop recording: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    print("記録を開始します")
    print("終了するには Ctrl + \\ キーを押してください")
    start_recording()
    if _keyboard_listener:
        # メインスレッドのブロック回避のためタイムアウト付きjoinを推奨
        _keyboard_listener.join(timeout=10.0)
