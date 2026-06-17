# @role: ユーザーのOSレベルの入力操作（マウス・キーボード・ホイール）をフックし、操作ログとして記録する。
#
# mouse_scroll のみ:
#   - TimeStamp
#   - Type
#   - Content
#   - WindowName
#   のみ実データを入れる。
#   その他の項目は None。
#
# Debug:
#   - 保存しない。
#
# Crop:
#   - クリック後に再スクリーンショットを撮らない。
#   - mouse down時に取得したPre画像からUI部分を切り抜く。
#
# Diff:
#   - Images.Diff に前回スクリーンショットとの差分率を%表記で保存する。
#   - 1回目のスクリーンショットは 100%。
#   - mouse_scroll はスクリーンショットを取らないため Diff は None。
#
# End:
#   - 最後に EventNo: "End" のログを追加する。
#   - 終了時スクリーンショット evt_End_pre.png を保存する。
#   - 前回スクリーンショットとの差分 Diff も保存する。
#
# JSON:
#   - 画像パスは wf_○○ から始まる相対パスにする。
#   - MacroDirectory / TempDirectory / ImagesDirectory / RecordingEndEventNo
#     / RecordingEndFullImage / SavedAt / LogCount は保存しない。
#
# 保存:
#   ../../../macros/wf_連番/temp/input_logs.json
#   ../../../macros/wf_連番/images/evt_XXX_pre.png
#   ../../../macros/wf_連番/images/evt_XXX_crop.png
#   ../../../macros/wf_連番/images/evt_End_pre.png

from datetime import datetime
import json
import threading
import traceback
import sys
from pathlib import Path

from pynput import mouse, keyboard


# =========================
# import対応
# =========================

CURRENT_DIR = Path(__file__).resolve().parent

if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import screen_capturer as screen_capture
import process_monitor


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
    "cmd",
    "cmd_l",
    "cmd_r",
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
}


# =========================
# 状態管理
# =========================

_mouse_listener: mouse.Listener | None = None
_keyboard_listener: keyboard.Listener | None = None

_is_recording = False
_is_processing = False

_input_logs: list[dict] = []

_recording_dirs: dict | None = None

_pending_click_timer: threading.Timer | None = None
_pending_click_event: dict | None = None
_pending_click_lock = threading.Lock()

_latest_mouse_down_event: dict | None = None
_latest_mouse_down_lock = threading.Lock()

_event_index = 0
_event_index_lock = threading.Lock()

_previous_screenshot_img = None
_previous_screenshot_lock = threading.Lock()

_pressed_keys: set[str] = set()
_pressed_keys_lock = threading.Lock()

_logged_combo_keys: set[str] = set()

_processing_threads: list[threading.Thread] = []
_processing_threads_lock = threading.Lock()


# =========================
# 補助関数
# =========================

def now_datetime() -> datetime:
    return datetime.now().astimezone()


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


def next_event_no() -> str:
    global _event_index

    with _event_index_lock:
        _event_index += 1
        return f"{_event_index:03d}"


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


def build_base_log(
    event_no: str,
    dt: datetime,
    input_type: str,
    content: dict | str | None,
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
    dx: int,
    dy: int,
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
            "dx": int(dx),
            "dy": int(dy),
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

    def sort_key(k: str):
        if k in order:
            return (0, order.index(k))
        return (1, k)

    return sorted(list(keys), key=sort_key)


def make_combo_text(keys: list[str]) -> str:
    return "+".join(keys)


def should_record_key_combo(pressed_keys: set[str], current_key: str) -> bool:
    has_modifier = any(k in MODIFIER_KEYS for k in pressed_keys)
    return has_modifier and current_key in COMBO_TRIGGER_KEYS


def register_processing_thread(thread: threading.Thread):
    with _processing_threads_lock:
        _processing_threads.append(thread)


def wait_processing_threads():
    """
    クリック処理スレッドが残っている場合、保存前に終わるのを待つ。
    Endログを本当に最後にするため。
    """
    current = threading.current_thread()

    with _processing_threads_lock:
        threads = list(_processing_threads)
        _processing_threads.clear()

    for thread in threads:
        if thread is current:
            continue

        if thread.is_alive():
            thread.join(timeout=3)


# =========================
# Endログ
# =========================

def create_end_log() -> dict:
    """
    最後のログとして EventNo: "End" を作成する。
    """
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
# クリック処理
# =========================

def process_click_event(
    event: dict,
    input_type: str,
    click_count: int
):
    global _is_processing

    try:
        event_no = event["event_no"]
        dt = event["datetime"]

        x = int(event["x"])
        y = int(event["y"])
        button = event["button"]

        print(f"クリック確定: evt_{event_no}, {input_type}, x={x}, y={y}, button={button}")

        pre_img = event["pre_full_img"]
        pre_monitor = event["pre_monitor"]

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
            cursor_y=y,
        )

        pre_ref = screen_capture.save_pre_image_from_pil(
            event_no=event_no,
            img=pre_img
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

        log["Images"] = {
            "Pre": pre_ref,
            "Crop": crop.get("ui_image_ref"),
            "Diff": diff,
        }

        _input_logs.append(log)

        print(
            f"クリックログ追加: evt_{event_no}, "
            f"diff={diff}, crop={crop.get('detection_method')}"
        )

    except Exception:
        print("クリック処理中にエラーが発生しました")
        traceback.print_exc()

    finally:
        _is_processing = False


def run_click_process_thread(
    event: dict,
    input_type: str,
    click_count: int
):
    global _is_processing

    if _is_processing:
        print("前のクリック処理中のため、このクリックは無視します")
        return

    _is_processing = True

    thread = threading.Thread(
        target=process_click_event,
        args=(event, input_type, click_count),
        daemon=True
    )
    register_processing_thread(thread)
    thread.start()


# =========================
# シングル / ダブルクリック判定
# =========================

def is_same_click(first_event: dict | None, second_event: dict | None) -> bool:
    if first_event is None or second_event is None:
        return False

    if str(first_event["button"]) != str(second_event["button"]):
        return False

    dx = int(first_event["x"]) - int(second_event["x"])
    dy = int(first_event["y"]) - int(second_event["y"])

    distance_sq = dx * dx + dy * dy

    return distance_sq <= DOUBLE_CLICK_MAX_DISTANCE * DOUBLE_CLICK_MAX_DISTANCE


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
# 入力イベント
# =========================

def on_click(x, y, button, pressed):
    global _latest_mouse_down_event
    global _pending_click_event
    global _pending_click_timer

    if not _is_recording:
        return

    if pressed:
        try:
            event_no = next_event_no()
            dt = now_datetime()

            window_info = process_monitor.get_foreground_window_info()
            pre_full_img, pre_monitor = screen_capture.take_screenshot()

            down_event = {
                "event_no": event_no,
                "datetime": dt,
                "x": int(x),
                "y": int(y),
                "button": button,
                "window_info": window_info,
                "pre_full_img": pre_full_img,
                "pre_monitor": pre_monitor,
            }

            with _latest_mouse_down_lock:
                _latest_mouse_down_event = down_event

            print(f"mouse down取得: evt_{event_no}, x={x}, y={y}, button={button}")

        except Exception:
            print("mouse down取得中にエラーが発生しました")
            traceback.print_exc()

        return

    with _latest_mouse_down_lock:
        current_event = _latest_mouse_down_event
        _latest_mouse_down_event = None

    if current_event is None:
        print("mouse down情報がないため、このクリックは無視します")
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

                print("ダブルクリック検出")

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
            _pending_click_timer.start()

        else:
            _pending_click_event = current_event
            _pending_click_timer = threading.Timer(
                DOUBLE_CLICK_INTERVAL_SEC,
                process_pending_single_click
            )
            _pending_click_timer.start()

    if previous_event_to_process is not None:
        run_click_process_thread(
            previous_event_to_process,
            input_type="mouse_click",
            click_count=1
        )


def on_scroll(x, y, dx, dy):
    if not _is_recording:
        return

    try:
        event_no = next_event_no()
        dt = now_datetime()

        log = build_scroll_log(
            event_no=event_no,
            dt=dt,
            x=int(x),
            y=int(y),
            dx=int(dx),
            dy=int(dy)
        )

        _input_logs.append(log)

        print(
            f"ホイールログ追加: evt_{event_no}, "
            f"dx={dx}, dy={dy}, window={log.get('WindowName', '')}"
        )

    except Exception:
        print("マウスホイール処理中にエラーが発生しました")
        traceback.print_exc()


def record_key_event(input_type: str, key_text: str, keys: list[str] | None = None):
    event_no = next_event_no()
    dt = now_datetime()

    window_info = process_monitor.get_foreground_window_info()

    if input_type == "key_combo":
        content = {
            "keys": keys or [],
            "combo": make_combo_text(keys or []),
        }
    else:
        content = {
            "key": key_text,
        }

    log = build_base_log(
        event_no=event_no,
        dt=dt,
        input_type=input_type,
        content=content,
        window_info=window_info,
        cursor_x=None,
        cursor_y=None,
    )

    pre_img, _ = screen_capture.take_screenshot()

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

    _input_logs.append(log)

    print(f"キー入力ログ追加: evt_{event_no}, type={input_type}, content={content}, diff={diff}")


def on_press(key):
    global _logged_combo_keys

    if not _is_recording:
        return

    key_text = key_to_string(key)

    if key == keyboard.Key.esc:
        with _pressed_keys_lock:
            has_modifier = any(k in MODIFIER_KEYS for k in _pressed_keys)

        if not has_modifier:
            print("Esc が押されたため記録を停止します")
            stop_recording()
            return False

    try:
        with _pressed_keys_lock:
            _pressed_keys.add(key_text)
            current_keys = set(_pressed_keys)

        if should_record_key_combo(current_keys, key_text):
            combo_keys = sorted_combo_keys(current_keys)
            combo_text = make_combo_text(combo_keys)

            if combo_text not in _logged_combo_keys:
                _logged_combo_keys.add(combo_text)
                record_key_event(
                    input_type="key_combo",
                    key_text=key_text,
                    keys=combo_keys
                )
            return

        if key_text in MODIFIER_KEYS:
            return

        record_key_event(
            input_type="key_press",
            key_text=key_text,
            keys=None
        )

    except Exception:
        print("キー入力処理中にエラーが発生しました")
        traceback.print_exc()


def on_release(key):
    global _logged_combo_keys

    key_text = key_to_string(key)

    with _pressed_keys_lock:
        if key_text in _pressed_keys:
            _pressed_keys.remove(key_text)

        if not _pressed_keys:
            _logged_combo_keys = set()


# =========================
# 保存
# =========================

def save_input_logs():
    temp_dir = screen_capture.get_temp_dir()
    json_path = temp_dir / "input_logs.json"

    output = {
        "MacroName": _recording_dirs["macro_name"] if _recording_dirs else None,
        "Logs": _input_logs,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    print(f"input_logs.json 保存: {json_path}")


# =========================
# 外部公開関数
# =========================

def start_recording():
    global _mouse_listener
    global _keyboard_listener
    global _is_recording
    global _is_processing
    global _input_logs
    global _recording_dirs
    global _pending_click_event
    global _pending_click_timer
    global _latest_mouse_down_event
    global _event_index
    global _previous_screenshot_img
    global _pressed_keys
    global _logged_combo_keys
    global _processing_threads

    if _is_recording:
        print("すでに記録中です")
        return

    try:
        _recording_dirs = screen_capture.make_directory()

        _input_logs = []
        _event_index = 0

        _pending_click_event = None
        _pending_click_timer = None
        _latest_mouse_down_event = None

        _previous_screenshot_img = None

        _pressed_keys = set()
        _logged_combo_keys = set()

        _processing_threads = []

        _is_processing = False

        process_monitor.start_process_monitors()

        _is_recording = True

        _mouse_listener = mouse.Listener(
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
        print(f"temp: {_recording_dirs['temp_dir']}")
        print(f"images: {_recording_dirs['images_dir']}")

    except Exception:
        _is_recording = False
        process_monitor.stop_process_monitors()

        print("記録開始に失敗しました")
        traceback.print_exc()
        raise


def stop_recording():
    global _mouse_listener
    global _keyboard_listener
    global _is_recording
    global _pending_click_timer

    if not _is_recording:
        print("記録中ではありません")
        return

    try:
        _is_recording = False

        if _pending_click_timer is not None:
            _pending_click_timer.cancel()
            _pending_click_timer = None
            process_pending_single_click()

        wait_processing_threads()

        end_log = create_end_log()
        _input_logs.append(end_log)

        if _mouse_listener is not None:
            _mouse_listener.stop()
            _mouse_listener = None

        if _keyboard_listener is not None:
            _keyboard_listener.stop()
            _keyboard_listener = None

        process_monitor.stop_process_monitors()

        save_input_logs()

        print("記録を停止しました")
        print("recording_end: evt_End_pre.png")

    except Exception:
        print("記録停止中にエラーが発生しました")
        traceback.print_exc()
        raise


# =========================
# 単体実行テスト用
# =========================

if __name__ == "__main__":
    print("記録を開始します")
    print("終了するには Esc キーを押してください")

    start_recording()

    if _keyboard_listener is not None:
        _keyboard_listener.join()