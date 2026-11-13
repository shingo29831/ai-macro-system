"""Module: @role: ブラウザ操作における低レベルの物理マウス移動・クリック、クリップボード/Unicodeタイピング、およびタブ・URL操作プリミティブを担当する。"""

import ctypes
import platform
import time
from typing import Any, Dict, Optional

from pynput.keyboard import Controller as KeyboardController, Key
from pynput.mouse import Button, Controller as MouseController

from core.executor.os_env_controller import set_system_cursor


class BrowserInputDriver:
    """ブラウザ操作向けの物理キーボード・マウス入力プリミティブを提供するドライバ"""

    def __init__(self, mouse: MouseController, keyboard: KeyboardController) -> None:
        self._mouse = mouse
        self._keyboard = keyboard

    def click_screen_point(self, cx: int, cy: int) -> None:
        if platform.system() == "Windows":
            from core.executor.input_helper import smooth_move

            smooth_move(cx, cy)
            time.sleep(0.04)
            set_system_cursor("run_click")
            time.sleep(0.03)
            self._mouse.click(Button.left, 1)
            time.sleep(0.06)
            set_system_cursor("run_idle")

    def click_physical_coords(
        self,
        x: Optional[int],
        y: Optional[int],
        last_win_args: Optional[Dict[str, Any]] = None,
    ) -> bool:
        # Why: ウィンドウ移動オフセットを自動加算し物理座標を確実にクリック
        if x is None or y is None or (x <= 20 and y <= 20):
            return False
        off_x, off_y = 0, 0
        if last_win_args and platform.system() == "Windows":
            try:
                target_hwnd = last_win_args.get("mapped_hwnd") or ctypes.windll.user32.GetForegroundWindow()
                if target_hwnd:
                    ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                    time.sleep(0.05)
                rec_x = last_win_args.get("x", 0)
                rec_y = last_win_args.get("y", 0)
                from core.executor.input_helper import get_window_offset

                off_x, off_y = get_window_offset(target_hwnd, rec_x, rec_y)
            except Exception:
                pass

        actual_x = int(x + off_x)
        actual_y = int(y + off_y)
        from core.executor.input_helper import smooth_move

        smooth_move(actual_x, actual_y)
        time.sleep(0.04)
        set_system_cursor("run_click")
        self._mouse.click(Button.left, 1)
        time.sleep(0.06)
        set_system_cursor("run_idle")
        time.sleep(0.1)
        return True

    def focus_address_bar(self) -> None:
        self._keyboard.press(Key.ctrl)
        self._keyboard.press("l")
        self._keyboard.release("l")
        self._keyboard.release(Key.ctrl)
        time.sleep(0.08)

    def perform_typing_input(self, text: str, clear_before: bool = True) -> None:
        # Why: 既存テキストを消去しクリップボード貼付と仮想DOM通知キーで確実に入力
        if clear_before:
            self._keyboard.press(Key.ctrl)
            self._keyboard.press("a")
            self._keyboard.release("a")
            self._keyboard.release(Key.ctrl)
            time.sleep(0.04)
            self._keyboard.press(Key.backspace)
            self._keyboard.release(Key.backspace)
            time.sleep(0.04)

        from core.executor.input_helper import send_unicode_string, set_clipboard_text
        from core.executor.os_env_controller import ensure_ime_state, normalize_text_width

        norm_text = normalize_text_width(text)
        # Why: クリップボード貼付直前はIMEを半角OFFにしてローマ字誤変換を完全根絶
        ensure_ime_state(target_state=False, timeout=0.4)
        time.sleep(0.04)

        # Why: クリップボード貼付とSendInput(Unicode)ダイレクト入力の二重化で確実に入力
        if set_clipboard_text(norm_text):
            self._keyboard.press(Key.ctrl)
            self._keyboard.press("v")
            self._keyboard.release("v")
            self._keyboard.release(Key.ctrl)
            time.sleep(0.06)
            self._keyboard.press(Key.right)
            self._keyboard.release(Key.right)
            time.sleep(0.06)
        else:
            send_unicode_string(norm_text)
        time.sleep(0.15)

    def open_url(self, url: str) -> None:
        if not url:
            return
        from core.executor.input_helper import set_clipboard_text
        from core.executor.os_env_controller import ensure_ime_state, normalize_text_width

        norm_url = normalize_text_width(url)
        # Why: IME状況を監視し半角状態を確認してからアドレスバーへ遷移
        ensure_ime_state(target_state=False, timeout=0.6)
        time.sleep(0.05)
        self.focus_address_bar()
        ensure_ime_state(target_state=False, timeout=0.4)
        if set_clipboard_text(norm_url):
            time.sleep(0.04)
            self._keyboard.press(Key.ctrl)
            self._keyboard.press("v")
            self._keyboard.release("v")
            self._keyboard.release(Key.ctrl)
            time.sleep(0.04)
        else:
            self._keyboard.type(norm_url)
        time.sleep(0.05)
        self._keyboard.press(Key.enter)
        self._keyboard.release(Key.enter)
        time.sleep(1.0)

    def close_tab(self) -> None:
        self._keyboard.press(Key.ctrl)
        self._keyboard.press("w")
        self._keyboard.release("w")
        self._keyboard.release(Key.ctrl)
        time.sleep(0.3)

    def switch_tab(self) -> None:
        self._keyboard.press(Key.ctrl)
        self._keyboard.press(Key.tab)
        self._keyboard.release(Key.tab)
        self._keyboard.release(Key.ctrl)
        time.sleep(0.3)

    def hover_screen_point(self, hx: int, hy: int, last_win_args: Optional[Dict[str, Any]]) -> None:
        if platform.system() == "Windows":
            target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
            if target_hwnd:
                ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                time.sleep(0.04)
            from core.executor.input_helper import smooth_move

            smooth_move(hx, hy, steps=12, duration=0.15)
            # Why: レンダラープロセスにマウス進入・ホバーを認識させる微小往復イベント
            self._mouse.position = (hx, hy)
            ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
            ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
        else:
            self._mouse.position = (hx, hy)
        # Why: ドロップダウンやメガメニューのCSSアニメーション展開時間を確実に待機
        time.sleep(0.55)
