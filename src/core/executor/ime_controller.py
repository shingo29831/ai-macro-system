"""Module: @role: Windows IME入力モード（全角/半角）の状態監視・切替制御および文字幅判定・URL正規化を担当する。"""

import ctypes
import logging
import platform
import re
import time
import unicodedata

logger = logging.getLogger(__name__)

WM_IME_CONTROL = 0x0283
IMC_SETOPENSTATUS = 0x0006


def is_fullwidth(text: str) -> bool:
    """文字列に全角文字が含まれているかを判定する"""
    return any(unicodedata.east_asian_width(c) in ('F', 'W', 'A') for c in text)


def should_input_as_halfwidth(text: str) -> bool:
    """テキストがリンク、URL、ドメイン、英数コード等、半角で入力すべきかを判定"""
    if is_link_or_url(text):
        return True
    has_fullwidth = any(unicodedata.east_asian_width(c) in ('F', 'W', 'A') for c in text)
    has_japanese = any('\u3040' <= c <= '\u309F' or '\u30A0' <= c <= '\u30FF' or '\u4E00' <= c <= '\u9FFF' for c in text)
    return not has_japanese and not has_fullwidth


def should_input_as_fullwidth(text: str) -> bool:
    """日本語文字（ひらがな、カタカナ、漢字）を含む全角文字入力であるかを判定"""
    return any('\u3040' <= c <= '\u309F' or '\u30A0' <= c <= '\u30FF' or '\u4E00' <= c <= '\u9FFF' for c in text)


def is_link_or_url(text: str) -> bool:
    """テキストがリンク（URL・ドメイン）であるかを判定する（全角・半角両対応）"""
    if not text:
        return False
    norm = unicodedata.normalize('NFKC', text).strip()
    norm_lower = norm.lower()
    if norm_lower.startswith(('http://', 'https://', 'ftp://', 'file://', 'www.')):
        return True
    domain_pattern = r'^[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z0-9][-a-zA-Z0-9.]*(/[^\s]*)?$'
    if bool(re.match(domain_pattern, norm_lower)):
        return True
    # Why: パス付きURLやパラメータ、全角混じりのWebアドレスを包括検知
    url_chars_pattern = r'^[a-zA-Z0-9\-._~:/?#\[\]@!$&\'()*+,;%=]+$'
    if ('.' in norm_lower or '/' in norm_lower) and bool(re.match(url_chars_pattern, norm_lower)):
        return True
    return False


def normalize_text_width(text: str) -> str:
    """リンクや英数記号の全角混入を検知し正規の半角形式へ自動変換"""
    if not text:
        return ""
    if is_link_or_url(text):
        return unicodedata.normalize('NFKC', text).strip()
    norm = unicodedata.normalize('NFKC', text)
    if any(prefix in norm.lower() for prefix in ['http://', 'https://', 'www.', '.html', '.php', '.com', '.jp', '.ac.jp', '.co.jp', '.org']):
        return norm.strip()
    return text


def get_focused_hwnd() -> int:
    """フォアグラウンドウィンドウまたは現在フォーカスのある子コントロールのHWNDを取得"""
    if platform.system() != "Windows":
        return 0
    try:
        user32 = ctypes.windll.user32
        fg_hwnd = user32.GetForegroundWindow()
        if not fg_hwnd:
            return 0
        thread_id = user32.GetWindowThreadProcessId(fg_hwnd, None)

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("hwndActive", ctypes.c_void_p),
                ("hwndFocus", ctypes.c_void_p),
                ("hwndCapture", ctypes.c_void_p),
                ("hwndMenuOwner", ctypes.c_void_p),
                ("hwndMoveSize", ctypes.c_void_p),
                ("hwndCaret", ctypes.c_void_p),
                ("rcCaret", RECT)
            ]

        gui_info = GUITHREADINFO()
        gui_info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(thread_id, ctypes.byref(gui_info)) and gui_info.hwndFocus:
            return int(gui_info.hwndFocus)
        return fg_hwnd
    except Exception:
        return ctypes.windll.user32.GetForegroundWindow() if platform.system() == "Windows" else 0


def get_ime_state(hwnd: int = 0) -> bool:
    """対象ウィンドウのIMEがON(全角)かOFF(半角)かを判定する"""
    if platform.system() != "Windows":
        return False
    target_hwnd = hwnd or get_focused_hwnd()
    if not target_hwnd:
        return False
    try:
        imm32 = ctypes.windll.imm32
        himc = imm32.ImmGetContext(target_hwnd)
        if himc:
            status = imm32.ImmGetOpenStatus(himc) != 0
            imm32.ImmReleaseContext(target_hwnd, himc)
            return status

        default_ime = imm32.ImmGetDefaultIMEWnd(target_hwnd)
        if default_ime:
            res = ctypes.windll.user32.SendMessageW(default_ime, WM_IME_CONTROL, 0x0005, 0)
            return res != 0
    except Exception as e:
        logger.debug(f"Failed to get IME state: {e}")
    return False


def ensure_ime_state(target_state: bool, timeout: float = 0.5) -> bool:
    """IMEの状況を監視し、指定された状態(全角:True, 半角:False)になるまで確実に制御"""
    if platform.system() != "Windows":
        return True
    start_t = time.time()

    while time.time() - start_t < timeout:
        hwnd = get_focused_hwnd()
        if get_ime_state(hwnd) == target_state:
            return True

        try:
            imm32 = ctypes.windll.imm32
            himc = imm32.ImmGetContext(hwnd)
            if himc:
                imm32.ImmSetOpenStatus(himc, 1 if target_state else 0)
                imm32.ImmReleaseContext(hwnd, himc)

            default_ime = imm32.ImmGetDefaultIMEWnd(hwnd)
            if default_ime:
                ctypes.windll.user32.SendMessageW(default_ime, WM_IME_CONTROL, IMC_SETOPENSTATUS, 1 if target_state else 0)
        except Exception:
            pass

        time.sleep(0.04)
        if get_ime_state(hwnd) == target_state:
            return True

    # タイムアウト時にキーボード送信による強制反転フォールバック
    hwnd = get_focused_hwnd()
    if get_ime_state(hwnd) != target_state:
        try:
            user32 = ctypes.windll.user32
            key_code = 0x1D if not target_state else 0x1C  # 無変換(0x1D) / 変換(0x1C)
            user32.keybd_event(key_code, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(key_code, 0, 2, 0)
            time.sleep(0.04)
        except Exception:
            pass

    return get_ime_state(hwnd) == target_state


def set_ime_state(text: str = "", target_state: bool | None = None):
    """アクティブウィンドウのIME状態（全角/半角）を制御する"""
    if platform.system() != "Windows":
        return
    if target_state is not None:
        ensure_ime_state(target_state, timeout=0.4)
    else:
        if is_link_or_url(text) or should_input_as_halfwidth(text):
            ensure_ime_state(False, timeout=0.4)
        elif should_input_as_fullwidth(text):
            ensure_ime_state(True, timeout=0.4)
