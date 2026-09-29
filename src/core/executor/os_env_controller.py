# Role: OSプロセスのDPIスケーリング（DPI Awareness）およびIME入力モードの状態制御を担当する。

import platform
import ctypes
import time
import logging
logger = logging.getLogger(__name__)

# =========================
# 設定定数
# =========================

WM_IME_CONTROL = 0x0283
IMC_SETOPENSTATUS = 0x0006
SPI_SETCURSORS = 0x0057


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
    """OSシステムカーソルを標準設定へ復元する（チカチカ防止のためSetSystemCursorは不使用）"""
    if platform.system() != "Windows":
        return
    try:
        ctypes.windll.user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
    except Exception as e:
        logger.warning(f"Failed to restore system cursor: {e}")


def set_system_cursor(mode: str = "default"):
    """互換性インターフェース: チカチカを防止するためOSカーソル改変は行わない"""
    if mode == "default":
        restore_system_cursor()
