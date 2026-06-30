# @role: pynputでは取得できない水平スクロールやタッチパッドの微細なスクロールを捕捉するためのWindows低レベルAPIフック。

import ctypes
from ctypes import wintypes
import threading
import traceback
from core.recorder.state import state
from core.recorder.event_processor import record_scroll_event

WH_MOUSE_LL = 14
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WM_QUIT = 0x0012
WHEEL_DELTA = 120

LRESULT = ctypes.c_ssize_t
HHOOK = wintypes.HANDLE
HINSTANCE = wintypes.HANDLE
DWORD = wintypes.DWORD

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]

LowLevelMouseProc = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelMouseProc, HINSTANCE, DWORD]
user32.SetWindowsHookExW.restype = HHOOK
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = DWORD

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = HINSTANCE

def native_scroll_hook_callback(n_code, w_param, l_param):
    if n_code >= 0 and state.is_recording and not state.is_stopping:
        if w_param in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            try:
                state.cancel_hover()
                mouse_info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                delta = ctypes.c_short((mouse_info.mouseData >> 16) & 0xFFFF).value / WHEEL_DELTA
                dx, dy = (0.0, delta) if w_param == WM_MOUSEWHEEL else (delta, 0.0)
                record_scroll_event(x=int(mouse_info.pt.x), y=int(mouse_info.pt.y), dx=dx, dy=dy, source="win_scroll")
            except Exception:
                traceback.print_exc()
    return user32.CallNextHookEx(state.native_scroll_hook_handle, n_code, w_param, l_param)

def native_scroll_hook_worker():
    try:
        state.native_scroll_hook_thread_id = kernel32.GetCurrentThreadId()
        state.native_scroll_hook_callback = LowLevelMouseProc(native_scroll_hook_callback)
        module_handle = kernel32.GetModuleHandleW(None)
        state.native_scroll_hook_handle = user32.SetWindowsHookExW(WH_MOUSE_LL, state.native_scroll_hook_callback, module_handle, 0)
        
        if not state.native_scroll_hook_handle:
            error_code = ctypes.get_last_error()
            print(f"Windowsスクロールフックの開始に失敗しました。 WinError={error_code}")
            state.native_scroll_hook_active = False
            state.native_scroll_hook_ready.set()
            return
            
        state.native_scroll_hook_active = True
        state.native_scroll_hook_ready.set()
        print("Windowsスクロールフックを開始しました")
        
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
    except Exception:
        print("Windowsスクロールフックの起動中にエラーが発生しました")
        traceback.print_exc()
        state.native_scroll_hook_active = False
        state.native_scroll_hook_ready.set()
    finally:
        if state.native_scroll_hook_handle:
            user32.UnhookWindowsHookEx(state.native_scroll_hook_handle)
        state.native_scroll_hook_handle = None
        state.native_scroll_hook_active = False
        print("Windowsスクロールフックを停止しました")

def start_native_scroll_hook():
    state.native_scroll_hook_ready.clear()
    state.native_scroll_hook_thread = threading.Thread(target=native_scroll_hook_worker, daemon=True)
    state.native_scroll_hook_thread.start()
    state.native_scroll_hook_ready.wait(timeout=1.0)

def stop_native_scroll_hook():
    if state.native_scroll_hook_thread_id is not None:
        user32.PostThreadMessageW(state.native_scroll_hook_thread_id, WM_QUIT, 0, 0)
    if state.native_scroll_hook_thread is not None:
        state.native_scroll_hook_thread.join(timeout=2)
    state.native_scroll_hook_thread = None
    state.native_scroll_hook_thread_id = None