# tests/scroll_tester.py
# @role: OSレベルのスクロール値の傍受と送信を行い、実際のブラウザ上の移動量とシステム値の乖離を測定する検証ツール。

import ctypes
from ctypes import wintypes
import time
import sys

WH_MOUSE_LL = 14
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WHEEL_DELTA = 120

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

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

# --- 修正箇所: 欠落していたWindows APIの型定義を追加 ---
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelMouseProc, HINSTANCE, DWORD]
user32.SetWindowsHookExW.restype = HHOOK
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = HINSTANCE
# --------------------------------------------------------

# グローバル状態（Pythonのガベージコレクションによる破棄を防ぐ）
is_listening = False
hook_handle = None
_callback_ref = None  

def scroll_hook_callback(n_code, w_param, l_param):
    if n_code >= 0 and is_listening:
        if w_param in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            mouse_info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            # 16ビットシフトして符号付きのショート値として取り出す
            raw_delta = ctypes.c_short((mouse_info.mouseData >> 16) & 0xFFFF).value
            normalized_delta = raw_delta / WHEEL_DELTA
            
            direction = "Vertical" if w_param == WM_MOUSEWHEEL else "Horizontal"
            print(f"[受信] {direction} | 生データ(raw): {raw_delta:>4} | 正規化(delta): {normalized_delta:>5.2f} | タイムスタンプ: {mouse_info.time}")
            
    return user32.CallNextHookEx(hook_handle, n_code, w_param, l_param)

def start_listener():
    global hook_handle, is_listening, _callback_ref
    is_listening = True
    
    # 参照をグローバル変数に保持して破棄を防ぐ
    _callback_ref = LowLevelMouseProc(scroll_hook_callback)
    module_handle = kernel32.GetModuleHandleW(None)
    hook_handle = user32.SetWindowsHookExW(WH_MOUSE_LL, _callback_ref, module_handle, 0)
    
    if not hook_handle:
        error_code = ctypes.get_last_error()
        print(f"フックの開始に失敗しました。 WinError={error_code}")
        return
        
    print("--- 傍受モード開始 ---")
    print("ブラウザ上でマウスホイールを回して、出力される値を確認してください。")
    print("終了するには Ctrl+C を押してください。\n")
    
    message = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))

def send_test_scroll(lines: float):
    print(f"\n--- 送信モード開始 ({lines}目盛り分のスクロール) ---")
    print("3秒後にスクロールイベントを送信します。ブラウザのアクティブな領域にマウスを置いてお待ちください。")
    time.sleep(3)
    
    amount = int(lines * WHEEL_DELTA)
    print(f"[送信] MOUSEEVENTF_WHEEL (raw: {amount})")
    
    # 実行エンジンと同様の手段でネイティブスクロールを送信
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, amount, 0)
    print("送信完了。\n")

def main():
    print("【スクロール量 測定・検証ツール】")
    print("1: マウスからのスクロール生データを受信・測定する (記録側のテスト)")
    print("2: 指定した量のスクロールイベントをOSに一括送信する (実行側のテスト)")
    
    choice = input("実行するモードを選択してください (1 または 2): ")
    
    if choice == '1':
        try:
            start_listener()
        except KeyboardInterrupt:
            print("\n終了します。")
            if hook_handle:
                user32.UnhookWindowsHookEx(hook_handle)
            sys.exit(0)
            
    elif choice == '2':
        lines_str = input("何目盛り分スクロールさせますか？ (例: 下へ1目盛り = -1, 上へ3目盛り = 3): ")
        try:
            lines = float(lines_str)
            send_test_scroll(lines)
        except ValueError:
            print("数値を入力してください。")
            
    else:
        print("無効な入力です。")

if __name__ == "__main__":
    main()