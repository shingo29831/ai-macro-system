# Role: OSプロセスのDPIスケーリング（DPI Awareness）およびIME入力モードの状態制御を担当する。

import platform
import ctypes
import time
import logging

logger = logging.getLogger(__name__)

# =========================
# 設定定数
# =========================

import atexit

WM_IME_CONTROL = 0x0283
IMC_SETOPENSTATUS = 0x0006
OCR_NORMAL = 32512
SPI_SETCURSORS = 0x0057

_cursor_changed = False


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


def set_ime_state(text: str = "", target_state: bool | None = None):
    """アクティブウィンドウのIME状態（全角/半角）を制御する"""
    if platform.system() != "Windows":
        return
    try:
        # Why: 日本語を含む場合はIMEをON、ASCIIのみは強制OFFにして半角英数入力の破壊を防止
        if target_state is None:
            has_japanese = any(ord(c) > 0x7F for c in text)
            open_status = 1 if has_japanese else 0
        else:
            open_status = 1 if target_state else 0

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return
        default_ime_wnd = ctypes.windll.imm32.ImmGetDefaultIMEWnd(hwnd)
        if default_ime_wnd:
            ctypes.windll.user32.SendMessageW(default_ime_wnd, WM_IME_CONTROL, IMC_SETOPENSTATUS, open_status)
            time.sleep(0.05)
    except Exception as e:
        logger.warning(f"Failed to set IME state: {e}")


def restore_system_cursor():
    """OSシステムカーソルを標準設定へ復元する"""
    global _cursor_changed
    if platform.system() != "Windows" or not _cursor_changed:
        return
    try:
        ctypes.windll.user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
        _cursor_changed = False
    except Exception as e:
        logger.warning(f"Failed to restore system cursor: {e}")


def set_system_cursor(mode: str = "default"):
    """
    システムの標準カーソルを録画・実行状態に合わせて動的に変更する。
    mode: 'record' (照準/十字), 'run' (アプリ起動中/待機), 'default' (復元)
    """
    global _cursor_changed
    if platform.system() != "Windows":
        return
    if mode == "default":
        restore_system_cursor()
        return

    cursor_map = {
        "record": 32515,  # IDC_CROSS
        "run": 32650,     # IDC_APPSTARTING
    }
    cursor_id = cursor_map.get(mode)
    if not cursor_id:
        return

    try:
        user32 = ctypes.windll.user32
        h_cursor = user32.LoadCursorW(0, cursor_id)
        if not h_cursor:
            return
        # Why: SetSystemCursorは渡されたハンドルを破棄するためCopyIconで複製して渡す
        h_copy = user32.CopyIcon(h_cursor)
        if h_copy:
            if user32.SetSystemCursor(h_copy, OCR_NORMAL):
                _cursor_changed = True
    except Exception as e:
        logger.warning(f"Failed to set system cursor ({mode}): {e}")


# Why: プロセス異常終了時もOSシステムカーソルを確実に標準へ復元
atexit.register(restore_system_cursor)
