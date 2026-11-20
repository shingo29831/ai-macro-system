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
    "検索",
    "スタート",
    "start",
    "search",
    "タスクバー",
    "taskbar",
    "cortana",
    "ジャンプ リスト",
    "タスクの切り替え",
    "task switching",
    "タスク ビュー",
    "task view",
    "プログラム マネージャー",
    "program manager",
]

_EXACT_IGNORED_SYSTEM_TITLES = frozenset({
    "記録中",
    "停止中",
    "設定",
    "実行中",
    "実行中...",
    "スナップ",
    "タスクの切り替え",
    "task switching",
    "タスク ビュー",
    "task view",
})

_SUBSTRING_IGNORED_SYSTEM_TITLES = (
    "aiマクロ生成中",
    "マクロ生成中",
    "ウィンドウの紐付け",
    "スナップ アシスト",
    "snap assist",
)

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

_BROWSER_WORD_RE = re.compile(
    r"(?:^|[\s\b])(firefox|chrome|msedge|edge|brave|opera)(?:$|[\s\b])",
    re.IGNORECASE,
)

_NON_BROWSER_APP_SUFFIXES = frozenset({
    "visual studio code",
    "code",
    "notepad",
    "メモ帳",
    "notepad++",
    "explorer",
    "エクスプローラー",
    "excel",
    "microsoft excel",
    "word",
    "microsoft word",
    "powerpoint",
    "microsoft powerpoint",
    "outlook",
    "microsoft outlook",
    "access",
    "microsoft access",
    "powershell",
    "windows powershell",
    "コマンド プロンプト",
    "cmd",
    "terminal",
    "windows terminal",
    "pycharm",
    "cursor",
})


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
    segments = [s.strip() for s in re.split(r"[\-—–―]", title) if s.strip()]
    if not segments:
        return False
    app_tail = segments[-1].lower()
    # Why: 非ブラウザアプリのファイル名に含まれる単語(chrome_driver.py, knowledge, operation等)の誤爆を防止
    if app_tail in _NON_BROWSER_APP_SUFFIXES:
        return False
    check_targets = segments[-2:] if len(segments) >= 2 else segments
    return any(bool(_BROWSER_WORD_RE.search(seg)) for seg in check_targets)


def get_browser_family(title: str) -> str:
    """タイトルがブラウザウィンドウを示す場合に正規化ブラウザ種別(chrome/edge/firefox/brave/opera)を返す。"""
    if not is_browser_window(title):
        return ""
    segments = [s.strip() for s in re.split(r"[\-—–―]", title) if s.strip()]
    for seg in reversed(segments[-2:] if len(segments) >= 2 else segments):
        m = _BROWSER_WORD_RE.search(seg)
        if m:
            fam = m.group(1).lower()
            return "edge" if fam == "msedge" else fam
    return ""


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
    # Why: Webページタイトルに「設定」「AI Macro System」等が含まれてもブラウザ操作は絶対に除外しない
    if is_browser_window(title):
        return False
    title_lower = title.lower().strip()
    if title_lower in _EXACT_IGNORED_SYSTEM_TITLES:
        return True
    if title_lower == "ai macro system" or title_lower.startswith("ai macro system "):
        return True
    return any(ignored in title_lower for ignored in _SUBSTRING_IGNORED_SYSTEM_TITLES)


def _strip_browser_title_suffix(window_title: str) -> str:
    """ブラウザウィンドウタイトルから末尾のブラウザ名を除いたページタイトル部分を返す。"""
    if not window_title:
        return ""
    parts = [p.strip() for p in re.split(r"[\-—–―]", window_title) if p.strip()]
    if len(parts) >= 2 and is_browser_window(parts[-1]):
        return " - ".join(parts[:-1]).strip().lower()
    return window_title.strip().lower()


def find_active_browser_document(
    win_ctrl: object,
    window_title: str = "",
    point: Optional[Tuple[int, int]] = None,
) -> object:
    """マルチタブ環境(Firefox/Chrome/Edge)で現在アクティブなタブの可視DocumentControlのみを特定する。"""
    try:
        import uiautomation as auto
    except Exception:
        return win_ctrl

    win_hwnd = getattr(win_ctrl, "NativeWindowHandle", 0) or 0
    win_rect = getattr(win_ctrl, "BoundingRectangle", None)

    def _belongs_to_window(ctrl: object) -> bool:
        if not win_hwnd:
            return True
        cur = ctrl
        for _ in range(32):
            if cur is None:
                break
            if (getattr(cur, "NativeWindowHandle", 0) or 0) == win_hwnd:
                return True
            try:
                cur = cur.GetParentControl()
            except Exception:
                break
        return False

    # Why: アクティブ領域の1点から親DocumentControlを逆引きすることで非表示バックグラウンドタブ混入をO(1)回避
    probe_points: List[Tuple[int, int]] = []
    if point is not None and win_rect is not None:
        px, py = int(point[0]), int(point[1])
        if win_rect.left <= px <= win_rect.right and (win_rect.top + 110) <= py <= win_rect.bottom:
            probe_points.append((px, py))
    if win_rect is not None and (win_rect.right - win_rect.left) > 100 and (win_rect.bottom - win_rect.top) > 160:
        cx = (win_rect.left + win_rect.right) // 2
        cy = max(win_rect.top + 140, (win_rect.top + win_rect.bottom) // 2)
        probe_points.append((cx, cy))

    for px, py in probe_points:
        try:
            pt_ctrl = auto.ControlFromPoint(px, py)
            cur = pt_ctrl
            for _ in range(32):
                if cur is None:
                    break
                if getattr(cur, "ControlTypeName", "") == "DocumentControl":
                    if not getattr(cur, "IsOffscreen", False) and _belongs_to_window(cur):
                        return cur
                    break
                cur = cur.GetParentControl()
        except Exception:
            continue

    # フォールバック: 浅層走査で可視かつタイトル一致・最大面積のDocumentControlを選択
    title_prefix = _strip_browser_title_suffix(window_title)
    visible_docs: List[Tuple[int, int, object]] = []
    try:
        for idx, (ctrl, _depth) in enumerate(auto.WalkControl(win_ctrl, maxDepth=10)):
            if idx >= 250:
                break
            if getattr(ctrl, "ControlTypeName", "") != "DocumentControl":
                continue
            if getattr(ctrl, "IsOffscreen", False):
                continue
            rect = getattr(ctrl, "BoundingRectangle", None)
            if not rect:
                continue
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w < 100 or h < 100:
                continue
            doc_name = str(getattr(ctrl, "Name", "") or "").strip().lower()
            title_score = 1 if (title_prefix and doc_name and (doc_name in title_prefix or title_prefix in doc_name)) else 0
            visible_docs.append((title_score, w * h, ctrl))
    except Exception:
        pass

    if visible_docs:
        visible_docs.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return visible_docs[0][2]

    try:
        doc = win_ctrl.DocumentControl()
        if doc and doc.Exists(0, 0) and not getattr(doc, "IsOffscreen", False):
            return doc
    except Exception:
        pass
    return win_ctrl


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
                if is_ignored_system_window(title):
                    rect = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        rects.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    user32.EnumWindows(enum_proc_type(enum_windows_proc), 0)
    return rects
