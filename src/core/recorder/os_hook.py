# @role: Provides a facade interface for starting and stopping macro recording, invoked from external modules such as the UI.

import logging
import threading
from pynput import keyboard, mouse
from core.recorder.state import state
from core.recorder import process_monitor, screen_capturer
from core.recorder.event_processor import (
    start_key_event_worker, 
    start_mouse_event_worker, 
    stop_key_event_worker, 
    stop_mouse_event_worker, 
    run_click_process_thread
)
from core.recorder.win_scroll import start_native_scroll_hook, stop_native_scroll_hook
from core.recorder.input_listener import on_move, on_click, on_scroll, on_press, on_release
from core.recorder.log_builder import create_end_log, save_input_logs

logger = logging.getLogger(__name__)

_mouse_listener = None
_keyboard_listener = None

def set_shortcut_stop_callback(callback):
    state.shortcut_stop_callback = callback

def get_current_workflow_id() -> str | None:
    if state.recording_dirs and "macro_name" in state.recording_dirs:
        return state.recording_dirs["macro_name"]
    return None

def start_recording():
    global _mouse_listener, _keyboard_listener

    if state.is_recording:
        logger.warning("Already recording.")
        return

    try:
        state.recording_dirs = screen_capturer.make_directory()
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

        process_monitor.start_process_monitors()
        state.is_recording = True

        start_key_event_worker()
        start_mouse_event_worker()
        start_native_scroll_hook()

        _mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
        _keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        
        _mouse_listener.start()
        _keyboard_listener.start()

        logger.info("Recording started successfully. Macro: %s", state.recording_dirs['macro_name'])

    except Exception as e:
        state.is_recording = False
        state.is_stopping = False
        stop_native_scroll_hook()
        stop_mouse_event_worker()
        stop_key_event_worker()
        process_monitor.stop_process_monitors()
        logger.error("Failed to start recording: %s", e, exc_info=True)
        raise

def stop_recording():
    global _mouse_listener, _keyboard_listener

    if not state.is_recording:
        return

    try:
        state.is_recording = False
        state.cancel_hover()

        with state.pending_click_lock:
            if state.pending_click_timer:
                state.pending_click_timer.cancel()
            pending_event = state.pending_click_event
            state.pending_click_timer = None
            state.pending_click_event = None

        if pending_event is not None:
            run_click_process_thread(pending_event, input_type="mouse_click", click_count=1)

        # 1. リスナーの停止を非同期かつ安全に行い、デッドロックを防止する
        def safe_stop_listeners(m_list, k_list):
            try:
                if m_list and m_list.running:
                    m_list.stop()
                if k_list and k_list.running:
                    k_list.stop()
            except Exception as le:
                logger.error("Error occurred while stopping pynput listeners: %s", le)

        stop_thread = threading.Thread(target=safe_stop_listeners, args=(_mouse_listener, _keyboard_listener), daemon=True)
        stop_thread.start()
        
        _mouse_listener = None
        _keyboard_listener = None

        stop_native_scroll_hook()
        stop_key_event_worker()
        stop_mouse_event_worker()

        # 2. 最大5秒のタイムアウトを設け、非同期クリック処理が異常終了した場合の無限ループ（アプリ完全停止）を防止
        max_wait_cycles = 500  # 0.01s * 500 = 5.0s
        wait_cycle = 0
        while state.is_click_processing and wait_cycle < max_wait_cycles:
            threading.Event().wait(0.01)
            wait_cycle += 1
            
        if wait_cycle >= max_wait_cycles:
            logger.warning("Click processing wait timed out. Proceeding to finalize logs to prevent application freeze.")

        # 最後にスクリーンショットを撮ってEndログを記録
        end_img, _ = screen_capturer.take_screenshot()
        end_ref = screen_capturer.save_pre_image_from_pil(event_no="End", img=end_img)
        diff_str = "100%"
        with state.previous_screenshot_lock:
            if state.previous_screenshot_img is not None:
                diff_str = screen_capturer.calculate_diff_percent(state.previous_screenshot_img, end_img)

        state.append_log(create_end_log(diff_str, end_ref))
        process_monitor.stop_process_monitors()
        save_input_logs()

        logger.info("Recording stopped successfully. End image saved as: %s", end_ref)

    except Exception as e:
        logger.error("Error occurred while stopping the recording session: %s", e, exc_info=True)
        raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger.info("Starting recording. Press Ctrl + \\ to stop.")
    start_recording()
    
    # メインスレッドの無限フリーズを避けるため、タイムアウト付きで待機する
    if _keyboard_listener:
        _keyboard_listener.join(timeout=10.0)