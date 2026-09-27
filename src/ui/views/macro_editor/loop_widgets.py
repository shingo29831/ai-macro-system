"""Module: @role: マクロエディタのキャンバス上でループ回数の設定および逆転警告を表示する補助UIウィジェット群。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSpinBox, QWidget


class WarningWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setToolTip("ループの開始と終了が逆転しています。\n矢印線が逆転しないように注意してください。")

    def paintEvent(self, event):
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


class LoopCountWidget(QFrame):
    count_changed = Signal(int, int)

    def __init__(self, loop_start_idx: int, initial_count: int, parent=None):
        super().__init__(parent)
        self.loop_start_idx = loop_start_idx

        self.setObjectName("LoopCountWidget")
        self.setStyleSheet("""
            #LoopCountWidget {
                background-color: #ffffff;
                border: 2px solid #0078d4;
                border-radius: 6px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        self.spin_box = QSpinBox()
        self.spin_box.setRange(1, 9999)
        self.spin_box.setValue(initial_count)
        self.spin_box.setStyleSheet("""
            QSpinBox {
                border: none;
                background: transparent;
                font-size: 13px;
                font-weight: bold;
                color: #0078d4;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 0px;
            }
        """)
        self.spin_box.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.spin_box.setFixedWidth(40)
        self.spin_box.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        label = QLabel("回")
        label.setStyleSheet("color: #333333; font-weight: bold; font-size: 13px;")

        layout.addWidget(self.spin_box)
        layout.addWidget(label)

        self.spin_box.valueChanged.connect(self._on_value_changed)

    def _on_value_changed(self, val):
        self.count_changed.emit(self.loop_start_idx, val)
