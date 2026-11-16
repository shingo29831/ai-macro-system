"""Module: @role: マクロエディタのキャンバス上でループ開始・終了逆転警告アイコンを描画するUIウィジェット。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class WarningWidget(QWidget):
    """ループ逆転時の警告アイコンを描画するウィジェット。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setToolTip("ループの開始と終了が逆転しています。\n矢印線が逆転しないように注意してください。")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.moveTo(12, 2)
        path.lineTo(22, 20)
        path.lineTo(2, 20)
        path.closeSubpath()

        painter.setBrush(QColor("#ffcc00"))
        painter.setPen(QPen(QColor("#d13438"), 2))
        painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#d13438"))
        painter.drawRect(11, 8, 2, 6)
        painter.drawRect(11, 16, 2, 2)
