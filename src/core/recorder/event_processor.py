# Role: キューに積まれたキーボード入力・UIA・Officeイベントの非同期処理と、全体のワーカー起動・停止管理を担当する。

import threading
import traceback
import queue
from pathlib import Path

from core.recorder import screen_capturer, window_inspector
from core.recorder.state import state
from core.recorder.utils import now_datetime, make_combo_text
from core.recorder.log_builder import build_base_log
from core.recorder.app_inspectors.inspector_factory import InspectorFactory

# マウスイベント処理系を re-export して既存の外部参照互換性を完全保証
from core.recorder.mouse_event_handler import (
    DOUBLE_CLICK_INTERVAL_SEC,
    DOUBLE_CLICK_MAX_DISTANCE,
    DRAG_MIN_DISTANCE,
    calculate_and_update_diff,
    process_move_event,
    process_click_event,
    run_click_process_thread,
    process_drag_event,
    run_drag_process_thread,
    is_same_click,
    process_pending_single_click,
    process_scroll_event,
    handle_mouse_event,
    mouse_event_worker,
    start_mouse_event_worker,
    stop_mouse_event_worker,
)

def _get_app_context(window_info: dict, x: int | None = None, y: int | None = None) -> dict | None:
    """該当アプリケーション専用のインスペクターがあれば、それを実行してコンテキストを取得する"""
    context = None
    inspector = InspectorFactory.get_inspector(window_info)
    if inspector:
        try:
            context = inspector.inspect(window_info, x=x, y=y)
        except Exception as e:
            print(f"Inspector error: {e}")

    # インスペクターが無い、またはテキストが取得できなかった場合のフォールバック
    if not context or not context.get("text"):
        try:
            from core.recorder.uia_scanner import get_focused_element_info
            focused_info = get_focused_element_info()
            if focused_info:
                if context is None:
                    context = {
                        "app": "GenericUIA",
                        "element_name": focused_info.get("name", ""),
                        "control_type": focused_info.get("control_type", ""),
                        "text": focused_info.get("value", "") or focused_info.get("name", ""),
                        "value": focused_info.get("value", ""),
                    }
                else:
                    context["text"] = focused_info.get("value", "") or focused_info.get("name", "")
                    context["value"] = focused_info.get("value", "")
                    if not context.get("element_name"):
                        context["element_name"] = focused_info.get("name", "")
                    if not context.get("control_type"):
                        context["control_type"] = focused_info.get("control_type", "")
        except Exception as e:
            print(f"UIA fallback error: {e}")

    return context


def enqueue_key_event(
    input_type: str,
    key_text: str,
    keys: list[str] | None = None,
    capture_now: bool = False,
    ime_active: bool = False,
):
    window_info = window_inspector.get_foreground_window_info()
    if window_inspector.should_ignore_window(window_info.get("title")):
        return

    event_no = state.get_next_event_no()
    dt = now_datetime()

    pre_img, pre_monitor = None, None
    if capture_now:
        pre_img, pre_monitor = screen_capturer.take_screenshot()

    state.key_event_queue.put({
        "event_no": event_no,
        "datetime": dt,
        "input_type": input_type,
        "key": key_text,
        "keys": keys or [],
        "window_info": window_info,
        "pre_img": pre_img,
        "pre_ref": None,
        "pre_monitor": pre_monitor,
        "ime_active": ime_active,
    })


def process_key_event(event: dict):
    if window_inspector.should_ignore_window(event["window_info"].get("title")):
        return

    input_type = event["input_type"]

    # text_field_search 等のメタイベントもログの集約に必須なため return で無視せず記録する
    event_no, dt = event["event_no"], event["datetime"]

    if input_type in ["text_field_search", "text_candidate_confirm"]:
        content = {"key": event["key"], "ime_active": event.get("ime_active", False)}
    else:
        content = (
            {"keys": event["keys"], "combo": make_combo_text(event["keys"])}
            if input_type == "key_combo"
            else {"key": event["key"]}
        )
        content["ime_active"] = event.get("ime_active", False)

    app_context = _get_app_context(event["window_info"])
    log = build_base_log(
        event_no=event_no,
        dt=dt,
        input_type=input_type,
        content=content,
        window_info=event["window_info"],
        app_specific_context=app_context,
    )

    pre_img = event.get("pre_img")
    pre_ref = event.get("pre_ref")

    # 背景: リスナーから同期取得された画像オブジェクトが渡されている場合でも、ファイルとしてディスクに保存する
    if pre_img is not None:
        if not pre_ref:
            pre_ref = screen_capturer.save_pre_image_from_pil(event_no=event_no, img=pre_img)
    else:
        pre_img, _ = screen_capturer.take_screenshot()
        pre_ref = screen_capturer.save_pre_image_from_pil(event_no=event_no, img=pre_img)

    diff_str = calculate_and_update_diff(pre_img)

    log["Images"] = {"Pre": pre_ref, "Crop": None, "Diff": diff_str}
    state.append_log(log)
    print(f"キー入力ログ追加: evt_{event_no}, type={input_type}, diff={diff_str}, ime={content['ime_active']}")

    # 画像変化率によるタイピングバッファの動的フラッシュ処理
    # 変化率が一定（5.0%）を超えた場合、UIの大幅な更新や画面遷移が起きたと判断し文字入力を確定させる
    if getattr(state, "typing_buffer", "") and input_type not in ["text_field_search", "text_candidate_confirm"]:
        diff_val = float(diff_str.replace("%", "")) if diff_str.replace("%", "").replace(".", "").isdigit() else 0.0
        if diff_val > 5.0:
            from core.recorder.input_listener import _flush_typing_buffer
            _flush_typing_buffer("diff_exceeded")


def process_uia_event(info: dict):
    """Tabキー押下時などに取得されたUIAの情報をログに記録する"""
    try:
        if "error" in info:
            print(f"UIA Error: {info['error']}")
            return

        window_info = window_inspector.get_foreground_window_info()
        if window_inspector.should_ignore_window(window_info.get("title")):
            return

        event_no = state.get_next_event_no()
        dt = now_datetime()

        content = {"uia_info": info, "action": "uia_scan"}
        log = build_base_log(
            event_no=event_no,
            dt=dt,
            input_type="uia_scan",
            content=content,
            window_info=window_info,
        )
        state.append_log(log)
        print(f"UIAログ追加: evt_{event_no}, name={info.get('name')}")
    except Exception:
        print("UIAイベントの処理中にエラーが発生しました")
        traceback.print_exc()


def process_office_event(info: dict):
    """Excel等からCOM経由で受け取ったイベント情報をログに記録する"""
    try:
        window_info = window_inspector.get_foreground_window_info()
        if window_inspector.should_ignore_window(window_info.get("title")):
            return

        event_no = state.get_next_event_no()
        dt = now_datetime()

        content = {"office_info": info, "action": "office_event"}
        log = build_base_log(
            event_no=event_no,
            dt=dt,
            input_type="office_event",
            content=content,
            window_info=window_info,
        )
        state.append_log(log)
        print(f"Officeログ追加: evt_{event_no}, msg={info.get('message')}")
    except Exception:
        print("Officeイベントの処理中にエラーが発生しました")
        traceback.print_exc()


# Workers
def key_event_worker():
    while True:
        try:
            event = state.key_event_queue.get(timeout=0.1)
        except queue.Empty:
            if state.key_worker_stop_event.is_set():
                break
            continue

        try:
            process_key_event(event)
        except Exception:
            traceback.print_exc()
        finally:
            state.key_event_queue.task_done()


def start_key_event_worker():
    state.key_worker_stop_event.clear()
    state.key_worker_thread = threading.Thread(target=key_event_worker, daemon=True)
    state.key_worker_thread.start()

def stop_key_event_worker():
    state.key_worker_stop_event.set()
    try:
        state.key_event_queue.join()
    except Exception:
        pass
    if state.key_worker_thread:
        state.key_worker_thread.join(timeout=10)
    state.key_worker_thread = None
def mouse_event_worker():
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
