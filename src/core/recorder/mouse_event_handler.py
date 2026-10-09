"""Module: @role: マウス移動・クリック・ダブルクリック判定・ドラッグ・スクロールイベントの非同期処理とワーカーを管理する。"""

from typing import Any, Dict, Optional
import queue
import threading
import traceback

from core.recorder import screen_capturer, window_inspector
from core.recorder.mouse_action_processor import (
    calculate_and_update_diff,
    process_click_event,
    process_drag_event,
    process_move_event as _action_process_move_event,
    process_scroll_event as _action_process_scroll_event,
)
from core.recorder.state import state
from core.recorder.utils import (
    calculate_distance,
    now_datetime,
)

DOUBLE_CLICK_INTERVAL_SEC = 0.35
DOUBLE_CLICK_MAX_DISTANCE = 8
DRAG_MIN_DISTANCE = 12


def process_move_event(event: Dict[str, Any]) -> None:
    _action_process_move_event(event, flush_pending_click=process_pending_single_click)


def process_scroll_event(event: Dict[str, Any]) -> None:
    _action_process_scroll_event(event, flush_pending_click=process_pending_single_click)


def run_click_process_thread(event: Dict[str, Any], input_type: str, click_count: int) -> None:
    # Why: 前のクリック処理中であっても連続クリックを破棄せず確実に非同期記録
    threading.Thread(
        target=process_click_event, args=(event, input_type, click_count), daemon=True
    ).start()


def run_drag_process_thread(event: Dict[str, Any]) -> None:
    # Why: 前のクリック処理中であってもドラッグ操作を破棄せず確実に非同期記録
    threading.Thread(target=process_drag_event, args=(event,), daemon=True).start()


def is_same_click(first_event: Optional[Dict[str, Any]], second_event: Optional[Dict[str, Any]]) -> bool:
    if first_event is None or second_event is None:
        return False
    if str(first_event["button"]) != str(second_event["button"]):
        return False
    distance_sq = (int(first_event["x"]) - int(second_event["x"])) ** 2 + (
        int(first_event["y"]) - int(second_event["y"])
    ) ** 2
    return distance_sq <= DOUBLE_CLICK_MAX_DISTANCE**2


def process_pending_single_click(sync: bool = False) -> None:
    with state.pending_click_lock:
        event = state.pending_click_event
        timer = state.pending_click_timer
        if timer:
            timer.cancel()
        state.pending_click_event = None
        state.pending_click_timer = None
    if event is not None:
        if sync:
            process_click_event(event, input_type="mouse_click", click_count=1)
        else:
            run_click_process_thread(event, input_type="mouse_click", click_count=1)


def handle_mouse_event(evt: Dict[str, Any]) -> None:
    evt_type = evt.get("type")
    if evt_type in ["hover", "move"]:
        process_move_event(evt)
        return
    elif evt_type == "scroll":
        process_scroll_event(evt)
        return

    x, y, button, pressed = evt["x"], evt["y"], evt["button"], evt["pressed"]

    if pressed:
        try:
            window_info = window_inspector.get_foreground_window_info()
            if window_inspector.should_ignore_window(window_info.get("title")):
                return

            event_no, dt = state.get_next_event_no(), now_datetime()
            pre_full_img, pre_monitor = screen_capturer.take_screenshot()
            pre_ref = screen_capturer.save_pre_image_from_pil(event_no=event_no, img=pre_full_img)

            with state.latest_mouse_down_lock:
                state.latest_mouse_down_event = {
                    "event_no": event_no,
                    "datetime": dt,
                    "x": int(x),
                    "y": int(y),
                    "button": button,
                    "window_info": window_info,
                    "pre_full_img": pre_full_img,
                    "pre_monitor": pre_monitor,
                    "pre_ref": pre_ref,
                }
            print(f"mouse down取得・画像保存: evt_{event_no}, x={x}, y={y}, button={button}")
        except Exception:
            print("mouse down取得中にエラーが発生しました")
            traceback.print_exc()
        return

    with state.latest_mouse_down_lock:
        current_event = state.latest_mouse_down_event
        state.latest_mouse_down_event = None

    if current_event is None:
        return

    drag_distance = calculate_distance(current_event["x"], current_event["y"], x, y)

    if drag_distance >= DRAG_MIN_DISTANCE:
        with state.pending_click_lock:
            previous_click = state.pending_click_event
            if state.pending_click_timer:
                state.pending_click_timer.cancel()
            state.pending_click_event = None
            state.pending_click_timer = None

        if previous_click is not None:
            run_click_process_thread(previous_click, input_type="mouse_click", click_count=1)

        drop_window = window_inspector.get_window_title_at_point(x, y)
        drop_ui_rect = window_inspector.get_ui_element_rect_at_point(x, y)

        drag_event = {
            **current_event,
            "drop_x": x,
            "drop_y": y,
            "drag_distance": drag_distance,
            "drop_window": drop_window,
            "drop_ui_rect": drop_ui_rect,
        }

        run_drag_process_thread(drag_event)
        return

    previous_event_to_process = None

    with state.pending_click_lock:
        if state.pending_click_event is not None:
            if is_same_click(state.pending_click_event, current_event):
                first_event = state.pending_click_event
                if state.pending_click_timer:
                    state.pending_click_timer.cancel()
                state.pending_click_event = None
                state.pending_click_timer = None
                run_click_process_thread(first_event, input_type="mouse_double_click", click_count=2)
                return
            previous_event_to_process = state.pending_click_event
            if state.pending_click_timer:
                state.pending_click_timer.cancel()

        state.pending_click_event = current_event
        state.pending_click_timer = threading.Timer(DOUBLE_CLICK_INTERVAL_SEC, process_pending_single_click)
        state.pending_click_timer.daemon = True
        state.pending_click_timer.start()

    if previous_event_to_process is not None:
        run_click_process_thread(previous_event_to_process, input_type="mouse_click", click_count=1)


def mouse_event_worker() -> None:
    while True:
        try:
            event = state.mouse_event_queue.get(timeout=0.1)
        except queue.Empty:
            if state.mouse_worker_stop_event.is_set():
                break
            continue

        try:
            handle_mouse_event(event)
        except Exception:
            traceback.print_exc()
        finally:
            state.mouse_event_queue.task_done()


def start_mouse_event_worker() -> None:
    state.mouse_worker_stop_event.clear()
    state.mouse_worker_thread = threading.Thread(target=mouse_event_worker, daemon=True)
    state.mouse_worker_thread.start()


def stop_mouse_event_worker() -> None:
    state.mouse_worker_stop_event.set()
    # Why: 無期限joinによる生成停止フリーズを防止しタイムアウト合流
    if state.mouse_worker_thread:
        state.mouse_worker_thread.join(timeout=3.0)
    state.mouse_worker_thread = None
