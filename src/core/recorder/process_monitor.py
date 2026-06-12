# @role: Windowsのアクティブウィンドウ情報・カーソル地点ウィンドウ情報・ETW/psutilによるプロセス起動監視を担当する。

from pathlib import Path
from datetime import datetime
from collections import deque
import threading
import time
import traceback
import ctypes
from ctypes import wintypes
import sys

try:
    import etw
except ImportError:
    etw = None

try:
    import psutil
except ImportError:
    psutil = None


# =========================
# 設定
# =========================

ENABLE_ADMIN_RELAUNCH = False

ENABLE_ETW_PROCESS_MONITOR = True
ENABLE_PSUTIL_PROCESS_MONITOR_FALLBACK = True

IGNORED_LAUNCH_PROCESS_NAMES = {
    "shellexperiencehost.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "applicationframehost.exe",
    "textinputhost.exe",
    "runtimebroker.exe",
    "conhost.exe",
    "python.exe",
    "pythonw.exe",
    "docker.exe",
    "docker desktop.exe",
    "com.docker.backend.exe",
    "com.docker.service.exe",
    "dockerd.exe",
    "containerd.exe",
    "wsl.exe",
    "wslhost.exe",
    "vmmem.exe",
}


# =========================
# 状態
# =========================

_latest_process_events = deque(maxlen=200)
_latest_process_events_lock = threading.Lock()

_etw_job = None
_etw_stop_event = threading.Event()
_psutil_stop_event = threading.Event()

_monitor_threads: list[threading.Thread] = []


# =========================
# 管理者権限
# =========================

def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_as_admin():
    """
    ETWを使う場合、環境によっては管理者権限が必要。
    GUIアプリから使う場合は勝手に再起動しない方がよいので、通常は False 推奨。
    """
    if is_admin():
        return

    script_path = str(Path(__file__).resolve())
    params = " ".join(
        [f'"{script_path}"'] + [f'"{arg}"' for arg in sys.argv[1:]]
    )

    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        params,
        None,
        1
    )

    if result <= 32:
        raise RuntimeError(f"管理者権限での再起動に失敗しました: ShellExecuteW={result}")

    sys.exit(0)


# =========================
# Window情報
# =========================

def _empty_window_info(error: str) -> dict:
    return {
        "success": False,
        "hwnd": 0,
        "title": "",
        "class_name": "",
        "process_id": None,
        "process_name": "",
        "exe_path": "",
        "rect": {
            "left": 0,
            "top": 0,
            "right": 0,
            "bottom": 0,
        },
        "size": {
            "width": 0,
            "height": 0,
        },
        "coordinates": {
            "x": 0,
            "y": 0,
        },
        "error": error,
    }


def get_foreground_window_info() -> dict:
    """
    アクティブウィンドウの名前・サイズ・絶対座標を取得する。
    """
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()

        if not hwnd:
            return _empty_window_info("GetForegroundWindow returned 0")

        title_buffer = ctypes.create_unicode_buffer(512)
        class_buffer = ctypes.create_unicode_buffer(256)

        user32.GetWindowTextW(hwnd, title_buffer, 512)
        user32.GetClassNameW(hwnd, class_buffer, 256)

        rect = wintypes.RECT()
        rect_ok = user32.GetWindowRect(hwnd, ctypes.byref(rect))

        if rect_ok:
            left = int(rect.left)
            top = int(rect.top)
            right = int(rect.right)
            bottom = int(rect.bottom)
            width = max(0, right - left)
            height = max(0, bottom - top)
        else:
            left = top = right = bottom = width = height = 0

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_id = int(pid.value) if pid.value else None

        process_name = ""
        exe_path = ""

        if process_id and psutil is not None:
            try:
                proc = psutil.Process(process_id)
                process_name = proc.name() or ""
                exe_path = proc.exe() or ""
            except Exception:
                pass

        return {
            "success": True,
            "hwnd": int(hwnd),
            "title": title_buffer.value or "",
            "class_name": class_buffer.value or "",
            "process_id": process_id,
            "process_name": process_name,
            "exe_path": exe_path,
            "rect": {
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
            },
            "size": {
                "width": width,
                "height": height,
            },
            "coordinates": {
                "x": left,
                "y": top,
            },
            "error": None,
        }

    except Exception as e:
        return _empty_window_info(str(e))


def build_recording_window_fields(
    window_info: dict,
    cursor_x: int | None = None,
    cursor_y: int | None = None
) -> dict:
    """
    ユーザー指定のログ項目に合わせたWindow情報へ変換する。
    CursorCoordinates はアクティブウィンドウ左上からの相対座標。
    """
    left = int(window_info.get("rect", {}).get("left", 0))
    top = int(window_info.get("rect", {}).get("top", 0))

    if cursor_x is None or cursor_y is None:
        cursor_coordinates = None
    else:
        cursor_coordinates = {
            "x": int(cursor_x - left),
            "y": int(cursor_y - top),
        }

    return {
        "WindowName": window_info.get("title", ""),
        "WindowSize": {
            "width": int(window_info.get("size", {}).get("width", 0)),
            "height": int(window_info.get("size", {}).get("height", 0)),
        },
        "WindowCoordinates": {
            "x": left,
            "y": top,
        },
        "CursorCoordinates": cursor_coordinates,
        "WindowDebug": {
            "hwnd": window_info.get("hwnd"),
            "class_name": window_info.get("class_name"),
            "process_id": window_info.get("process_id"),
            "process_name": window_info.get("process_name"),
            "exe_path": window_info.get("exe_path"),
            "success": window_info.get("success"),
            "error": window_info.get("error"),
        },
    }


def get_window_title_at_point(x: int, y: int) -> dict:
    """
    カーソル地点にあるウィンドウ名を取得する。
    mouse_scroll 用。
    アクティブウィンドウではなく、座標上のウィンドウを取得する。
    """
    try:
        user32 = ctypes.windll.user32

        point = wintypes.POINT(int(x), int(y))
        hwnd = user32.WindowFromPoint(point)

        if not hwnd:
            return {
                "success": False,
                "title": "",
                "hwnd": 0,
                "class_name": "",
                "root_hwnd": 0,
                "root_title": "",
                "root_class_name": "",
                "error": "WindowFromPoint returned 0",
            }

        title_buffer = ctypes.create_unicode_buffer(512)
        class_buffer = ctypes.create_unicode_buffer(256)

        user32.GetWindowTextW(hwnd, title_buffer, 512)
        user32.GetClassNameW(hwnd, class_buffer, 256)

        GA_ROOT = 2
        root_hwnd = user32.GetAncestor(hwnd, GA_ROOT)

        root_title_buffer = ctypes.create_unicode_buffer(512)
        root_class_buffer = ctypes.create_unicode_buffer(256)

        if root_hwnd:
            user32.GetWindowTextW(root_hwnd, root_title_buffer, 512)
            user32.GetClassNameW(root_hwnd, root_class_buffer, 256)

        title = title_buffer.value or ""
        root_title = root_title_buffer.value or ""

        # 子要素のtitleが空になりやすいため、root側を優先的に使う
        display_title = root_title or title

        return {
            "success": True,
            "title": display_title,
            "hwnd": int(hwnd),
            "class_name": class_buffer.value or "",
            "root_hwnd": int(root_hwnd) if root_hwnd else 0,
            "root_title": root_title,
            "root_class_name": root_class_buffer.value or "",
            "error": None,
        }

    except Exception as e:
        return {
            "success": False,
            "title": "",
            "hwnd": 0,
            "class_name": "",
            "root_hwnd": 0,
            "root_title": "",
            "root_class_name": "",
            "error": str(e),
        }


# =========================
# プロセス監視
# =========================

def normalize_process_name(name: str | None) -> str:
    return (name or "").strip().lower()


def should_ignore_process_name(name: str | None) -> bool:
    return normalize_process_name(name) in IGNORED_LAUNCH_PROCESS_NAMES


def add_process_event(process_data: dict):
    app_name = process_data.get("app_name", "")
    process_data["is_ignored_process_name"] = should_ignore_process_name(app_name)

    with _latest_process_events_lock:
        _latest_process_events.append(process_data)

    ignored_text = " ignored" if process_data.get("is_ignored_process_name") else ""
    print(f"process launch detected{ignored_text}: {app_name} ({process_data.get('source')})")


def extract_etw_process_data(event) -> dict | None:
    try:
        if isinstance(event, (list, tuple)) and len(event) >= 2:
            data = event[1]
        else:
            data = event

        if not isinstance(data, dict):
            return None

        image_name = (
            data.get("ImageName")
            or data.get("ProcessName")
            or data.get("FileName")
            or data.get("ImageFileName")
            or data.get("ExecutablePath")
            or ""
        )

        process_id = (
            data.get("ProcessID")
            or data.get("ProcessId")
            or data.get("PID")
        )

        parent_process_id = (
            data.get("ParentProcessID")
            or data.get("ParentProcessId")
            or data.get("PPID")
        )

        command_line = (
            data.get("CommandLine")
            or data.get("CmdLine")
            or ""
        )

        app_path = str(image_name) if image_name else ""
        app_name = Path(app_path).name if app_path else ""

        if not app_name:
            return None

        return {
            "timestamp": datetime.now().astimezone(),
            "app_name": app_name,
            "app_path": app_path,
            "process_id": int(process_id) if process_id is not None else None,
            "parent_process_id": int(parent_process_id) if parent_process_id is not None else None,
            "command_line": command_line,
            "source": "etw_process_start",
            "raw": data,
        }

    except Exception:
        return None


def on_etw_process_start(event):
    process_data = extract_etw_process_data(event)

    if process_data is None:
        return

    add_process_event(process_data)


def psutil_process_monitor_worker():
    if psutil is None:
        print("psutil がインストールされていないため、psutil監視を開始できません")
        print("PowerShellで pip install psutil を実行してください")
        return

    print("psutilプロセス起動監視を開始しました")

    try:
        known_pids = set(psutil.pids())
    except Exception:
        known_pids = set()

    while not _psutil_stop_event.is_set():
        try:
            current_pids = set(psutil.pids())
            new_pids = current_pids - known_pids
            known_pids = current_pids

            for pid in new_pids:
                try:
                    proc = psutil.Process(pid)

                    app_name = proc.name() or ""
                    if not app_name:
                        continue

                    app_path = ""
                    command_line = ""

                    try:
                        app_path = proc.exe() or ""
                    except Exception:
                        pass

                    try:
                        command_line = " ".join(proc.cmdline())
                    except Exception:
                        pass

                    process_data = {
                        "timestamp": datetime.now().astimezone(),
                        "app_name": app_name,
                        "app_path": app_path,
                        "process_id": int(pid),
                        "parent_process_id": proc.ppid(),
                        "command_line": command_line,
                        "source": "psutil_process_polling",
                        "raw": None,
                    }

                    add_process_event(process_data)

                except Exception:
                    pass

        except Exception:
            pass

        time.sleep(0.1)

    print("psutilプロセス起動監視を停止しました")


def etw_process_monitor_worker():
    global _etw_job

    if etw is None:
        print("pywintrace がインストールされていないため、ETW監視を開始できません")
        print("PowerShellで pip install pywintrace を実行してください")

        if ENABLE_PSUTIL_PROCESS_MONITOR_FALLBACK:
            psutil_process_monitor_worker()

        return

    try:
        providers = [
            etw.ProviderInfo(
                "Microsoft-Windows-Kernel-Process",
                etw.GUID("{22FB2CD6-0E7B-422B-A0C7-2FAD1FD0E716}")
            )
        ]

        _etw_job = etw.ETW(
            providers=providers,
            event_callback=on_etw_process_start,
            task_name_filters="PROCESSSTART"
        )

        _etw_job.start()
        print("ETWプロセス起動監視を開始しました")

        while not _etw_stop_event.is_set():
            time.sleep(0.2)

    except PermissionError:
        print("ETWが権限不足で開始できませんでした。psutil監視に切り替えます。")
        traceback.print_exc()

        if ENABLE_PSUTIL_PROCESS_MONITOR_FALLBACK:
            psutil_process_monitor_worker()

    except Exception:
        print("ETW監視中にエラーが発生しました。psutil監視に切り替えます。")
        traceback.print_exc()

        if ENABLE_PSUTIL_PROCESS_MONITOR_FALLBACK:
            psutil_process_monitor_worker()

    finally:
        try:
            if _etw_job is not None:
                _etw_job.stop()
                print("ETWプロセス起動監視を停止しました")
        except Exception:
            pass


def start_process_monitors():
    global _monitor_threads

    _etw_stop_event.clear()
    _psutil_stop_event.clear()
    _monitor_threads = []

    if ENABLE_ADMIN_RELAUNCH:
        relaunch_as_admin()

    if ENABLE_ETW_PROCESS_MONITOR:
        thread = threading.Thread(
            target=etw_process_monitor_worker,
            daemon=True
        )
        thread.start()
        _monitor_threads.append(thread)

    elif ENABLE_PSUTIL_PROCESS_MONITOR_FALLBACK:
        thread = threading.Thread(
            target=psutil_process_monitor_worker,
            daemon=True
        )
        thread.start()
        _monitor_threads.append(thread)


def stop_process_monitors():
    _etw_stop_event.set()
    _psutil_stop_event.set()


def get_process_events_after(since_datetime: datetime, limit: int = 20) -> list[dict]:
    with _latest_process_events_lock:
        candidates = [
            process_data
            for process_data in list(_latest_process_events)
            if process_data["timestamp"] >= since_datetime
        ]

    candidates.sort(key=lambda item: item.get("timestamp"))
    return candidates[:limit]