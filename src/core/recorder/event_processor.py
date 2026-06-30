# @role: キューに積まれたUI操作イベントを非同期で取り出し、スクリーンショットの差分計算・切り抜き・ログ生成を行うワーカー。

import threading
import time
import traceback
import queue
from pathlib import Path
from core.recorder import screen_capturer, process_monitor
from core.recorder.state import state
from core.recorder.utils import now_datetime, mouse_button_to_string, make_combo_text
from core.recorder.log_builder import build_base_log, build_scroll_log

DOUBLE_CLICK_INTERVAL_SEC = 0.35
DOUBLE_CLICK_MAX_DISTANCE = 8

def calculate_and_update_diff(current_img) -> str:
    with state.previous_screenshot_lock:
        if state.previous_screenshot_img is None:
            diff = "100%"
        else:
            diff = screen_capturer.calculate_diff_percent(state.previous_screenshot_img, current_img)
        state.previous_screenshot_img = current_img.copy()
    return diff

def enqueue_key_event(input_type: str, key_text: str, keys: list[str] | None = None, capture_now: bool = False):
    event_no = state.get_next_event_no()
    dt = now_datetime()
    window_info = process_monitor.get_foreground_window_info()
    state.key_event_queue.put({
        "event_no": event_no, "datetime": dt, "input_type": input_type,
        "key": key_text, "keys": keys or [], "window_info": window_info,
        "pre_img": None, "pre_ref": None,
    })

def process_key_event(event: dict):
    event_no, dt, input_type = event["event_no"], event["datetime"], event["input_type"]
    content = {"keys": event["keys"], "combo": make_combo_text(event["keys"])} if input_type == "key_combo" else {"key": event["key"]}
    log = build_base_log(event_no=event_no, dt=dt, input_type=input_type, content=content, window_info=event["window_info"])

    pre_img = event.get("pre_img") or screen_capturer.take_screenshot()[0]
    pre_ref = event.get("pre_ref") or screen_capturer.save_pre_image_from_pil(event_no=event_no, img=pre_img)
    diff = calculate_and_update_diff(pre_img)

    log["Images"] = {"Pre": pre_ref, "Crop": None, "Diff": diff}
    state.append_log(log)
    print(f"キー入力ログ追加: evt_{event_no}, type={input_type}, diff={diff}")

def process_hover_event(event: dict):
    try:
        time.sleep(0.2)
        event_no = state.get_next_event_no()
        dt = now_datetime()
        x, y = int(event["x"]), int(event["y"])
        
        window_info = process_monitor.get_foreground_window_info()
        pre_full_img, pre_monitor = screen_capturer.take_screenshot()
        pre_ref = screen_capturer.save_pre_image_from_pil(event_no=event_no, img=pre_full_img)
        
        diff_str = calculate_and_update_diff(pre_full_img)
        diff_val = float(diff_str.replace("%", "")) if diff_str.replace("%", "").replace(".", "").isdigit() else 0.0

        is_meaningless = diff_val < 0.1
        macros_root = screen_capturer.get_macros_root()

        if is_meaningless:
            old_path = macros_root / pre_ref
            if old_path.exists():
                new_name = "delete_" + old_path.name
                old_path.rename(old_path.with_name(new_name))
                pre_ref = str(Path(pre_ref).parent / new_name).replace("\\", "/")

        log = build_base_log(event_no=event_no, dt=dt, input_type="mouse_hover", content={"screen_coordinates": {"x": x, "y": y}}, window_info=window_info, cursor_x=x, cursor_y=y)
        ui_rect = process_monitor.get_ui_element_rect_at_point(x, y)

        if ui_rect is not None:
            crop = screen_capturer.save_ui_crop_by_rect(event_no=event_no, rect=ui_rect, full_img=pre_full_img, monitor=pre_monitor)
        else:
            crop = screen_capturer.save_ui_crop(event_no=event_no, click_x=x, click_y=y, full_img=pre_full_img, monitor=pre_monitor)

        crop_ref = crop.get("ui_image_ref", "切り抜き失敗")
        if is_meaningless and crop_ref != "切り抜き失敗":
            old_crop_path = macros_root / crop_ref
            if old_crop_path.exists():
                new_crop_name = "delete_" + old_crop_path.name
                old_crop_path.rename(old_crop_path.with_name(new_crop_name))
                crop_ref = str(Path(crop_ref).parent / new_crop_name).replace("\\", "/")

        log["Images"] = {"Pre": pre_ref, "Crop": crop_ref, "Diff": diff_str}
        state.append_log(log)
        print(f"ホバーログ追加: evt_{event_no}, diff={diff_str} {'(deleted)' if is_meaningless else ''}")
    except Exception:
        print("ホバー処理中にエラーが発生しました")
        traceback.print_exc()

def process_click_event(event: dict, input_type: str, click_count: int):
    try:
        event_no, dt = event["event_no"], event["datetime"]
        x, y, button = int(event["x"]), int(event["y"]), event["button"]
        pre_img, pre_monitor, pre_ref = event["pre_full_img"], event["pre_monitor"], event["pre_ref"]

        content = {"button": mouse_button_to_string(button), "click_count": int(click_count), "screen_coordinates": {"x": x, "y": y}}
        log = build_base_log(event_no=event_no, dt=dt, input_type=input_type, content=content, window_info=event["window_info"], cursor_x=x, cursor_y=y)
        diff = calculate_and_update_diff(pre_img)

        ui_rect = process_monitor.get_ui_element_rect_at_point(x, y)
        if ui_rect is not None:
            crop = screen_capturer.save_ui_crop_by_rect(event_no=event_no, rect=ui_rect, full_img=pre_img, monitor=pre_monitor)
        else:
            crop = screen_capturer.save_ui_crop(event_no=event_no, click_x=x, click_y=y, full_img=pre_img, monitor=pre_monitor)

        log["Images"] = {"Pre": pre_ref, "Crop": crop.get("ui_image_ref", "切り抜き失敗"), "Diff": diff}
        state.append_log(log)
        print(f"クリックログ追加: evt_{event_no}, type={input_type}, diff={diff}")
    except Exception:
        print("クリック処理中にエラーが発生しました")
        traceback.print_exc()
    finally:
        state.is_click_processing = False

def run_click_process_thread(event: dict, input_type: str, click_count: int):
    if state.is_click_processing:
        return
    state.is_click_processing = True
    threading.Thread(target=process_click_event, args=(event, input_type, click_count), daemon=True).start()

def is_same_click(first_event: dict | None, second_event: dict | None) -> bool:
    if first_event is None or second_event is None: return False
    if str(first_event["button"]) != str(second_event["button"]): return False
    distance_sq = (int(first_event["x"]) - int(second_event["x"]))**2 + (int(first_event["y"]) - int(second_event["y"]))**2
    return distance_sq <= DOUBLE_CLICK_MAX_DISTANCE**2

def process_pending_single_click():
    with state.pending_click_lock:
        event = state.pending_click_event
        state.pending_click_event = None
        state.pending_click_timer = None
    if event is not None:
        run_click_process_thread(event, input_type="mouse_click", click_count=1)

def _handle_mouse_event(evt: dict):
    if evt.get("type") == "hover":
        process_hover_event(evt)
        return

    x, y, button, pressed = evt["x"], evt["y"], evt["button"], evt["pressed"]

    if pressed:
        try:
            event_no, dt = state.get_next_event_no(), now_datetime()
            window_info = process_monitor.get_foreground_window_info()
            pre_full_img, pre_monitor = screen_capturer.take_screenshot()
            pre_ref = screen_capturer.save_pre_image_from_pil(event_no=event_no, img=pre_full_img)

            with state.latest_mouse_down_lock:
                state.latest_mouse_down_event = {"event_no": event_no, "datetime": dt, "x": int(x), "y": int(y), "button": button, "window_info": window_info, "pre_full_img": pre_full_img, "pre_monitor": pre_monitor, "pre_ref": pre_ref}
            print(f"mouse down取得・画像保存: evt_{event_no}, x={x}, y={y}, button={button}")
        except Exception:
            print("mouse down取得中にエラーが発生しました")
            traceback.print_exc()
        return

    with state.latest_mouse_down_lock:
        current_event = state.latest_mouse_down_event
        state.latest_mouse_down_event = None

    if current_event is None: return
    previous_event_to_process = None

    with state.pending_click_lock:
        if state.pending_click_event is not None:
            if is_same_click(state.pending_click_event, current_event):
                first_event = state.pending_click_event
                if state.pending_click_timer: state.pending_click_timer.cancel()
                state.pending_click_event = None
                state.pending_click_timer = None
                run_click_process_thread(first_event, input_type="mouse_double_click", click_count=2)
                return
            previous_event_to_process = state.pending_click_event
            if state.pending_click_timer: state.pending_click_timer.cancel()

        state.pending_click_event = current_event
        state.pending_click_timer = threading.Timer(DOUBLE_CLICK_INTERVAL_SEC, process_pending_single_click)
        state.pending_click_timer.daemon = True
        state.pending_click_timer.start()

    if previous_event_to_process is not None:
        run_click_process_thread(previous_event_to_process, input_type="mouse_click", click_count=1)

def record_scroll_event(x: int, y: int, dx: float, dy: float):
    if not state.is_recording or state.is_stopping: return
    event_no = state.get_next_event_no()
    log = build_scroll_log(event_no=event_no, dt=now_datetime(), x=int(x), y=int(y), dx=float(dx), dy=float(dy))
    state.append_log(log)
    print(f"スクロールログ追加: evt_{event_no}, dx={dx}, dy={dy}")

# Workers
def key_event_worker():
    while not state.key_worker_stop_event.is_set() or not state.key_event_queue.empty():
        try:
            event = state.key_event_queue.get(timeout=0.1)
            process_key_event(event)
            state.key_event_queue.task_done()
        except queue.Empty: continue
        except Exception: traceback.print_exc()

def start_key_event_worker():
    state.key_worker_stop_event.clear()
    state.key_worker_thread = threading.Thread(target=key_event_worker, daemon=True)
    state.key_worker_thread.start()

def stop_key_event_worker():
    state.key_worker_stop_event.set()
    try: state.key_event_queue.join()
    except Exception: pass
    if state.key_worker_thread: state.key_worker_thread.join(timeout=10)
    state.key_worker_thread = None

def mouse_event_worker():
    while not state.mouse_worker_stop_event.is_set() or not state.mouse_event_queue.empty():
        try:
            event = state.mouse_event_queue.get(timeout=0.1)
            _handle_mouse_event(event)
            state.mouse_event_queue.task_done()
        except queue.Empty: continue
        except Exception: traceback.print_exc()

def start_mouse_event_worker():
    state.mouse_worker_stop_event.clear()
    state.mouse_worker_thread = threading.Thread(target=mouse_event_worker, daemon=True)
    state.mouse_worker_thread.start()

def stop_mouse_event_worker():
    state.mouse_worker_stop_event.set()
    try: state.mouse_event_queue.join()
    except Exception: pass
    if state.mouse_worker_thread: state.mouse_worker_thread.join(timeout=10)
    state.mouse_worker_thread = None