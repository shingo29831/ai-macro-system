# @role: ユーザーのOSレベルの入力操作を記録する。
#
# 特徴:
# - クリックはmouse down時にPre画像を取得し、その場でPNG保存する。
# - 高速キー入力やマウスクリックはキューへ積み、OSによるフックの強制切断（タイムアウト）を防ぐ。
# - マウスの移動経路を追跡し、角（L字など45度以上）を描いた頂点をホバー座標として記録する。
#   スクリーンショット時の差分がない場合は画像名に delete_ を付与しリリース時に削除可能とする。
# - 物理ホイール・横ホイール・多くのタッチパッドスクロールを
#   Windows低レベルフックで取得する。
# - 画像パスは wf_○○/images/... の形式でJSONへ保存する。
# - 最後に EventNo: "End" のログを追加する。

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import queue
import sys
import threading
import traceback
import time
import math

from pynput import keyboard, mouse


# =========================
# import対応
# =========================

CURRENT_DIR = Path(__file__).resolve().parent

if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import process_monitor
import screen_capturer as screen_capture


# =========================
# 設定
# =========================

DOUBLE_CLICK_INTERVAL_SEC = 0.35
DOUBLE_CLICK_MAX_DISTANCE = 8

MODIFIER_KEYS = {
    "ctrl",
    "ctrl_l",
    "ctrl_r",
    "alt",
    "alt_l",
    "alt_r",
    "shift",
    "shift_l",
    "shift_r",
    "win",
    "win_l",
    "win_r",
    "windows",
}

COMBO_TRIGGER_KEYS = {
    "tab",
    "enter",
    "space",
    "esc",
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
    "a",
    "c",
    "v",
    "x",
    "z",
    "y",
    "s",
    "n",
    "o",
    "p",
    "r",
    "t",
    "w",
    "l",
    "\\",
}

# Windows低レベルマウスフック
WH_MOUSE_LL = 14

WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WM_QUIT = 0x0012

WHEEL_DELTA = 120

# ホバー検知（軌跡の角判定）用設定
MIN_DISTANCE_FOR_VECTOR = 40  # 手ブレを排除するためのサンプリング距離（ピクセル）
CORNER_ANGLE_THRESHOLD = 20   # 方向転換と見なす最小角度（度）


# =========================
# Windows API 型定義
# =========================

LRESULT = ctypes.c_ssize_t
HHOOK = wintypes.HANDLE
HINSTANCE = wintypes.HANDLE
DWORD = wintypes.DWORD


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


LowLevelMouseProc = ctypes.WINFUNCTYPE(
    LRESULT,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    LowLevelMouseProc,
    HINSTANCE,
    DWORD,
]
user32.SetWindowsHookExW.restype = HHOOK

user32.CallNextHookEx.argtypes = [
    HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = LRESULT

user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = ctypes.c_int

user32.TranslateMessage.argtypes = [
    ctypes.POINTER(wintypes.MSG),
]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
]
user32.DispatchMessageW.restype = LRESULT

user32.PostThreadMessageW.argtypes = [
    DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = DWORD

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = HINSTANCE


# =========================
# 状態管理
# =========================

_mouse_listener: mouse.Listener | None = None
_keyboard_listener: keyboard.Listener | None = None

_is_recording = False
_is_stopping = False
_is_click_processing = False

_input_logs: list[dict] = []
_input_logs_lock = threading.Lock()

_recording_dirs: dict | None = None

_event_index = 0
_event_index_lock = threading.Lock()

_previous_screenshot_img = None
_previous_screenshot_lock = threading.Lock()

_pressed_keys: set[str] = set()
_pressed_keys_lock = threading.Lock()

_logged_combo_keys: set[str] = set()

_latest_mouse_down_event: dict | None = None
_latest_mouse_down_lock = threading.Lock()

_pending_click_event: dict | None = None
_pending_click_timer: threading.Timer | None = None
_pending_click_lock = threading.Lock()

# キー入力専用キュー
_key_event_queue: queue.Queue = queue.Queue()
_key_worker_thread: threading.Thread | None = None
_key_worker_stop_event = threading.Event()

# マウス入力・ホバー用キュー
_mouse_event_queue: queue.Queue = queue.Queue()
_mouse_worker_thread: threading.Thread | None = None
_mouse_worker_stop_event = threading.Event()

_native_scroll_hook_thread: threading.Thread | None = None
_native_scroll_hook_thread_id: int | None = None
_native_scroll_hook_handle = None
_native_scroll_hook_callback = None
_native_scroll_hook_ready = threading.Event()
_native_scroll_hook_active = False

# ホバー検知（軌跡判定）用
_mouse_path: list[tuple[float, float, float]] = []

_shortcut_stop_callback = None

def set_shortcut_stop_callback(callback):
    global _shortcut_stop_callback
    _shortcut_stop_callback = callback

# =========================
# 基本関数
# =========================

def now_datetime() -> datetime:
    return datetime.now().astimezone()


def next_event_no() -> str:
    global _event_index

    with _event_index_lock:
        _event_index += 1
        return f"{_event_index:03d}"


def mouse_button_to_string(button) -> str:
    text = str(button)

    if "." in text:
        return text.split(".")[-1]

    return text


def normalize_key_name(key) -> str:
    try:
        if key.char is not None:
            return str(key.char).lower()
    except AttributeError:
        pass

    text = str(key)

    if text.startswith("Key."):
        text = text.replace("Key.", "")

    text = text.replace("cmd", "win")

    return text.lower()


def key_to_string(key) -> str:
    return normalize_key_name(key)


def sorted_combo_keys(keys: set[str]) -> list[str]:
    order = [
        "ctrl",
        "ctrl_l",
        "ctrl_r",
        "alt",
        "alt_l",
        "alt_r",
        "shift",
        "shift_l",
        "shift_r",
        "win",
        "win_l",
        "win_r",
    ]

    def sort_key(value: str):
        if value in order:
            return 0, order.index(value)

        return 1, value

    return sorted(list(keys), key=sort_key)


def make_combo_text(keys: list[str]) -> str:
    return "+".join(keys)


def should_record_key_combo(
    pressed_keys: set[str],
    current_key: str
) -> bool:
    has_modifier = any(
        pressed_key in MODIFIER_KEYS
        for pressed_key in pressed_keys
    )

    return has_modifier and current_key in COMBO_TRIGGER_KEYS


def append_log(log: dict):
    with _input_logs_lock:
        _input_logs.append(log)


def calculate_and_update_diff(current_img) -> str:
    global _previous_screenshot_img

    with _previous_screenshot_lock:
        if _previous_screenshot_img is None:
            diff = "100%"
        else:
            diff = screen_capture.calculate_diff_percent(
                _previous_screenshot_img,
                current_img
            )

        _previous_screenshot_img = current_img.copy()

    return diff


def cancel_hover():
    global _mouse_path
    _mouse_path.clear()


# =========================
# JSONログ作成
# =========================

def build_base_log(
    event_no: str,
    dt: datetime,
    input_type: str,
    content: dict | None,
    window_info: dict,
    cursor_x: int | None = None,
    cursor_y: int | None = None,
) -> dict:
    window_fields = process_monitor.build_recording_window_fields(
        window_info,
        cursor_x=cursor_x,
        cursor_y=cursor_y
    )

    return {
        "EventNo": event_no,
        "TimeStamp": dt.isoformat(),
        "Type": input_type,
        "Content": content,
        "WindowName": window_fields["WindowName"],
        "WindowSize": window_fields["WindowSize"],
        "WindowCoordinates": window_fields["WindowCoordinates"],
        "CursorCoordinates": window_fields["CursorCoordinates"],
        "Images": {
            "Pre": None,
            "Crop": None,
            "Diff": None,
        },
    }


def build_scroll_log(
    event_no: str,
    dt: datetime,
    x: int,
    y: int,
    dx: float,
    dy: float,
) -> dict:
    point_window = process_monitor.get_window_title_at_point(x, y)

    if dy > 0:
        direction = "up"
    elif dy < 0:
        direction = "down"
    elif dx > 0:
        direction = "right"
    elif dx < 0:
        direction = "left"
    else:
        direction = "none"

    return {
        "EventNo": event_no,
        "TimeStamp": dt.isoformat(),
        "Type": "mouse_scroll",
        "Content": {
            "dx": round(float(dx), 3),
            "dy": round(float(dy), 3),
            "direction": direction,
            "screen_coordinates": {
                "x": int(x),
                "y": int(y),
            },
        },
        "WindowName": point_window.get("title", ""),
        "WindowSize": None,
        "WindowCoordinates": None,
        "CursorCoordinates": None,
        "Images": {
            "Pre": None,
            "Crop": None,
            "Diff": None,
        },
    }


# =========================
# キー入力キュー処理
# =========================

def key_event_worker():
    while (
        not _key_worker_stop_event.is_set()
        or not _key_event_queue.empty()
    ):
        try:
            event = _key_event_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        try:
            process_key_event(event)

        except Exception:
            print("キー入力キュー処理中にエラーが発生しました")
            traceback.print_exc()

        finally:
            _key_event_queue.task_done()


def start_key_event_worker():
    global _key_worker_thread

    _key_worker_stop_event.clear()

    _key_worker_thread = threading.Thread(
        target=key_event_worker,
        daemon=True
    )

    _key_worker_thread.start()


def stop_key_event_worker():
    global _key_worker_thread

    _key_worker_stop_event.set()

    try:
        _key_event_queue.join()
    except Exception:
        pass

    if _key_worker_thread is not None:
        _key_worker_thread.join(timeout=10)

    _key_worker_thread = None


def enqueue_key_event(
    input_type: str,
    key_text: str,
    keys: list[str] | None = None,
    capture_now: bool = False,
):
    event_no = next_event_no()
    dt = now_datetime()

    window_info = process_monitor.get_foreground_window_info()

    _key_event_queue.put({
        "event_no": event_no,
        "datetime": dt,
        "input_type": input_type,
        "key": key_text,
        "keys": keys or [],
        "window_info": window_info,
        "pre_img": None,
        "pre_ref": None,
    })


def process_key_event(event: dict):
    event_no = event["event_no"]
    dt = event["datetime"]
    input_type = event["input_type"]

    if input_type == "key_combo":
        content = {
            "keys": event["keys"],
            "combo": make_combo_text(event["keys"]),
        }
    else:
        content = {
            "key": event["key"],
        }

    log = build_base_log(
        event_no=event_no,
        dt=dt,
        input_type=input_type,
        content=content,
        window_info=event["window_info"]
    )

    pre_img = event.get("pre_img")
    pre_ref = event.get("pre_ref")

    if pre_img is None:
        pre_img, _ = screen_capture.take_screenshot()

    if pre_ref is None:
        pre_ref = screen_capture.save_pre_image_from_pil(
            event_no=event_no,
            img=pre_img
        )

    diff = calculate_and_update_diff(pre_img)

    log["Images"] = {
        "Pre": pre_ref,
        "Crop": None,
        "Diff": diff,
    }

    append_log(log)

    print(
        f"キー入力ログ追加: evt_{event_no}, "
        f"type={input_type}, diff={diff}"
    )


# =========================
# ホバーイベントの処理
# =========================

def process_hover_event(event: dict):
    try:
        # UI（ドロップダウン等）の展開エフェクトが完了するまでのラグを微小待機する
        time.sleep(0.2)

        event_no = next_event_no()
        dt = now_datetime()

        x = int(event["x"])
        y = int(event["y"])

        window_info = process_monitor.get_foreground_window_info()
        pre_full_img, pre_monitor = screen_capture.take_screenshot()

        pre_ref = screen_capture.save_pre_image_from_pil(
            event_no=event_no,
            img=pre_full_img
        )

        diff_str = calculate_and_update_diff(pre_full_img)
        diff_val = 0.0
        try:
            diff_val = float(diff_str.replace("%", ""))
        except:
            pass

        # 差分がほとんどない場合は delete_ を付与 (0.1% 未満)
        is_meaningless = diff_val < 0.1
        
        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()

        if is_meaningless:
            old_path = macros_root / pre_ref
            if old_path.exists():
                new_name = "delete_" + old_path.name
                new_path = old_path.with_name(new_name)
                old_path.rename(new_path)
                pre_ref = str(Path(pre_ref).parent / new_name).replace("\\", "/")

        content = {
            "screen_coordinates": {
                "x": x,
                "y": y,
            }
        }

        log = build_base_log(
            event_no=event_no,
            dt=dt,
            input_type="mouse_hover",
            content=content,
            window_info=window_info,
            cursor_x=x,
            cursor_y=y
        )

        ui_rect = process_monitor.get_ui_element_rect_at_point(x, y)

        if ui_rect is not None:
            crop = screen_capture.save_ui_crop_by_rect(
                event_no=event_no,
                rect=ui_rect,
                full_img=pre_full_img,
                monitor=pre_monitor
            )
        else:
            crop = screen_capture.save_ui_crop(
                event_no=event_no,
                click_x=x,
                click_y=y,
                full_img=pre_full_img,
                monitor=pre_monitor
            )

        crop_ref = crop.get("ui_image_ref")

        if crop_ref is None:
            crop_ref = "切り抜き失敗"
            
        if is_meaningless and crop_ref != "切り抜き失敗":
            old_crop_path = macros_root / crop_ref
            if old_crop_path.exists():
                new_crop_name = "delete_" + old_crop_path.name
                new_crop_path = old_crop_path.with_name(new_crop_name)
                old_crop_path.rename(new_crop_path)
                crop_ref = str(Path(crop_ref).parent / new_crop_name).replace("\\", "/")

        log["Images"] = {
            "Pre": pre_ref,
            "Crop": crop_ref,
            "Diff": diff_str,
        }

        append_log(log)

        print(f"ホバーログ追加: evt_{event_no}, diff={diff_str} {'(deleted)' if is_meaningless else ''}")

    except Exception:
        print("ホバー処理中にエラーが発生しました")
        traceback.print_exc()


# =========================
# マウス入力キュー処理
# =========================

def mouse_event_worker():
    while (
        not _mouse_worker_stop_event.is_set()
        or not _mouse_event_queue.empty()
    ):
        try:
            event = _mouse_event_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        try:
            _handle_mouse_event(event)

        except Exception:
            print("マウス入力キュー処理中にエラーが発生しました")
            traceback.print_exc()

        finally:
            _mouse_event_queue.task_done()

def start_mouse_event_worker():
    global _mouse_worker_thread

    _mouse_worker_stop_event.clear()

    _mouse_worker_thread = threading.Thread(
        target=mouse_event_worker,
        daemon=True
    )

    _mouse_worker_thread.start()

def stop_mouse_event_worker():
    global _mouse_worker_thread

    _mouse_worker_stop_event.set()

    try:
        _mouse_event_queue.join()
    except Exception:
        pass

    if _mouse_worker_thread is not None:
        _mouse_worker_thread.join(timeout=10)

    _mouse_worker_thread = None


def on_move(x, y):
    global _mouse_path
    if not _is_recording:
        return
    
    current_time = time.time()
    
    if not _mouse_path:
        _mouse_path.append((x, y, current_time))
    else:
        last_x, last_y, _ = _mouse_path[-1]
        dist = math.hypot(x - last_x, y - last_y)
        
        # 一定距離(MIN_DISTANCE_FOR_VECTOR)進むごとにサンプリング
        if dist >= MIN_DISTANCE_FOR_VECTOR:
            _mouse_path.append((x, y, current_time))
            
            # 3点以上あれば、なす角を計算して「方向転換」を検出
            if len(_mouse_path) >= 3:
                p1 = _mouse_path[-3]
                p2 = _mouse_path[-2]  # 頂点候補
                p3 = _mouse_path[-1]
                
                v1 = (p2[0] - p1[0], p2[1] - p1[1])
                v2 = (p3[0] - p2[0], p3[1] - p2[1])
                
                dot = v1[0]*v2[0] + v1[1]*v2[1]
                mag1 = math.hypot(v1[0], v1[1])
                mag2 = math.hypot(v2[0], v2[1])
                
                if mag1 > 0 and mag2 > 0:
                    cos_theta = dot / (mag1 * mag2)
                    cos_theta = max(-1.0, min(1.0, cos_theta))
                    angle = math.degrees(math.acos(cos_theta))
                    
                    # 進行方向が閾値以上曲がったら「角」と見なす
                    if angle >= CORNER_ANGLE_THRESHOLD:
                        _mouse_event_queue.put({
                            "type": "hover",
                            "x": p2[0],
                            "y": p2[1]
                        })
                
                # 連続する方向転換を判定できるように最古の1点だけ捨てる
                _mouse_path.pop(0)


def on_click(x, y, button, pressed):
    cancel_hover()
    if not _is_recording:
        return
    
    _mouse_event_queue.put({
        "type": "click",
        "x": x,
        "y": y,
        "button": button,
        "pressed": pressed
    })


def _handle_mouse_event(evt: dict):
    evt_type = evt.get("type", "click")
    
    if evt_type == "hover":
        process_hover_event(evt)
        return

    # click logic
    global _latest_mouse_down_event
    global _pending_click_event
    global _pending_click_timer

    x = evt["x"]
    y = evt["y"]
    button = evt["button"]
    pressed = evt["pressed"]

    if pressed:
        try:
            event_no = next_event_no()
            dt = now_datetime()

            window_info = process_monitor.get_foreground_window_info()

            pre_full_img, pre_monitor = screen_capture.take_screenshot()

            pre_ref = screen_capture.save_pre_image_from_pil(
                event_no=event_no,
                img=pre_full_img
            )

            down_event = {
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

            with _latest_mouse_down_lock:
                _latest_mouse_down_event = down_event

            print(
                f"mouse down取得・画像保存: "
                f"evt_{event_no}, x={x}, y={y}, button={button}"
            )

        except Exception:
            print("mouse down取得中にエラーが発生しました")
            traceback.print_exc()

        return

    with _latest_mouse_down_lock:
        current_event = _latest_mouse_down_event
        _latest_mouse_down_event = None

    if current_event is None:
        return

    previous_event_to_process = None

    with _pending_click_lock:
        if _pending_click_event is not None:
            if is_same_click(_pending_click_event, current_event):
                first_event = _pending_click_event

                if _pending_click_timer is not None:
                    _pending_click_timer.cancel()

                _pending_click_event = None
                _pending_click_timer = None

                run_click_process_thread(
                    first_event,
                    input_type="mouse_double_click",
                    click_count=2
                )

                return

            previous_event_to_process = _pending_click_event

            if _pending_click_timer is not None:
                _pending_click_timer.cancel()

        _pending_click_event = current_event

        _pending_click_timer = threading.Timer(
            DOUBLE_CLICK_INTERVAL_SEC,
            process_pending_single_click
        )
        _pending_click_timer.daemon = True
        _pending_click_timer.start()

    if previous_event_to_process is not None:
        run_click_process_thread(
            previous_event_to_process,
            input_type="mouse_click",
            click_count=1
        )


def process_click_event(
    event: dict,
    input_type: str,
    click_count: int
):
    global _is_click_processing

    try:
        event_no = event["event_no"]
        dt = event["datetime"]

        x = int(event["x"])
        y = int(event["y"])
        button = event["button"]

        pre_img = event["pre_full_img"]
        pre_monitor = event["pre_monitor"]

        pre_ref = event["pre_ref"]

        content = {
            "button": mouse_button_to_string(button),
            "click_count": int(click_count),
            "screen_coordinates": {
                "x": x,
                "y": y,
            },
        }

        log = build_base_log(
            event_no=event_no,
            dt=dt,
            input_type=input_type,
            content=content,
            window_info=event["window_info"],
            cursor_x=x,
            cursor_y=y
        )

        diff = calculate_and_update_diff(pre_img)

        ui_rect = process_monitor.get_ui_element_rect_at_point(x, y)

        if ui_rect is not None:
            crop = screen_capture.save_ui_crop_by_rect(
                event_no=event_no,
                rect=ui_rect,
                full_img=pre_img,
                monitor=pre_monitor
            )
        else:
            crop = screen_capture.save_ui_crop(
                event_no=event_no,
                click_x=x,
                click_y=y,
                full_img=pre_img,
                monitor=pre_monitor
            )

        crop_ref = crop.get("ui_image_ref")

        if crop_ref is None:
            crop_ref = "切り抜き失敗"

        log["Images"] = {
            "Pre": pre_ref,
            "Crop": crop_ref,
            "Diff": diff,
        }

        append_log(log)

        print(
            f"クリックログ追加: evt_{event_no}, "
            f"type={input_type}, diff={diff}"
        )

    except Exception:
        print("クリック処理中にエラーが発生しました")
        traceback.print_exc()

    finally:
        _is_click_processing = False


def run_click_process_thread(
    event: dict,
    input_type: str,
    click_count: int
):
    global _is_click_processing

    if _is_click_processing:
        print("前のクリック処理中のため、このクリックは無視します")
        return

    _is_click_processing = True

    thread = threading.Thread(
        target=process_click_event,
        args=(event, input_type, click_count),
        daemon=True
    )
    thread.start()


def is_same_click(
    first_event: dict | None,
    second_event: dict | None
) -> bool:
    if first_event is None or second_event is None:
        return False

    if str(first_event["button"]) != str(second_event["button"]):
        return False

    dx = int(first_event["x"]) - int(second_event["x"])
    dy = int(first_event["y"]) - int(second_event["y"])

    distance_sq = dx * dx + dy * dy

    return (
        distance_sq
        <= DOUBLE_CLICK_MAX_DISTANCE * DOUBLE_CLICK_MAX_DISTANCE
    )


def process_pending_single_click():
    global _pending_click_event
    global _pending_click_timer

    with _pending_click_lock:
        event = _pending_click_event
        _pending_click_event = None
        _pending_click_timer = None

    if event is None:
        return

    run_click_process_thread(
        event,
        input_type="mouse_click",
        click_count=1
    )


# =========================
# スクロール処理
# =========================

def record_scroll_event(
    x: int,
    y: int,
    dx: float,
    dy: float
):
    if not _is_recording:
        return

    event_no = next_event_no()
    dt = now_datetime()

    log = build_scroll_log(
        event_no=event_no,
        dt=dt,
        x=int(x),
        y=int(y),
        dx=float(dx),
        dy=float(dy)
    )

    append_log(log)

    print(
        f"スクロールログ追加: evt_{event_no}, "
        f"dx={dx}, dy={dy}"
    )


def on_scroll(x, y, dx, dy):
    cancel_hover()
    if _native_scroll_hook_active:
        return

    try:
        record_scroll_event(
            x=int(x),
            y=int(y),
            dx=float(dx),
            dy=float(dy)
        )

    except Exception:
        print("pynputスクロール処理中にエラーが発生しました")
        traceback.print_exc()


# =========================
# Windows低レベルスクロールフック
# =========================

def native_scroll_hook_callback(n_code, w_param, l_param):
    if n_code >= 0 and _is_recording:
        if w_param in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            try:
                cancel_hover()
                mouse_info = ctypes.cast(
                    l_param,
                    ctypes.POINTER(MSLLHOOKSTRUCT)
                ).contents

                raw_delta = ctypes.c_short(
                    (mouse_info.mouseData >> 16) & 0xFFFF
                ).value

                delta = raw_delta / WHEEL_DELTA

                if w_param == WM_MOUSEWHEEL:
                    dx = 0.0
                    dy = delta
                else:
                    dx = delta
                    dy = 0.0

                record_scroll_event(
                    x=int(mouse_info.pt.x),
                    y=int(mouse_info.pt.y),
                    dx=dx,
                    dy=dy
                )

            except Exception:
                print("Windowsスクロールフック処理中にエラーが発生しました")
                traceback.print_exc()

    return user32.CallNextHookEx(
        _native_scroll_hook_handle,
        n_code,
        w_param,
        l_param
    )


def native_scroll_hook_worker():
    global _native_scroll_hook_thread_id
    global _native_scroll_hook_handle
    global _native_scroll_hook_callback
    global _native_scroll_hook_active

    try:
        _native_scroll_hook_thread_id = kernel32.GetCurrentThreadId()

        _native_scroll_hook_callback = LowLevelMouseProc(
            native_scroll_hook_callback
        )

        module_handle = kernel32.GetModuleHandleW(None)

        _native_scroll_hook_handle = user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            _native_scroll_hook_callback,
            module_handle,
            0
        )

        if not _native_scroll_hook_handle:
            error_code = ctypes.get_last_error()

            print(
                "Windowsスクロールフックの開始に失敗しました。"
                f" WinError={error_code}"
            )

            _native_scroll_hook_active = False
            _native_scroll_hook_ready.set()
            return

        _native_scroll_hook_active = True
        _native_scroll_hook_ready.set()

        print("Windowsスクロールフックを開始しました")

        message = wintypes.MSG()

        while user32.GetMessageW(
            ctypes.byref(message),
            None,
            0,
            0
        ) != 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    except Exception:
        print("Windowsスクロールフックの起動中にエラーが発生しました")
        traceback.print_exc()

        _native_scroll_hook_active = False
        _native_scroll_hook_ready.set()

    finally:
        if _native_scroll_hook_handle:
            user32.UnhookWindowsHookEx(_native_scroll_hook_handle)

        _native_scroll_hook_handle = None
        _native_scroll_hook_active = False

        print("Windowsスクロールフックを停止しました")


def start_native_scroll_hook():
    global _native_scroll_hook_thread

    _native_scroll_hook_ready.clear()

    _native_scroll_hook_thread = threading.Thread(
        target=native_scroll_hook_worker,
        daemon=True
    )

    _native_scroll_hook_thread.start()

    _native_scroll_hook_ready.wait(timeout=1.0)


def stop_native_scroll_hook():
    global _native_scroll_hook_thread
    global _native_scroll_hook_thread_id

    if _native_scroll_hook_thread_id is not None:
        user32.PostThreadMessageW(
            _native_scroll_hook_thread_id,
            WM_QUIT,
            0,
            0
        )

    if _native_scroll_hook_thread is not None:
        _native_scroll_hook_thread.join(timeout=2)

    _native_scroll_hook_thread = None
    _native_scroll_hook_thread_id = None


# =========================
# pynput入力イベント
# =========================

def on_press(key):
    global _logged_combo_keys
    cancel_hover()

    if not _is_recording:
        return

    key_text = key_to_string(key)

    if key_text in ("\\", "\x1c"):
        with _pressed_keys_lock:
            has_ctrl = any(
                pressed_key in ["ctrl", "ctrl_l", "ctrl_r"]
                for pressed_key in _pressed_keys
            )

        if has_ctrl:
            global _is_stopping
            if not _is_stopping:
                _is_stopping = True
                print("Ctrl + \\ が押されたため記録を停止します")
                if _shortcut_stop_callback:
                    _shortcut_stop_callback()
                else:
                    stop_recording()
            return

    try:
        with _pressed_keys_lock:
            _pressed_keys.add(key_text)
            current_keys = set(_pressed_keys)

        capture_now = (key_text == "enter")

        if should_record_key_combo(current_keys, key_text):
            combo_keys = sorted_combo_keys(current_keys)
            combo_text = make_combo_text(combo_keys)

            if combo_text not in _logged_combo_keys:
                _logged_combo_keys.add(combo_text)

                enqueue_key_event(
                    input_type="key_combo",
                    key_text=key_text,
                    keys=combo_keys,
                    capture_now=capture_now
                )

            return

        if key_text in MODIFIER_KEYS and not key_text.startswith("win"):
            return

        enqueue_key_event(
            input_type="key_press",
            key_text=key_text,
            capture_now=capture_now
        )

    except Exception:
        print("キー入力処理中にエラーが発生しました")
        traceback.print_exc()


def on_release(key):
    global _logged_combo_keys

    key_text = key_to_string(key)

    with _pressed_keys_lock:
        _pressed_keys.discard(key_text)

        if not _pressed_keys:
            _logged_combo_keys = set()


# =========================
# Endログ
# =========================

def create_end_log() -> dict:
    event_no = "End"
    dt = now_datetime()

    window_info = process_monitor.get_foreground_window_info()

    end_img, _ = screen_capture.take_screenshot()

    end_ref = screen_capture.save_pre_image_from_pil(
        event_no=event_no,
        img=end_img
    )

    diff = calculate_and_update_diff(end_img)

    window_fields = process_monitor.build_recording_window_fields(
        window_info,
        cursor_x=None,
        cursor_y=None
    )

    return {
        "EventNo": event_no,
        "TimeStamp": dt.isoformat(),
        "Type": "recording_end",
        "Content": None,
        "WindowName": window_fields["WindowName"],
        "WindowSize": window_fields["WindowSize"],
        "WindowCoordinates": None,
        "CursorCoordinates": None,
        "Images": {
            "Pre": end_ref,
            "Crop": None,
            "Diff": diff,
        },
    }


# =========================
# 保存
# =========================

def event_no_sort_key(log: dict):
    event_no = log.get("EventNo")

    if event_no == "End":
        return 999999999

    try:
        return int(event_no)
    except (TypeError, ValueError):
        return 999999998


def save_input_logs():
    temp_dir = screen_capture.get_temp_dir()
    json_path = temp_dir / "input_logs.json"

    with _input_logs_lock:
        logs_copy = list(_input_logs)

    logs_copy.sort(key=event_no_sort_key)

    output = {
        "MacroName": _recording_dirs["macro_name"] if _recording_dirs else None,
        "Logs": logs_copy,
    }

    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2,
            default=str
        )

    print(f"input_logs.json 保存: {json_path}")


# =========================
# 録画開始・停止
# =========================

def start_recording():
    global _mouse_listener
    global _keyboard_listener
    global _is_recording
    global _is_stopping
    global _is_click_processing
    global _input_logs
    global _recording_dirs
    global _event_index
    global _previous_screenshot_img
    global _pressed_keys
    global _logged_combo_keys
    global _pending_click_event
    global _pending_click_timer
    global _latest_mouse_down_event

    if _is_recording:
        print("すでに記録中です")
        return

    try:
        _recording_dirs = screen_capture.make_directory()

        _input_logs = []

        _event_index = 0
        _previous_screenshot_img = None

        _pressed_keys = set()
        _logged_combo_keys = set()

        _pending_click_event = None
        _pending_click_timer = None
        _latest_mouse_down_event = None

        _is_stopping = False
        _is_click_processing = False

        process_monitor.start_process_monitors()

        _is_recording = True

        start_key_event_worker()
        start_mouse_event_worker()
        start_native_scroll_hook()

        _mouse_listener = mouse.Listener(
            on_move=on_move,
            on_click=on_click,
            on_scroll=on_scroll
        )

        _keyboard_listener = keyboard.Listener(
            on_press=on_press,
            on_release=on_release
        )

        _mouse_listener.start()
        _keyboard_listener.start()

        print("記録を開始しました")
        print(f"macro: {_recording_dirs['macro_name']}")
        print(f"native_scroll_hook: {_native_scroll_hook_active}")

    except Exception:
        _is_recording = False
        _is_stopping = False

        stop_native_scroll_hook()
        stop_mouse_event_worker()
        stop_key_event_worker()
        process_monitor.stop_process_monitors()

        print("記録開始に失敗しました")
        traceback.print_exc()

        raise


def stop_recording():
    global _mouse_listener
    global _keyboard_listener
    global _is_recording
    global _pending_click_timer
    global _pending_click_event

    if not _is_recording:
        return

    try:
        _is_recording = False
        cancel_hover()

        with _pending_click_lock:
            if _pending_click_timer is not None:
                _pending_click_timer.cancel()

            pending_event = _pending_click_event

            _pending_click_timer = None
            _pending_click_event = None

        if pending_event is not None:
            run_click_process_thread(
                pending_event,
                input_type="mouse_click",
                click_count=1
            )

        if _mouse_listener is not None:
            _mouse_listener.stop()
            _mouse_listener = None

        if _keyboard_listener is not None:
            _keyboard_listener.stop()
            _keyboard_listener = None

        stop_native_scroll_hook()

        stop_key_event_worker()
        stop_mouse_event_worker()

        while _is_click_processing:
            threading.Event().wait(0.01)

        append_log(create_end_log())

        process_monitor.stop_process_monitors()

        save_input_logs()

        print("記録を停止しました")
        print("recording_end: evt_End_pre.png")

    except Exception:
        print("記録停止中にエラーが発生しました")
        traceback.print_exc()

        raise


# =========================
# 単体実行
# =========================

if __name__ == "__main__":
    print("記録を開始します")
    print("終了するには Ctrl + \\ キーを押してください")

    start_recording()

    if _keyboard_listener is not None:
        _keyboard_listener.join()