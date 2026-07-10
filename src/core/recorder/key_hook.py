# @role: キーボード入力（Tabキー）をフックし、UIAスキャナーを非同期でトリガーする。

import threading
from typing import Callable, Optional
from pynput import keyboard
from core.recorder.uia_scanner import get_focused_element_info

class KeyHookManager:
    def __init__(self, on_tab_pressed_callback: Callable[[dict], None]):
        self.on_tab_pressed_callback = on_tab_pressed_callback
        self.listener: Optional[keyboard.Listener] = None
        self._is_running = False

    def start(self):
        if self._is_running:
            return
        self._is_running = True
        self.listener = keyboard.Listener(on_release=self._on_release)
        self.listener.start()

    def stop(self):
        if not self._is_running:
            return
        self._is_running = False
        if self.listener:
            self.listener.stop()
            self.listener = None

    def _on_release(self, key):
        if not self._is_running:
            return False

        if key == keyboard.Key.tab:
            # 背景: UIAスキャンは重い処理になる可能性があるため、キーフックのメインスレッドをブロックしないよう非同期実行する
            threading.Thread(target=self._scan_and_notify, daemon=True).start()

    def _scan_and_notify(self):
        info = get_focused_element_info()
        if info:
            self.on_tab_pressed_callback(info)