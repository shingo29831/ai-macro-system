# AI Role: Implements Windows API wrappers to detect IME state for capturing full-width input confirmations.

import ctypes
import logging

logger = logging.getLogger(__name__)

WM_IME_CONTROL = 0x0283
IMC_GETOPENSTATUS = 0x0005
IMC_GETCONVERSIONMODE = 0x0001
IME_CMODE_NATIVE = 0x0001
IME_CMODE_FULLSHAPE = 0x0008

def get_foreground_window() -> int:
    return ctypes.windll.user32.GetForegroundWindow()

def is_ime_active(hwnd: int = 0) -> bool:
    if hwnd == 0:
        hwnd = get_foreground_window()
    
    if not hwnd:
        return False

    try:
        # imm32.dll is required to interact with the Input Method Editor.
        imm32 = ctypes.windll.imm32
        user32 = ctypes.windll.user32
        
        ime_window = imm32.ImmGetDefaultIMEWnd(hwnd)
        if ime_window:
            status = user32.SendMessageW(ime_window, WM_IME_CONTROL, IMC_GETOPENSTATUS, 0)
            return status != 0
        return False
    except Exception as e:
        logger.error("Failed to retrieve IME open status: %s", e)
        return False

def is_full_width_mode(hwnd: int = 0) -> bool:
    if hwnd == 0:
        hwnd = get_foreground_window()
    
    if not hwnd:
        return False

    try:
        imm32 = ctypes.windll.imm32
        user32 = ctypes.windll.user32
        
        ime_window = imm32.ImmGetDefaultIMEWnd(hwnd)
        if ime_window:
            conversion_status = user32.SendMessageW(ime_window, WM_IME_CONTROL, IMC_GETCONVERSIONMODE, 0)
            return bool(conversion_status & (IME_CMODE_NATIVE | IME_CMODE_FULLSHAPE))
        return False
    except Exception as e:
        logger.error("Failed to retrieve IME conversion mode: %s", e)
        return False