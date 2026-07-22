import json
from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QFrame
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QFont
from PySide6.QtCore import Qt

class ActionBlockWidget(QFrame):
    def __init__(self, command: dict, workflow_dir: Path, parent=None):
        super().__init__(parent)
        self.command = command
        self.workflow_dir = workflow_dir
        self.method = command.get("method", "")
        self.args = command.get("args", {})
        
        self.setObjectName("ActionBlock")
        self.setStyleSheet("""
            #ActionBlock {
                background-color: #ffffff;
                border: 1px solid #d0d0d0;
                border-radius: 8px;
            }
            #ActionBlock:hover {
                border: 2px solid #0078d4;
            }
        """)
        self.setFixedWidth(340)
        
        self._build_ui()
        
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # 1. ヘッダー（アクション名）
        header_layout = QHBoxLayout()
        title_label = QLabel(self._get_title())
        font = title_label.font()
        font.setBold(True)
        font.setPointSize(11)
        title_label.setFont(font)
        title_label.setStyleSheet("color: #333333;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        # 2. スクリーンショット画像とカーソル合成
        raw_event_id = self.args.get("raw_event_id")
        if raw_event_id:
            img_path = self.workflow_dir / "images" / f"{raw_event_id}_pre.png"
            if img_path.exists():
                img_label = QLabel()
                img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                pixmap = QPixmap(str(img_path))
                
                # クリックや移動の場合は座標にカーソル風のマークを描画
                if self.method in ["click", "move"] and "x" in self.args and "y" in self.args:
                    pixmap = self._draw_cursor_on_pixmap(pixmap, self.args["x"], self.args["y"])
                
                # 表示用に縮小（アスペクト比維持）
                scaled_pixmap = pixmap.scaled(308, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                img_label.setPixmap(scaled_pixmap)
                img_label.setStyleSheet("border: 1px solid #e0e0e0; border-radius: 4px;")
                layout.addWidget(img_label)
                
        # 3. フッター（テキスト内容やキー情報）
        info_text = self._get_info_text()
        if info_text:
            info_label = QLabel(info_text)
            info_label.setWordWrap(True)
            info_label.setStyleSheet("color: #555555; background-color: #f3f2f1; padding: 6px; border-radius: 4px;")
            layout.addWidget(info_label)
            
    def _get_title(self) -> str:
        method_map = {
            "click": "クリック",
            "move": "マウス移動",
            "type_text": "テキスト入力",
            "press_key": "キー入力",
            "wait": "待機",
            "scroll": "スクロール",
            "activate_window": "ウィンドウアクティブ化"
        }
        return method_map.get(self.method, self.method)
        
    def _get_info_text(self) -> str:
        if self.method == "type_text":
            return f"入力内容: {self.args.get('text', '')}"
        elif self.method == "press_key":
            return f"キー: {self.args.get('key', '')}"
        elif self.method == "wait":
            return f"待機時間: {self.args.get('duration', 0)} 秒"
        elif self.method == "activate_window":
            return f"対象: {self.args.get('window_title', '')}"
        return ""
        
    def _draw_cursor_on_pixmap(self, pixmap: QPixmap, x: int, y: int) -> QPixmap:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 半透明の赤い円を描画
        pen = QPen(QColor(255, 0, 0, 200), 4)
        painter.setPen(pen)
        painter.setBrush(QColor(255, 0, 0, 80))
        
        radius = 24
        painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)
        
        # 十字のクロスヘアを描画
        painter.drawLine(x - radius - 10, y, x + radius + 10, y)
        painter.drawLine(x, y - radius - 10, x, y + radius + 10)
        
        painter.end()
        return pixmap