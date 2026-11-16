"""Module: @role: マクロ実行中の緊急停止ショートカット(Ctrl+\\)監視を担当する。"""

import logging
from typing import Any, Callable, Optional, Set

from pynput.keyboard import Listener as KeyboardListener

logger = logging.getLogger(__name__)


class EmergencyStopMonitor:
    """実行中の緊急停止ショートカット(Ctrl+\\)を監視するリスナーラッパー。"""

    def __init__(self, on_stop_triggered: Callable[[], None]) -> None:
        self._on_stop_triggered = on_stop_triggered
        self._pressed_keys: Set[str] = set()
        self._listener: Optional[KeyboardListener] = None

    def _normalize_key(self, key: Any) -> str:
        if hasattr(key, "char") and key.char is not None:
            return str(key.char).lower()
        return str(key).replace("Key.", "").lower()

    def _on_press(self, key: Any) -> Optional[bool]:
        try:
            key_name = self._normalize_key(key)
            self._pressed_keys.add(key_name)
            has_ctrl = any(k in {"ctrl", "ctrl_l", "ctrl_r"} for k in self._pressed_keys)
            vk = getattr(key, "vk", None)
            is_backslash = key_name in {"\\", "¥", "yen", "\x1c"} or vk in {220, 226}

            if has_ctrl and is_backslash:
                logger.warning("Emergency stop shortcut (Ctrl+\\) triggered.")
                self._on_stop_triggered()
                return False
        except Exception as e:
            logger.debug(f"EmergencyStopMonitor on_press error: {e}")
        return None

    def _on_release(self, key: Any) -> None:
        try:
            key_name = self._normalize_key(key)
            self._pressed_keys.discard(key_name)
        except Exception as e:
            logger.debug(f"EmergencyStopMonitor on_release error: {e}")

    def start(self) -> None:
        self._pressed_keys.clear()
        self._listener = KeyboardListener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as e:
                logger.debug(f"Failed to stop EmergencyStopMonitor listener: {e}")
            finally:
                self._listener = None
