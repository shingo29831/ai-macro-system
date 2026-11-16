"""Module: @role: OSウィンドウ矩形取得・タイトル解析・システムウィンドウ判定の共通ユーティリティを提供する。"""

import ctypes
import logging
import platform
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

IGNORED_SYSTEM_WINDOW_TITLES = [
    "記録中",
    "停止中",
    "AI Macro System",
    "設定",
    "AIマクロ生成中...",
    "AIマクロ生成中",
    "マクロ生成中",
    "実行中",
    "実行中...",
    "ウィンドウの紐付け",
]

SYSTEM_EXACT_WINDOWS = [
    "検索", "スタート", "start", "search", "タスクバー", "taskbar", "cortana", "ジャンプ リスト",
]

SYSTEM_WINDOW_KEYWORDS = [
    "program manager",
    "ジャンプ リスト",
    "taskbar",
    "cortana",
    "検索",
    "geforce overlay",
    "nvidia share",
    "overlay",
]

GENERATOR_SYSTEM_CONTAINS = [
    "python",
    "unknown window",
    "マクロ生成中",
    "aiマクロ生成中",
    "ai macro system",
    "記録中",
    "停止中",
    "実行中",
    "設定",
    "ウィンドウの紐付け",
]

BROWSER_KEYWORDS = ("firefox", "chrome", "edge", "brave", "opera")


def extract_app_name(title: str, default: str = "") -> str:
    """ウィンドウタイトルの区切り文字以降からアプリケーション名を抽出する。"""
    if not title:
        return default
    parts = re.split(r"[\-—–―]", title)
    app = parts[-1].strip()
    return app if app else (title.strip() or default)


def is_browser_window(title: str) -> bool:
    """ウィンドウタイトルが主要ブラウザを示すかを判定する。"""
    if not title:
        return False
    lower = title.lower()
    return any(b in lower for b in BROWSER_KEYWORDS)


def is_system_shell_window(title: str, check_keywords_for_browser: bool = False) -> bool:
    """OSシェルやシステム自身のウィンドウであり自動化対象外かを判定する。"""
    w_lower = (title or "").lower().strip()
    if not w_lower:
        return True
    if is_browser_window(w_lower):
        if check_keywords_for_browser:
            return any(sc in w_lower for sc in GENERATOR_SYSTEM_CONTAINS)
        return False
    return w_lower in SYSTEM_EXACT_WINDOWS or any(sc in w_lower for sc in GENERATOR_SYSTEM_CONTAINS)


def is_ignored_system_window(title: Optional[str]) -> bool:
    """録画・UIマスク対象から除外すべきシステム/ツール自身ウィンドウかを判定する。"""
    if not title:
        return False
    title_lower = title.lower()
    extra = ["スナップ アシスト", "スナップ", "snap assist"]
    return any(ignored.lower() in title_lower for ignored in (IGNORED_SYSTEM_WINDOW_TITLES + extra))


def get_window_bounds(hwnd: Optional[int]) -> Optional[Tuple[int, int, int, int]]:
    """指定HWNDのウィンドウ境界 (left, top, right, bottom) を取得する。"""
    if not hwnd or platform.system() != "Windows":
        return None
    try:
        class _RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = _RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception as e:
        logger.debug(f"GetWindowRect failed for hwnd={hwnd}: {e}")
    return None


def get_window_rect_xywh(hwnd: Optional[int]) -> Optional[Tuple[int, int, int, int]]:
    """指定HWNDのウィンドウ矩形 (x, y, width, height) を取得する。"""
    bounds = get_window_bounds(hwnd)
    if not bounds:
        return None
    left, top, right, bottom = bounds
    return (left, top, right - left, bottom - top)


def get_system_window_rects() -> List[Tuple[int, int, int, int]]:
    """画像比較時にマスク除外すべき自システムウィンドウの矩形一覧を返す。"""
    rects: List[Tuple[int, int, int, int]] = []
    if platform.system() != "Windows":
        return rects

    from ctypes import wintypes

    user32 = ctypes.windll.user32

    def enum_windows_proc(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                if any(ignored in title for ignored in IGNORED_SYSTEM_WINDOW_TITLES):
                    rect = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        rects.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    user32.EnumWindows(enum_proc_type(enum_windows_proc), 0)
    return rects
