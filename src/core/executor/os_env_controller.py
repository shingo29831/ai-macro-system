"""Module: @role: OSプロセスのDPIスケーリング・管理者権限・ホイール設定制御およびIME/カーソル制御のファサードを担当する。"""

import ctypes
import platform
import time

from .ime_controller import (
    IMC_SETOPENSTATUS,
    WM_IME_CONTROL,
    ensure_ime_state,
    get_focused_hwnd,
    get_ime_state,
    is_fullwidth,
    is_link_or_url,
    normalize_text_width,
    set_ime_state,
    should_input_as_fullwidth,
    should_input_as_halfwidth,
)
from .os_cursor_controller import (
    RUN_RECORD_CURSOR_IDS,
    SPIF_SENDCHANGE,
    SPIF_UPDATEINIFILE,
    SPI_SETCURSORS,
    restore_system_cursor,
    set_system_cursor,
)

SPI_GETWHEELSCROLLLINES = 0x0068
SPI_GETWHEELSCROLLCHARS = 0x006C
WHEEL_PAGESCROLL = 0xFFFFFFFF

_wheel_scroll_settings_cache = None
_wheel_scroll_settings_cache_time = 0.0

def get_wheel_scroll_settings() -> dict:
    """Why: Windows設定から1ノッチあたりのスクロール行数/文字数を取得し環境差による乖離を根絶"""
    global _wheel_scroll_settings_cache, _wheel_scroll_settings_cache_time
    import time
    now = time.time()
    if _wheel_scroll_settings_cache is not None and (now - _wheel_scroll_settings_cache_time < 5.0):
        return _wheel_scroll_settings_cache

    if platform.system() != "Windows":
        _wheel_scroll_settings_cache = {"scroll_lines": 3, "scroll_chars": 3, "is_page_scroll": False}
        _wheel_scroll_settings_cache_time = now
        return _wheel_scroll_settings_cache
    try:
        user32 = ctypes.windll.user32
        lines = ctypes.c_uint()
        chars = ctypes.c_uint()
        user32.SystemParametersInfoW(SPI_GETWHEELSCROLLLINES, 0, ctypes.byref(lines), 0)
        user32.SystemParametersInfoW(SPI_GETWHEELSCROLLCHARS, 0, ctypes.byref(chars), 0)
        is_page = (lines.value == WHEEL_PAGESCROLL)
        res = {
            "scroll_lines": 3 if is_page else max(1, lines.value),
            "scroll_chars": max(1, chars.value),
            "is_page_scroll": is_page
        }
        _wheel_scroll_settings_cache = res
        _wheel_scroll_settings_cache_time = now
        return res
    except Exception:
        return {"scroll_lines": 3, "scroll_chars": 3, "is_page_scroll": False}

# Why: 待機時(WAIT)・起動時(APPSTARTING)・文字選択(IBEAM)等でのユーザーカーソルチラつきを完全排除
# Why: 通常矢印・リンク手・作業中を最適化置換し、フック遅延ゼロとチラつき解消を両立
RUN_RECORD_CURSOR_IDS = [
    32512,  # OCR_NORMAL
    32649,  # OCR_HAND
    32650,  # OCR_APPSTARTING
]

import queue

_current_cursor_mode: str = "default"
_cur_cache: dict[str, Path] = {}
_cursor_lock = threading.RLock()
_auto_revert_timer: threading.Timer | None = None
_cursor_queue: queue.Queue = queue.Queue()
_cursor_worker_thread: threading.Thread | None = None
_cursor_worker_lock = threading.Lock()


def set_dpi_awareness():
    """WindowsプロセスのDPI Awarenessを高DPI対応モードに設定する"""
    if platform.system() == "Windows":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def is_running_as_admin() -> bool:
    """現在のプロセスがWindows管理者権限で実行されているかを判定する"""
    if platform.system() != "Windows":
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


__all__ = [
    "WM_IME_CONTROL",
    "IMC_SETOPENSTATUS",
    "SPI_SETCURSORS",
    "SPI_GETWHEELSCROLLLINES",
    "SPI_GETWHEELSCROLLCHARS",
    "WHEEL_PAGESCROLL",
    "SPIF_UPDATEINIFILE",
    "SPIF_SENDCHANGE",
    "RUN_RECORD_CURSOR_IDS",
    "get_wheel_scroll_settings",
    "set_dpi_awareness",
    "is_running_as_admin",
    "is_fullwidth",
    "should_input_as_halfwidth",
    "should_input_as_fullwidth",
    "is_link_or_url",
    "normalize_text_width",
    "get_focused_hwnd",
    "get_ime_state",
    "ensure_ime_state",
    "set_ime_state",
    "restore_system_cursor",
    "set_system_cursor",
]
