"""Module: @role: マクロアクションのスクリーンショット画像を画面中央にモーダル拡大表示するプレビューダイアログ。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout


class ImagePreviewDialog(QDialog):
    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("background-color: white; border: 1px solid #999999; border-radius: 4px;")

        screen = self.screen().availableGeometry()
        max_w = screen.width() * 0.8
        max_h = screen.height() * 0.8

        if pixmap.width() > max_w or pixmap.height() > max_h:
            pixmap = pixmap.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

        label.setPixmap(pixmap)
        layout.addWidget(label)

        self.adjustSize()
        if parent:
            parent_rect = parent.window().geometry()
            x = parent_rect.x() + (parent_rect.width() - self.width()) // 2
            y = parent_rect.y() + (parent_rect.height() - self.height()) // 2
            self.move(x, y)

    def mousePressEvent(self, event):
        self.accept()
        super().mousePressEvent(event)
