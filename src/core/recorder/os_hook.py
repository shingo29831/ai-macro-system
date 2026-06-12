# @role: ユーザーのOSレベルの入力操作（マウス・キーボード・ホイール）をフックし、操作ログとして記録する。
#
# 録画段階で取得するデータ:
#   - TimeStamp
#   - Type
#   - Content
#   - WindowName
#   - WindowSize
#   - WindowCoordinates
#   - CursorCoordinates
#
# mouse_scroll のみ:
#   - TimeStamp
#   - Type
#   - Content
#   - WindowName
#   のみ実データを入れる。
#   WindowName はアクティブウィンドウではなく、カーソル地点にあるウィンドウ名。
#   その他の項目は None。
#
# 画像取得:
#   - 画面全体: アクション直前と録画終了タイミング
#   - UI切り抜き画像: クリック時
#   - mouse_scroll は画像なし
#
# 保存:
#   ../../../macros/wf_連番/temp/input_logs.json
#   ../../../macros/wf_連番/images/evt_XXX_pre.png
#   ../../../macros/wf_連番/images/evt_XXX_crop.png

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
# python os_hook.py のように直接実行しても、
# 同じフォルダの screen_capturer.py / process_monitor.py を読めるようにする。

CURRENT_DIR = Path(__file__).resolve().parent

if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import screen_capturer as screen_capture
import process_monitor


# =========================
# 設定
# =========================

JSON_VERSION = "2.1"

DOUBLE_CLICK_INTERVAL_SEC = 0.35
DOUBLE_CLICK_MAX_DISTANCE = 8


# =========================
# 状態管理
# =========================

_mouse_listener: mouse.Listener | None = None
_keyboard_listener: keyboard.Listener | None = None

_is_recording = False
_is_processing = False

_input_logs: list[dict] = []

_recording_dirs: dict | None = None

_recording_end_event_no: str | None = None
_recording_end_full_image_ref: str | None = None

_pending_click_timer: threading.Timer | None = None
_pending_click_event: dict | None = None
_pending_click_lock = threading.Lock()

_latest_mouse_down_event: dict | None = None
_latest_mouse_down_lock = threading.Lock()

_event_index = 0
_event_index_lock = threading.Lock()


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


def key_to_string(key) -> str:
    try:
        return key.char
    except AttributeError:
        return str(key)


def next_event_no() -> str:
    """
    evt_001, evt_002... の番号を発行する。
    何番目のアクションかを追うための番号。
    """
    global _event_index

    with _event_index_lock:
        _event_index += 1
        return f"{_event_index:03d}"


def build_base_log(
    event_no: str,
    dt: datetime,
    input_type: str,
    content: dict | str,
    window_info: dict,
    cursor_x: int | None = None,
    cursor_y: int | None = None,
) -> dict:
    """
    クリック・キー入力用。
    指定された7項目を中心にログを作る。
    """
    window_fields = process_monitor.build_recording_window_fields(
        window_info,
        cursor_x=cursor_x,
        cursor_y=cursor_y
    )

    log = {
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
        },
        "Debug": {
            "WindowDebug": window_fields["WindowDebug"],
        },
    }

    return log


def build_scroll_log(
    event_no: str,
    dt: datetime,
    x: int,
    y: int,
    dx: int,
    dy: int,
) -> dict:
    """
    マウスホイール専用ログ。
    TimeStamp, Type, Content, WindowName のみ実データ。
    その他は None。
    """
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
        },
        "Debug": None,
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

        # 画面全体: アクション直前
        # mouse down時点で保持した画像を、確定後に evt_XXX_pre.png として保存する。
        pre_ref = screen_capture.save_pre_image_from_pil(
            event_no=event_no,
            img=event["pre_full_img"]
        )

        # UI切り抜き画像: クリック時
        crop = screen_capture.save_ui_crop(
            event_no=event_no,
            click_x=x,
            click_y=y
        )

        log["Images"] = {
            "Pre": pre_ref,
            "Crop": crop["ui_image_ref"],
        }

        log["Debug"]["UICropDebug"] = {
            "detection_method": crop["detection_method"],
            "screen_bbox": crop["screen_bbox"],
            "candidate_count": crop["candidate_count"],
            "selected_rect": crop["selected_rect"],
            "fallback_rect": crop["fallback_rect"],
        }

        _input_logs.append(log)

        print(f"クリックログ追加: evt_{event_no}")

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
    """
    mouse down時点で「実行直前画像」とWindow情報を取得する。
    release時点でシングル/ダブルクリック判定をする。
    """
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
            pre_full_img, _ = screen_capture.take_screenshot()

            down_event = {
                "event_no": event_no,
                "datetime": dt,
                "x": int(x),
                "y": int(y),
                "button": button,
                "window_info": window_info,
                "pre_full_img": pre_full_img,
            }

            with _latest_mouse_down_lock:
                _latest_mouse_down_event = down_event

            print(f"mouse down取得: evt_{event_no}, x={x}, y={y}, button={button}")

        except Exception:
            print("mouse down取得中にエラーが発生しました")
            traceback.print_exc()

        return

    # release時点
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

                # ダブルクリックは1回目のpre画像と同じEventNoで保存する。
                # 2回目down時に発行されたEventNoは使わないため欠番になる可能性がある。
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
    """
    マウスホイールログ。
    仕様:
      - TimeStamp
      - Type
      - Content
      - WindowName
    のみ実データを入れる。
    その他は None。
    WindowName はアクティブウィンドウではなく、カーソル地点のウィンドウ名。
    """
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


def on_press(key):
    if not _is_recording:
        return

    if key == keyboard.Key.esc:
        print("Esc が押されたため記録を停止します")
        stop_recording()
        return False

    try:
        event_no = next_event_no()
        dt = now_datetime()

        key_text = key_to_string(key)

        window_info = process_monitor.get_foreground_window_info()

        content = {
            "key": key_text,
        }

        log = build_base_log(
            event_no=event_no,
            dt=dt,
            input_type="key_press",
            content=content,
            window_info=window_info,
            cursor_x=None,
            cursor_y=None,
        )

        # 画面全体: アクション直前
        pre_ref = screen_capture.save_event_pre_image(
            event_no=event_no
        )

        log["Images"] = {
            "Pre": pre_ref,
            "Crop": None,
        }

        _input_logs.append(log)

        print(f"キー入力ログ追加: evt_{event_no}, key={key_text}")

    except Exception:
        print("キー入力処理中にエラーが発生しました")
        traceback.print_exc()


# =========================
# 保存
# =========================

def save_input_logs():
    temp_dir = screen_capture.get_temp_dir()
    json_path = temp_dir / "input_logs.json"

    output = {
        "JsonVersion": JSON_VERSION,
        "MacroName": _recording_dirs["macro_name"] if _recording_dirs else None,
        "MacroDirectory": _recording_dirs["macro_dir"] if _recording_dirs else None,
        "TempDirectory": _recording_dirs["temp_dir"] if _recording_dirs else None,
        "ImagesDirectory": _recording_dirs["images_dir"] if _recording_dirs else None,
        "RecordingEndEventNo": _recording_end_event_no,
        "RecordingEndFullImage": _recording_end_full_image_ref,
        "SavedAt": now_datetime().isoformat(),
        "LogCount": len(_input_logs),
        "Logs": _input_logs,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    print(f"input_logs.json 保存: {json_path}")


# =========================
# 外部公開関数
# =========================

def start_recording():
    """
    記録を開始し、フックリスナーを起動する。
    """
    global _mouse_listener
    global _keyboard_listener
    global _is_recording
    global _is_processing
    global _input_logs
    global _recording_dirs
    global _recording_end_event_no
    global _recording_end_full_image_ref
    global _pending_click_event
    global _pending_click_timer
    global _latest_mouse_down_event
    global _event_index

    if _is_recording:
        print("すでに記録中です")
        return

    try:
        _recording_dirs = screen_capture.make_directory()

        _input_logs = []
        _event_index = 0

        _recording_end_event_no = None
        _recording_end_full_image_ref = None

        _pending_click_event = None
        _pending_click_timer = None
        _latest_mouse_down_event = None

        _is_processing = False

        process_monitor.start_process_monitors()

        _is_recording = True

        _mouse_listener = mouse.Listener(
            on_click=on_click,
            on_scroll=on_scroll
        )
        _keyboard_listener = keyboard.Listener(on_press=on_press)

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
    """
    記録を停止し、リスナーを破棄して input_logs.json に保存する。
    """
    global _mouse_listener
    global _keyboard_listener
    global _is_recording
    global _pending_click_timer
    global _recording_end_event_no
    global _recording_end_full_image_ref

    if not _is_recording:
        print("記録中ではありません")
        return

    try:
        _is_recording = False

        if _pending_click_timer is not None:
            _pending_click_timer.cancel()
            _pending_click_timer = None
            process_pending_single_click()

        # 録画終了タイミング画像
        end_event_no = next_event_no()
        _recording_end_event_no = end_event_no
        _recording_end_full_image_ref = screen_capture.save_event_pre_image(
            event_no=end_event_no
        )

        if _mouse_listener is not None:
            _mouse_listener.stop()
            _mouse_listener = None

        if _keyboard_listener is not None:
            _keyboard_listener.stop()
            _keyboard_listener = None

        process_monitor.stop_process_monitors()

        save_input_logs()

        print("記録を停止しました")
        print(f"recording_end: evt_{_recording_end_event_no}_pre.png")

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