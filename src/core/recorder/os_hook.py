# @role: モジュール外部（UI等）から呼び出される記録開始・停止のインターフェースを提供するファサード。

from pynput import keyboard, mouse
import threading
import traceback
from core.recorder.state import state
from core.recorder import process_monitor, screen_capturer
from core.recorder.event_processor import start_key_event_worker, start_mouse_event_worker, stop_key_event_worker, stop_mouse_event_worker, run_click_process_thread
from core.recorder.win_scroll import start_native_scroll_hook, stop_native_scroll_hook
from core.recorder.input_listener import on_move, on_click, on_scroll, on_press, on_release
from core.recorder.log_builder import create_end_log, save_input_logs

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
        print("すでに記録中です")
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

        print("記録を開始しました")
        print(f"macro: {state.recording_dirs['macro_name']}")
        print(f"native_scroll_hook: {state.native_scroll_hook_active}")

    except Exception:
        state.is_recording = False
        state.is_stopping = False
        stop_native_scroll_hook()
        stop_mouse_event_worker()
        stop_key_event_worker()
        process_monitor.stop_process_monitors()
        print("記録開始に失敗しました")
        traceback.print_exc()
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

        # 背景: pynputの停止時にエラーが発生しても処理が止まらないようにtry-exceptで保護
        try:
            if _mouse_listener:
                _mouse_listener.stop()
                _mouse_listener = None
            if _keyboard_listener:
                _keyboard_listener.stop()
                _keyboard_listener = None
        except Exception as e:
            print(f"リスナー停止中にエラー（無視して続行します）: {e}")

        stop_native_scroll_hook()
        stop_key_event_worker()
        stop_mouse_event_worker()

        # 背景: クリック処理がスタックして無限ループ（完全フリーズ）になるのを防ぐため、最大5秒のタイムアウトを設ける
        max_wait_cycles = 500
        wait_cycles = 0
        while state.is_click_processing and wait_cycles < max_wait_cycles:
            threading.Event().wait(0.01)
            wait_cycles += 1
            
        if wait_cycles >= max_wait_cycles:
            print("警告: クリック処理待ちがタイムアウトしました。強制的に停止プロセスを続行します。")

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

        print("記録を停止しました")
        print("recording_end: evt_End_pre.png")

    except Exception:
        print("記録停止中にエラーが発生しました")
        traceback.print_exc()
        raise

if __name__ == "__main__":
    print("記録を開始します")
    print("終了するには Ctrl + \\ キーを押してください")
    start_recording()
    if _keyboard_listener:
        # メインスレッドのブロック回避のためタイムアウト付きjoinを推奨
        _keyboard_listener.join(timeout=10.0)