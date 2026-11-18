# Role: キューに積まれたキーボード入力・UIA・Officeイベントの非同期処理と、全体のワーカー起動・停止管理を担当する。

import logging
import threading
import traceback
import queue

from core.recorder import screen_capturer, window_inspector
from core.recorder.ime_detector import is_ime_active
from core.recorder.mouse_action_processor import get_app_context as _shared_get_app_context
from core.recorder.state import state
from core.recorder.utils import now_datetime, make_combo_text
from core.recorder.log_builder import build_base_log
from core.shared.app_log_profile import (
    AppProfile,
    LogChannel,
    detect_app_profile,
    get_app_log_policy,
)
from core.shared.screen_capture import take_screenshot

logger = logging.getLogger(__name__)

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
    """mouse_action_processorと共通化されたアプリプロファイル対応コンテキスト取得を委譲呼び出しする"""
    return _shared_get_app_context(window_info, x=x, y=y)


def flush_typing_buffer(trigger_reason: str, window_info: dict | None = None) -> None:
    """未確定のタイピングバッファを確定メタイベントとして投入する。"""
    if not getattr(state, "typing_buffer", ""):
        return
    ime_on = is_ime_active()
    enqueue_key_event(
        "text_candidate_confirm",
        "",
        capture_now=True,
        ime_active=ime_on,
        window_info=window_info,
    )
    state.typing_buffer = ""
    logger.debug("タイピングバッファを確定しました [%s]", trigger_reason)


def enqueue_key_event(
    input_type: str,
    key_text: str,
    keys: list[str] | None = None,
    capture_now: bool = False,
    ime_active: bool = False,
    window_info: dict | None = None,
):
    # Why: フックスレッドのブロックを防止しつつ保留クリックを非同期即時確定
    process_pending_single_click(sync=False)

    if window_info is None:
        window_info = window_inspector.get_foreground_window_info()
    if window_inspector.should_ignore_window(window_info.get("title")):
        return

    event_no = state.get_next_event_no()
    dt = now_datetime()

    pre_img, pre_monitor = None, None
    if capture_now:
        pre_img, pre_monitor = take_screenshot()

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
        pre_img, _ = take_screenshot()
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
            flush_typing_buffer("diff_exceeded")


def process_uia_event(info: dict):
    """Tabキー押下時などに取得されたUIAの情報をログに記録する"""
    try:
        if "error" in info:
            print(f"UIA Error: {info['error']}")
            return

        window_info = window_inspector.get_foreground_window_info()
        if window_inspector.should_ignore_window(window_info.get("title")):
            return

        # Why: Excel操作時はCOMイベントを優先してUIA_SCANを除外し、Word等の非Excel系OfficeではUIA_SCANを許可
        app_profile = detect_app_profile(window_info)
        policy = get_app_log_policy(app_profile, source=window_info)
        if LogChannel.UIA_SCAN not in policy.allowed_channels:
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


def _find_recent_excel_window_info() -> dict | None:
    """フォーカス喪失時のOnSheetChange救出用に、直近のExcel入力イベントからウィンドウ情報を復元する。"""
    with state.input_logs_lock:
        recent_logs = list(state.input_logs[-6:])
    for prev_log in reversed(recent_logs):
        prev_type = str(prev_log.get("Type", "")).lower()
        if "office" in prev_type or "recording" in prev_type:
            continue
        if get_app_log_policy(detect_app_profile(prev_log), source=prev_log).use_office_com:
            if any(k in prev_type for k in ("key", "type", "click", "drag")):
                win_coords = prev_log.get("WindowCoordinates") or {"x": 0, "y": 0}
                win_size = prev_log.get("WindowSize") or {"width": 0, "height": 0}
                return {
                    "title": prev_log.get("WindowName", ""),
                    "process_name": prev_log.get("ProcessName", ""),
                    "command_line": prev_log.get("WindowCommandLine", ""),
                    "rect": {
                        "left": win_coords.get("x", 0),
                        "top": win_coords.get("y", 0),
                        "right": win_coords.get("x", 0) + win_size.get("width", 0),
                        "bottom": win_coords.get("y", 0) + win_size.get("height", 0),
                    },
                    "size": win_size,
                    "coordinates": win_coords,
                    "is_maximized": prev_log.get("IsMaximized", False),
                    "monitor": prev_log.get("Monitor"),
                }
    return None


def process_office_event(info: dict):
    """Excel等からCOM経由で受け取ったイベント情報をログに記録する"""
    try:
        msg = str(info.get("message", ""))
        if info.get("event_type") == "system" or "[System]" in msg:
            return

        window_info = window_inspector.get_foreground_window_info()
        if window_inspector.should_ignore_window(window_info.get("title")):
            return

        fg_profile = detect_app_profile(window_info)
        fg_policy = get_app_log_policy(fg_profile, source=window_info)
        if not fg_policy.use_office_com:
            # Why: セル入力直後に別ウィンドウクリックでフォーカス喪失発火したOnSheetChangeのみ直近Excel情報を引き継いで救出
            is_cell_change = info.get("event_type") == "cell_change" or "入力確定" in msg
            rescued_win = _find_recent_excel_window_info() if is_cell_change else None
            if not rescued_win:
                return
            window_info = rescued_win

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
    # Why: 無期限joinによる生成停止フリーズを防止しタイムアウト合流
    if state.key_worker_thread:
        state.key_worker_thread.join(timeout=3.0)
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
    # Why: 無期限joinによる生成停止フリーズを防止しタイムアウト合流
    if state.mouse_worker_thread:
        state.mouse_worker_thread.join(timeout=3.0)
    state.mouse_worker_thread = None
