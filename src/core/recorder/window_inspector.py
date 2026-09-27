"""Module: @role: Windows APIおよびUI Automationを用いて、フォアグラウンドウィンドウやカーソル位置のUI要素矩形・テキスト情報を取得・診断する。"""

import ctypes
from ctypes import wintypes

try:
    import psutil
except ImportError:
    psutil = None

IGNORED_WINDOW_TITLES = [
    '記録中', '停止中', 'AI Macro System', '設定', 'AIマクロ生成中...', 'AIマクロ生成中', 'マクロ生成中', '実行中', '実行中...', 'ウィンドウの紐付け'
]

def should_ignore_window(title: str | None) -> bool:
    """システム自身の操作・設定ウィンドウかどうかを判定して除外対象とする"""
    if not title:
        return False
    title_lower = title.lower()
    for ignored in IGNORED_WINDOW_TITLES:
        if ignored.lower() in title_lower:
            return True
    return False

def _empty_window_info(error: str) -> dict:
    return {
        "success": False,
        "hwnd": 0,
        "title": "",
        "class_name": "",
        "process_id": None,
        "process_name": "",
        "exe_path": "",
        "rect": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        "size": {"width": 0, "height": 0},
        "coordinates": {"x": 0, "y": 0},
        "error": error,
    }

def get_foreground_window_info() -> dict:
    """現在アクティブなフォアグラウンドウィンドウの属性・サイズ・所属プロセス情報を取得する"""
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
        command_line = ""

        if process_id and psutil is not None:
            try:
                proc = psutil.Process(process_id)
                process_name = proc.name() or ""
                exe_path = proc.exe() or ""
                cmdline = proc.cmdline()
                if cmdline:
                    # 実行ファイルパスにスペースが含まれる場合を考慮してダブルクォートで囲む
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
            "error": None,
        }

    except Exception as e:
        return _empty_window_info(str(e))

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

    except Exception as e:
        print(f"[process_monitor] UIA rect取得エラー ({x}, {y}): {e}")
        return None
    finally:
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass
