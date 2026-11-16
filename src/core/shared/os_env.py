"""Module: @role: OSプロセスのDPIスケーリング・管理者権限・ホイール設定制御およびIME/カーソル制御の共有ファサードを提供する。"""

import ctypes
import logging
import platform
import time

from core.shared.ime_controller import (
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
from core.shared.os_cursor_controller import (
    RUN_RECORD_CURSOR_IDS,
    SPIF_SENDCHANGE,
    SPIF_UPDATEINIFILE,
    SPI_SETCURSORS,
    restore_system_cursor,
    set_system_cursor,
)

logger = logging.getLogger(__name__)

SPI_GETWHEELSCROLLLINES = 0x0068
SPI_GETWHEELSCROLLCHARS = 0x006C
WHEEL_PAGESCROLL = 0xFFFFFFFF

_wheel_scroll_settings_cache = None
_wheel_scroll_settings_cache_time = 0.0


def get_wheel_scroll_settings() -> dict:
    """Windows設定から1ノッチあたりのスクロール行数/文字数を取得する。"""
    global _wheel_scroll_settings_cache, _wheel_scroll_settings_cache_time
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
        is_page = lines.value == WHEEL_PAGESCROLL
        res = {
            "scroll_lines": 3 if is_page else max(1, lines.value),
            "scroll_chars": max(1, chars.value),
            "is_page_scroll": is_page,
        }
        _wheel_scroll_settings_cache = res
        _wheel_scroll_settings_cache_time = now
        return res
    except Exception as e:
        logger.debug(f"Failed to query wheel scroll settings: {e}")
        return {"scroll_lines": 3, "scroll_chars": 3, "is_page_scroll": False}


def set_dpi_awareness() -> None:
    """WindowsプロセスのDPI Awarenessを高DPI対応モードに設定する。"""
    if platform.system() == "Windows":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception as e_shcore:
            logger.debug(f"SetProcessDpiAwareness fallback: {e_shcore}")
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception as e_user32:
                logger.debug(f"SetProcessDPIAware failed: {e_user32}")


def is_running_as_admin() -> bool:
    """現在のプロセスがWindows管理者権限で実行されているかを判定する。"""
    if platform.system() != "Windows":
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception as e:
        logger.debug(f"IsUserAnAdmin failed: {e}")
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
