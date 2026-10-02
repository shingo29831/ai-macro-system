"""Module: @role: マクロエディタのツールパレット用アイテムおよびステップ目次用アイテムのUIコンポーネント。"""

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class ToolItemWidget(QFrame):
    def __init__(self, label_text: str, action_type: str, parent=None):
        super().__init__(parent)
        self.action_type = action_type
        self.setObjectName("ToolItem")
        self.setStyleSheet("""
            #ToolItem {
                background-color: #ffffff;
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                padding: 10px;
            }
            #ToolItem:hover {
                border: 1px solid #0078d4;
                background-color: #f3f2f1;
            }
        """)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        label = QLabel(label_text)
        label.setStyleSheet("color: #333333;")
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        layout.addWidget(label)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() > 5:
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setText(f"new_action:{self.action_type}")
            drag.setMimeData(mime_data)
            drag.exec_(Qt.DropAction.CopyAction)
        super().mouseMoveEvent(event)


class IndexItemWidget(QFrame):
    clicked = Signal(int)

    def __init__(self, title: str, index: int, depth: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.setObjectName("IndexItem")

        indent = depth * 20
        self.setStyleSheet(f"""
            #IndexItem {{
                background-color: transparent;
                border: none;
                border-radius: 4px;
                padding: 6px;
                padding-left: {indent + 6}px;
            }}
            #IndexItem:hover {{
                background-color: #e1dfdd;
            }}
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel(title)
        label.setStyleSheet("color: #333333;")
        layout.addWidget(label)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)
