# src/ui/views/macro_editor/action_block_widget.py
import json
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QHBoxLayout, QFrame, 
                               QPushButton, QFormLayout, QSpinBox, QLineEdit, QDoubleSpinBox)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QDrag, QMouseEvent
from PySide6.QtCore import Qt, Signal, QMimeData, QPoint

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
        self.setFixedWidth(340)
        
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
        font.setPointSize(11)
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
                img_label = QLabel()
                img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                pixmap = QPixmap(str(img_path))
                
                if self.method in ["click", "move"] and "x" in self.args and "y" in self.args:
                    pixmap = self._draw_cursor_on_pixmap(pixmap, self.args["x"], self.args["y"])
                
                scaled_pixmap = pixmap.scaled(308, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                img_label.setPixmap(scaled_pixmap)
                img_label.setStyleSheet("border: 1px solid #e0e0e0; border-radius: 4px;")
                self.main_layout.addWidget(img_label)
                
        # 3. フッター（サマリー情報）
        self.info_label = QLabel(self._get_info_text())
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #555555; background-color: #f3f2f1; padding: 6px; border-radius: 4px;")
        if not self.info_label.text():
            self.info_label.hide()
        self.main_layout.addWidget(self.info_label)
        
        # 4. インライン編集フォーム（初期状態は非表示）
        self.edit_container = QWidget()
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
                self.is_expanded = not self.is_expanded
                self.edit_container.setVisible(self.is_expanded)
        self.drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _get_title(self) -> str:
        method_map = {
            "click": "クリック", "move": "マウス移動", "type_text": "テキスト入力",
            "press_key": "キー入力", "wait": "待機", "scroll": "スクロール", "activate_window": "ウィンドウアクティブ化"
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
        pen = QPen(QColor(255, 0, 0, 200), 4)
        painter.setPen(pen)
        painter.setBrush(QColor(255, 0, 0, 80))
        radius = 24
        painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)
        painter.drawLine(x - radius - 10, y, x + radius + 10, y)
        painter.drawLine(x, y - radius - 10, x, y + radius + 10)
        painter.end()
        return pixmap