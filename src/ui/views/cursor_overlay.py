"""Module: @role: クリックスルーかつキャプチャ不可視の全画面透過HUD。各マウスイベントを高精度描画する。"""

import sys
import time
import math
import platform
import ctypes
from typing import Optional, List, Tuple
from PySide6.QtCore import Qt, QObject, Signal, QTimer, QPointF
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtGui import QPainter, QColor, QPen, QBrush

WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
WDA_EXCLUDEFROMCAPTURE = 0x00000011


class Ripple:
    def __init__(self, x: float, y: float, color: QColor, max_radius: float = 24.0, duration: float = 0.35):
        self.x = x
        self.y = y
        self.color = color
        self.max_radius = max_radius
        self.duration = duration
        self.start_time = time.time()

    @property
    def progress(self) -> float:
        return min(1.0, (time.time() - self.start_time) / self.duration)

    @property
    def is_alive(self) -> bool:
        return self.progress < 1.0


class CursorOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._cur_x: float = -100.0
        self._cur_y: float = -100.0
        self._is_pressed: bool = False
        self._press_pos: Optional[Tuple[float, float]] = None
        self._is_dragging: bool = False
        self._is_hovering: bool = False
        self._hover_pos: Optional[Tuple[float, float]] = None
        self._corner_pos: Optional[Tuple[float, float]] = None
        self._corner_time: float = 0.0

        self._ripples: List[Ripple] = []

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)  # ~60 FPS
        self._anim_timer.timeout.connect(self._on_tick)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_native_window_styles()
        self._fit_to_all_screens()
        self._anim_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._anim_timer.stop()
        self._ripples.clear()
        self._is_pressed = False
        self._is_dragging = False
        self._is_hovering = False

    def _fit_to_all_screens(self):
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.virtualGeometry())

    def _apply_native_window_styles(self):
        # Why: マウス入力を完全透過しキャプチャ画像から自ウィンドウを自動除外
        if platform.system() != "Windows":
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            cur_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, cur_style | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE)
            user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass

    def on_mouse_event(self, data: dict):
        evt_type = data.get("type")
        x = float(data.get("x", self._cur_x))
        y = float(data.get("y", self._cur_y))
        self._cur_x, self._cur_y = x, y

        if evt_type == "move":
            self._is_hovering = False
            if self._is_pressed and self._press_pos:
                dist = math.hypot(x - self._press_pos[0], y - self._press_pos[1])
                if dist > 8.0:
                    self._is_dragging = True

        elif evt_type == "down":
            self._is_pressed = True
            self._is_dragging = False
            self._is_hovering = False
            self._press_pos = (x, y)
            self._ripples.append(Ripple(x, y, QColor(239, 68, 68, 220), max_radius=18.0, duration=0.25))

        elif evt_type == "up":
            self._is_pressed = False
            self._is_dragging = False
            self._press_pos = None
            self._ripples.append(Ripple(x, y, QColor(59, 130, 246, 220), max_radius=28.0, duration=0.35))

        elif evt_type == "hover":
            self._is_hovering = True
            self._hover_pos = (x, y)

        elif evt_type == "corner":
            self._corner_pos = (x, y)
            self._corner_time = time.time()

        self.update()

    def _on_tick(self):
        alive_ripples = [r for r in self._ripples if r.is_alive]
        has_changed = len(alive_ripples) != len(self._ripples) or len(alive_ripples) > 0
        self._ripples = alive_ripples

        if self._corner_pos and (time.time() - self._corner_time > 0.4):
            self._corner_pos = None
            has_changed = True

        if has_changed or self._is_dragging or self._is_pressed or self._is_hovering:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. クリックDOWN/UPのフェードアウト波紋
        for r in self._ripples:
            p = r.progress
            radius = 6.0 + (r.max_radius - 6.0) * p
            alpha = int(r.color.alpha() * (1.0 - p))
            pen_color = QColor(r.color.red(), r.color.green(), r.color.blue(), alpha)
            painter.setPen(QPen(pen_color, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(r.x, r.y), radius, radius)

        # 2. ドラッグ&ドロップガイドライン
        if self._is_dragging and self._press_pos:
            pen = QPen(QColor(245, 158, 11, 200), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(self._press_pos[0], self._press_pos[1]), QPointF(self._cur_x, self._cur_y))
            # 始点アンカー
            painter.setPen(QPen(QColor(245, 158, 11, 230), 1.5))
            painter.setBrush(QBrush(QColor(245, 158, 11, 80)))
            painter.drawEllipse(QPointF(self._press_pos[0], self._press_pos[1]), 5.0, 5.0)

        # 3. 移動角変更インジケータ (急な方向転換点)
        if self._corner_pos:
            elapsed = time.time() - self._corner_time
            if elapsed < 0.4:
                alpha = int(220 * (1.0 - elapsed / 0.4))
                painter.setPen(QPen(QColor(168, 85, 247, alpha), 1.5))
                painter.setBrush(QBrush(QColor(168, 85, 247, int(alpha * 0.4))))
                cx, cy = self._corner_pos
                pts = [QPointF(cx, cy - 6), QPointF(cx + 6, cy), QPointF(cx, cy + 6), QPointF(cx - 6, cy)]
                painter.drawPolygon(pts)

        # 4. 一定時間停止（静止検知: 極細クロスヘア）
        if self._is_hovering and self._hover_pos:
            hx, hy = self._hover_pos
            painter.setPen(QPen(QColor(16, 185, 129, 230), 1.5))
            # 中心は邪魔しないよう空洞化
            painter.drawLine(QPointF(hx - 12, hy), QPointF(hx - 4, hy))
            painter.drawLine(QPointF(hx + 4, hy), QPointF(hx + 12, hy))
            painter.drawLine(QPointF(hx, hy - 12), QPointF(hx, hy - 4))
            painter.drawLine(QPointF(hx, hy + 4), QPointF(hx, hy + 12))
            painter.setPen(QPen(QColor(16, 185, 129, 150), 1.0))
            painter.drawEllipse(QPointF(hx, hy), 6.0, 6.0)

        # 5. クリックDOWN中の収束リング
        if self._is_pressed and not self._is_dragging:
            painter.setPen(QPen(QColor(239, 68, 68, 240), 2.0))
            painter.setBrush(QBrush(QColor(239, 68, 68, 60)))
            painter.drawEllipse(QPointF(self._cur_x, self._cur_y), 8.0, 8.0)
            painter.setBrush(QBrush(QColor(239, 68, 68, 255)))
            painter.drawEllipse(QPointF(self._cur_x, self._cur_y), 2.0, 2.0)


class CursorOverlayManager(QObject):
    _instance = None
    _event_signal = Signal(dict)
    _state_signal = Signal(bool)

    def __init__(self):
        super().__init__()
        self._overlay: Optional[CursorOverlay] = None
        self._event_signal.connect(self._handle_event)
        self._state_signal.connect(self._handle_state)

    @classmethod
    def get_instance(cls) -> "CursorOverlayManager":
        if cls._instance is None:
            cls._instance = CursorOverlayManager()
        return cls._instance

    def _ensure_overlay(self):
        if self._overlay is None and QApplication.instance():
            self._overlay = CursorOverlay()

    def _handle_state(self, active: bool):
        self._ensure_overlay()
        if self._overlay:
            if active:
                self._overlay.show()
            else:
                self._overlay.hide()

    def _handle_event(self, data: dict):
        if self._overlay and self._overlay.isVisible():
            self._overlay.on_mouse_event(data)

    def set_active(self, active: bool):
        self._state_signal.emit(active)

    def notify_move(self, x: float, y: float):
        self._event_signal.emit({"type": "move", "x": x, "y": y})

    def notify_click(self, x: float, y: float, pressed: bool):
        evt_type = "down" if pressed else "up"
        self._event_signal.emit({"type": evt_type, "x": x, "y": y})

    def notify_hover(self, x: float, y: float):
        self._event_signal.emit({"type": "hover", "x": x, "y": y})

    def notify_corner(self, x: float, y: float):
        self._event_signal.emit({"type": "corner", "x": x, "y": y})
