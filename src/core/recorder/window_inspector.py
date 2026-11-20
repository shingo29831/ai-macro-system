"""Module: @role: Windows APIおよびUI Automationを用いて、フォアグラウンドウィンドウやカーソル位置のUI要素矩形・テキスト情報を取得・診断する。"""

import ctypes
import os
from ctypes import wintypes

from core.shared.window_utils import IGNORED_SYSTEM_WINDOW_TITLES, is_ignored_system_window

try:
    import psutil
except ImportError:
    psutil = None

IGNORED_WINDOW_TITLES = IGNORED_SYSTEM_WINDOW_TITLES + ["スナップ アシスト", "スナップ", "snap assist"]

_NON_SYSTEM_USER_PROCESSES = frozenset({
    "firefox.exe",
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "opera.exe",
    "excel.exe",
    "winword.exe",
    "powerpnt.exe",
    "outlook.exe",
    "msaccess.exe",
    "code.exe",
    "notepad.exe",
})


def should_ignore_window(title: str | None, window_info: dict | None = None) -> bool:
    """システム自身の操作・設定ウィンドウかどうかを判定して除外対象とする"""
    if isinstance(window_info, dict):
        pid = window_info.get("process_id")
        # Why: 自プロセスウィンドウはタイトルに依らず確実除外し、外部ブラウザ等の誤除外を完全防止
        if pid and int(pid) == os.getpid():
            return True
        proc_name = str(window_info.get("process_name") or "").lower().strip()
        if proc_name in _NON_SYSTEM_USER_PROCESSES:
            return False
    return is_ignored_system_window(title)

def _empty_window_info(error: str) -> dict:
    return {
        "success": False,
        "hwnd": 0,
        "title": "",
        "class_name": "",
        "process_id": None,
        "process_name": "",
        "exe_path": "",
        "command_line": "",
        "rect": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        "size": {"width": 0, "height": 0},
        "coordinates": {"x": 0, "y": 0},
        "is_maximized": False,
        "monitor": None,
        "error": error,
    }

def _build_window_info_from_hwnd(hwnd: int) -> dict:
    user32 = ctypes.windll.user32
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

    is_maximized = bool(user32.IsZoomed(hwnd))
    monitor_info = {"left": 0, "top": 0, "right": 0, "bottom": 0, "is_primary": True}
    try:
        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        class _MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]
        h_mon = user32.MonitorFromWindow(hwnd, 2)
        if h_mon:
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            if user32.GetMonitorInfoW(h_mon, ctypes.byref(mi)):
                monitor_info = {
                    "left": int(mi.rcMonitor.left),
                    "top": int(mi.rcMonitor.top),
                    "right": int(mi.rcMonitor.right),
                    "bottom": int(mi.rcMonitor.bottom),
                    "is_primary": bool(mi.dwFlags & 1),
                }
    except Exception:
        pass

    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_id = int(pid.value) if pid.value else None

    process_name = ""
    exe_path = ""
    command_line = ""

    if process_id and psutil is not None:
        try:
            proc = psutil.Process(process_id)
            process_name = proc.name() or ""
            exe_path = proc.exe() or ""
            cmdline = proc.cmdline()
            if cmdline:
                cmdline[0] = f'"{cmdline[0]}"'
                command_line = " ".join(cmdline)
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
        "command_line": command_line,
        "rect": {"left": left, "top": top, "right": right, "bottom": bottom},
        "size": {"width": width, "height": height},
        "coordinates": {"x": left, "y": top},
        "is_maximized": is_maximized,
        "monitor": monitor_info,
        "error": None,
    }


def get_foreground_window_info() -> dict:
    """現在アクティブなフォアグラウンドウィンドウの属性・サイズ・所属プロセス情報を取得する"""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return _empty_window_info("GetForegroundWindow returned 0")
        return _build_window_info_from_hwnd(int(hwnd))
    except Exception as e:
        return _empty_window_info(str(e))


def get_window_info_at_point(x: int, y: int) -> dict:
    """クリック座標直下のルートウィンドウ情報を取得し、ウィンドウ切替直前の旧FG誤帰属を防止する。"""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if hwnd:
            root_hwnd = user32.GetAncestor(hwnd, 2) or hwnd
            info = _build_window_info_from_hwnd(int(root_hwnd))
            # Why: ブラウザの<select>ポップアップ等タイトル空の同一プロセス子窓は親FGウィンドウ情報へ統合
            if not info.get("title"):
                fg_info = get_foreground_window_info()
                if (
                    fg_info.get("success")
                    and fg_info.get("title")
                    and info.get("process_name")
                    and info["process_name"].lower() == str(fg_info.get("process_name") or "").lower()
                ):
                    return fg_info
            return info
    except Exception:
        pass
    return get_foreground_window_info()

def build_recording_window_fields(
    window_info: dict,
    cursor_x: int | None = None,
    cursor_y: int | None = None,
) -> dict:
    """ログ出力用にウィンドウ座標系とカーソル相対座標の辞書を整形する"""
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
        "ProcessName": window_info.get("process_name", ""),
        "WindowCommandLine": window_info.get("command_line", ""),
        "WindowSize": {
            "width": int(window_info.get("size", {}).get("width", 0)),
            "height": int(window_info.get("size", {}).get("height", 0)),
        },
        "WindowCoordinates": {
            "x": left,
            "y": top,
        },
        "CursorCoordinates": cursor_coordinates,
        "IsMaximized": window_info.get("is_maximized", False),
        "Monitor": window_info.get("monitor"),
    }

def get_window_title_at_point(x: int, y: int) -> dict:
    """カーソル指定地点にあるウィンドウのタイトルおよび祖先ウィンドウ情報を取得する"""
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
                "process_name": "",
                "command_line": "",
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
        display_title = root_title or title

        target_hwnd = int(root_hwnd) if root_hwnd else int(hwnd)
        process_name = ""
        command_line = ""
        if target_hwnd and psutil is not None:
            try:
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(target_hwnd, ctypes.byref(pid))
                if pid.value:
                    proc = psutil.Process(int(pid.value))
                    process_name = proc.name() or ""
                    cmdline = proc.cmdline()
                    if cmdline:
                        cmdline[0] = f'"{cmdline[0]}"'
                        command_line = " ".join(cmdline)
            except Exception:
                pass

        return {
            "success": True,
            "title": display_title,
            "hwnd": int(hwnd),
            "class_name": class_buffer.value or "",
            "process_name": process_name,
            "command_line": command_line,
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
            "process_name": "",
            "command_line": "",
            "root_hwnd": 0,
            "root_title": "",
            "root_class_name": "",
            "error": str(e),
        }

def get_ui_element_rect_at_point(x: int, y: int) -> dict | None:
    """
    カーソル地点のUI要素矩形と内部テキストをUI Automationで取得し、
    診断情報をログに付与して返す。取れない場合は None。
    """
    try:
        from pywinauto import Desktop
        import pythoncom

        # 別スレッドからの呼び出しを考慮し、COM環境を安全に初期化
        pythoncom.CoInitialize()

        desktop = Desktop(backend="uia")
        element = desktop.from_point(int(x), int(y))
        rect = element.rectangle()

        left = int(rect.left)
        top = int(rect.top)
        right = int(rect.right)
        bottom = int(rect.bottom)

        width = right - left
        height = bottom - top

        if width <= 0 or height <= 0:
            return None

        # 画面全体に近すぎる巨大要素は「UIのみ切り抜き」として不適切なため除外
        if width > 2000 or height > 1500:
            return None

        name = ""
        control_type = ""
        value_text = ""
        debug_info = []

        try:
            name = element.window_text() or ""
            debug_info.append(f"Name: {name}")
        except Exception as e:
            debug_info.append(f"Name Error: {e}")

        try:
            control_type = getattr(element.element_info, "control_type", "") or ""
            debug_info.append(f"Type: {control_type}")
        except Exception as e:
            debug_info.append(f"Type Error: {e}")

        # テキスト値の積極的な抽出
        try:
            if element.is_value_pattern_available():
                value_text = element.get_value() or ""
                debug_info.append(f"ValuePattern: {value_text}")
        except Exception as e:
            debug_info.append(f"ValuePattern Error: {e}")

        if not value_text:
            try:
                value_text = element.legacy_properties().get("Value", "") or ""
                debug_info.append(f"LegacyValue: {value_text}")
            except Exception:
                pass

        return {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": width,
            "height": height,
            "name": name,
            "control_type": control_type,
            "value": value_text,
            "source": "uia",
            "uia_debug": " | ".join(debug_info),
        }

    except Exception:
        # UIA未対応UIや境界座標でのCOMError(E_INVALIDARG等)発生時は安全にNoneへフォールバック
        return None
    finally:
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass
