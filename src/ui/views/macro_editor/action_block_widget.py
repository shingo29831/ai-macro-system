# src/ui/views/macro_editor/action_block_widget.py
import json
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QHBoxLayout, QFrame, 
                               QPushButton, QFormLayout, QSpinBox, QLineEdit, QDoubleSpinBox, QDialog)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QDrag, QMouseEvent
from PySide6.QtCore import Qt, Signal, QMimeData, QPoint

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

class ActionBlockWidget(QFrame):
    delete_requested = Signal(int)
    content_changed = Signal()

    def __init__(self, command: dict, cmd_index: int, workflow_dir: Path, is_in_loop: bool = False, parent=None):
        super().__init__(parent)
        self.command = command
        self.cmd_index = cmd_index
        self.workflow_dir = workflow_dir
        self.is_in_loop = is_in_loop
        
        self.method = command.get("method", "")
        self.args = command.get("args", {})
        
        self.is_expanded = False
        self.drag_start_pos = None
        
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
        self.setFixedWidth(480)
        
        self._build_ui()
        
    def _build_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(16, 16, 16, 16)
        self.main_layout.setSpacing(12)
        
        # 1. ヘッダー（アクション名のみ、削除ボタンは詳細へ移動）
        header_layout = QHBoxLayout()
        title_label = QLabel(self._get_title())
        font = title_label.font()
        font.setBold(True)
        font.setPointSize(14)
        title_label.setFont(font)
        title_label.setStyleSheet("color: #333333;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
        self.main_layout.addLayout(header_layout)
        
        # 2. スクリーンショット画像とカーソル合成
        raw_event_id = self.args.get("raw_event_id")
        if raw_event_id:
            img_path = self.workflow_dir / "images" / f"{raw_event_id}_pre.png"
            if img_path.exists():
                self.img_label = QLabel()
                self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.img_label.setCursor(Qt.CursorShape.PointingHandCursor)
                
                pixmap = QPixmap(str(img_path))
                
                if self.method in ["click", "move"] and "x" in self.args and "y" in self.args:
                    pixmap = self._draw_cursor_on_pixmap(pixmap, self.args["x"], self.args["y"])
                
                self._original_pixmap = pixmap
                
                scaled_pixmap = pixmap.scaled(448, 280, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self.img_label.setPixmap(scaled_pixmap)
                self.img_label.setStyleSheet("border: 1px solid #e0e0e0; border-radius: 4px;")
                self.main_layout.addWidget(self.img_label)
                
        # 3. フッター（サマリー情報）
        self.info_label = QLabel(self._get_info_text())
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #555555; background-color: #f3f2f1; padding: 8px; border-radius: 4px; font-size: 13px;")
        if not self.info_label.text():
            self.info_label.hide()
        self.main_layout.addWidget(self.info_label)
        
        # 4. インライン編集フォーム（初期状態は非表示）
        self.edit_container = QWidget()
        self.edit_container.setStyleSheet("""
            QLabel, QSpinBox, QLineEdit, QDoubleSpinBox {
                color: #333333;
                background-color: #ffffff;
                font-size: 13px;
            }
        """)
        self.edit_layout = QFormLayout(self.edit_container)
        self.edit_layout.setContentsMargins(0, 10, 0, 0)
        self._build_edit_form()
        self.edit_container.hide()
        self.main_layout.addWidget(self.edit_container)
        
    def _build_edit_form(self):
        if self.method in ["click", "move"]:
            self.x_spin = QSpinBox()
            self.x_spin.setRange(-9999, 9999)
            self.x_spin.setValue(self.args.get("x", 0))
            self.x_spin.valueChanged.connect(lambda v: self._update_arg("x", v))
            self.edit_layout.addRow("X座標:", self.x_spin)
            
            self.y_spin = QSpinBox()
            self.y_spin.setRange(-9999, 9999)
            self.y_spin.setValue(self.args.get("y", 0))
            self.y_spin.valueChanged.connect(lambda v: self._update_arg("y", v))
            self.edit_layout.addRow("Y座標:", self.y_spin)
            
            if self.is_in_loop:
                self.dx_spin = QSpinBox()
                self.dx_spin.setRange(-999, 999)
                self.dx_spin.setValue(self.args.get("x_offset", 0))
                self.dx_spin.valueChanged.connect(lambda v: self._update_arg("x_offset", v))
                self.edit_layout.addRow("X差分(ループ):", self.dx_spin)
                
                self.dy_spin = QSpinBox()
                self.dy_spin.setRange(-999, 999)
                self.dy_spin.setValue(self.args.get("y_offset", 0))
                self.dy_spin.valueChanged.connect(lambda v: self._update_arg("y_offset", v))
                self.edit_layout.addRow("Y差分(ループ):", self.dy_spin)
                
        elif self.method == "type_text":
            self.text_edit = QLineEdit(self.args.get("text", ""))
            self.text_edit.textChanged.connect(lambda v: self._update_arg("text", v))
            self.edit_layout.addRow("テキスト:", self.text_edit)
            
            if self.is_in_loop:
                seq_val = self.args.setdefault("sequence_value", {"start": 1, "step": 1})
                self.seq_start_spin = QSpinBox()
                self.seq_start_spin.setValue(seq_val.get("start", 1))
                self.seq_start_spin.valueChanged.connect(lambda v: self._update_seq_var("start", v))
                self.edit_layout.addRow("連番開始:", self.seq_start_spin)
                
                self.seq_step_spin = QSpinBox()
                self.seq_step_spin.setValue(seq_val.get("step", 1))
                self.seq_step_spin.valueChanged.connect(lambda v: self._update_seq_var("step", v))
                self.edit_layout.addRow("ステップ:", self.seq_step_spin)
                
        elif self.method == "wait":
            self.duration_spin = QDoubleSpinBox()
            self.duration_spin.setRange(0.1, 3600.0)
            self.duration_spin.setValue(self.args.get("duration", 1.0))
            self.duration_spin.valueChanged.connect(lambda v: self._update_arg("duration", v))
            self.edit_layout.addRow("待機(秒):", self.duration_spin)

        # 詳細フォーム内に削除ボタンを配置
        delete_btn = QPushButton("このアクションを削除")
        delete_btn.setStyleSheet("QPushButton { color: #d13438; font-weight: bold; padding: 4px; }")
        delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.cmd_index))
        self.edit_layout.addRow("", delete_btn)

    def _update_arg(self, key, value):
        self.args[key] = value
        self.info_label.setText(self._get_info_text())
        self.content_changed.emit()
        
    def _update_seq_var(self, key, value):
        self.args["sequence_value"][key] = value
        self.content_changed.emit()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not (event.buttons() & Qt.MouseButton.LeftButton) or not self.drag_start_pos:
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() > 10:
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setText(f"action_block:{self.cmd_index}")
            drag.setMimeData(mime_data)
            drag.exec_(Qt.DropAction.MoveAction)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_start_pos:
            if (event.pos() - self.drag_start_pos).manhattanLength() <= 10:
                if hasattr(self, 'img_label') and self.img_label.geometry().contains(event.pos()):
                    self._show_image_preview()
                else:
                    self.is_expanded = not self.is_expanded
                    self.edit_container.setVisible(self.is_expanded)
        self.drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _show_image_preview(self):
        if hasattr(self, '_original_pixmap'):
            dialog = ImagePreviewDialog(self._original_pixmap, self)
            dialog.exec()

    def _get_title(self) -> str:
        method_map = {
            "click": "クリック", "move": "マウス移動", "type_text": "テキスト入力",
            "press_key": "キー入力", "wait": "待機", "scroll": "スクロール", 
            "activate_window": "ウィンドウアクティブ化", "loop_start": "ループ開始", "loop_end": "ループ終了"
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
        elif self.method == "loop_start":
            return f"回数: {self.args.get('loop_count', 1)} 回"
        return ""
        
    def _draw_cursor_on_pixmap(self, pixmap: QPixmap, x: int, y: int) -> QPixmap:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(255, 0, 0, 200), 4)
        painter.setPen(pen)
        painter.setBrush(QColor(255, 0, 0, 80))
        radius = 24
        painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)
        painter.drawLine(x - radius - 10, y, x + radius + 10, y)
        painter.drawLine(x, y - radius - 10, x, y + radius + 10)
        painter.end()
        return pixmap