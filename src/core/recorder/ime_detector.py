# @role: Windows APIを利用して現在のIME入力モード（全角/半角）を取得する。

import ctypes
import logging

logger = logging.getLogger(__name__)

WM_IME_CONTROL = 0x0283
IMC_GETOPENSTATUS = 0x0005

def get_foreground_window() -> int:
    """現在アクティブなウィンドウのハンドルを取得する。"""
    return ctypes.windll.user32.GetForegroundWindow()

def is_ime_active(hwnd: int = 0) -> bool:
    """
    対象ウィンドウのIMEがオン（全角入力モード）かどうかを判定する。
    hwndが0の場合は現在アクティブなウィンドウを対象とする。
    """
    if hwnd == 0:
        hwnd = get_foreground_window()
    
    if not hwnd:
        return False

    try:
        imm32 = ctypes.windll.imm32
        user32 = ctypes.windll.user32
        
        ime_window = imm32.ImmGetDefaultIMEWnd(hwnd)
        if ime_window:
            status = user32.SendMessageW(ime_window, WM_IME_CONTROL, IMC_GETOPENSTATUS, 0)
            return status != 0
        return False
    except Exception as e:
        logger.error("IME状態の取得に失敗しました: %s", e)
        return False