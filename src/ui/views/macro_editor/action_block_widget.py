"""Module: @role: ワークフロー内の単一アクションの視認用ブロック表示およびインライン編集フォームのUI制御を担当する。"""

import json
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QHBoxLayout, QFrame, 
                               QPushButton, QFormLayout, QSpinBox, QLineEdit, QDoubleSpinBox,
                               QComboBox, QCheckBox, QFileDialog)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QDrag, QMouseEvent
from PySide6.QtCore import Qt, Signal, QMimeData, QPoint

from .image_preview_dialog import ImagePreviewDialog


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
        # Why: ループ内ブロックの左端にアクセントカラーを付与し実行範囲を明瞭化
        if self.is_in_loop:
            border_accent = "#107c41" if self.args.get("data_source") == "excel" else "#0078d4"
            self.setStyleSheet(f"""
                #ActionBlock {{
                    background-color: #ffffff;
                    border: 1px solid #d0d0d0;
                    border-left: 5px solid {border_accent};
                    border-radius: 8px;
                }}
                #ActionBlock:hover {{
                    border: 1px solid #0078d4;
                    border-left: 5px solid {border_accent};
                }}
            """)
        else:
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
        # Why: アクションブロック内の入力・メニュー項目の文字色とコントラストを明示固定
        self.edit_container.setStyleSheet("""
            QLabel {
                color: #2b2b2b;
                font-size: 13px;
            }
            QSpinBox, QDoubleSpinBox, QLineEdit {
                color: #1f2937;
                background-color: #ffffff;
                border: 1px solid #c7c7c7;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 13px;
            }
            QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
                border: 1px solid #0078d4;
            }
            QComboBox {
                color: #1f2937;
                background-color: #ffffff;
                border: 1px solid #c7c7c7;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 13px;
            }
            QComboBox:hover {
                border: 1px solid #0078d4;
            }
            QComboBox QAbstractItemView {
                color: #1f2937;
                background-color: #ffffff;
                selection-background-color: #0078d4;
                selection-color: #ffffff;
                border: 1px solid #c7c7c7;
            }
            QCheckBox {
                color: #2b2b2b;
                font-size: 13px;
            }
        """)
        self.edit_layout = QFormLayout(self.edit_container)
        self.edit_layout.setContentsMargins(0, 10, 0, 0)
        self._build_edit_form()
        self.edit_container.hide()
        self.main_layout.addWidget(self.edit_container)
        
    def _build_edit_form(self):
        if "seq_vars" not in self.args:
            self.args["seq_vars"] = {}

        if self.method in ["click", "move"]:
            self.x_spin = QSpinBox()
            self.x_spin.setRange(-9999, 9999)
            self.x_spin.setValue(self.args.get("x", 0))
            self.x_spin.valueChanged.connect(lambda v: self._update_arg("x", v))
            self.edit_layout.addRow("X座標(初期値):", self.x_spin)
            
            self.y_spin = QSpinBox()
            self.y_spin.setRange(-9999, 9999)
            self.y_spin.setValue(self.args.get("y", 0))
            self.y_spin.valueChanged.connect(lambda v: self._update_arg("y", v))
            self.edit_layout.addRow("Y座標(初期値):", self.y_spin)
            
            if self.is_in_loop:
                x_seq = self.args["seq_vars"].setdefault("x", {"step": 0})
                self.dx_spin = QSpinBox()
                self.dx_spin.setRange(-999, 999)
                self.dx_spin.setValue(x_seq.get("step", 0))
                self.dx_spin.valueChanged.connect(lambda v: self._update_seq_var("x", "step", v))
                self.edit_layout.addRow("X差分(ループ毎):", self.dx_spin)
                
                y_seq = self.args["seq_vars"].setdefault("y", {"step": 0})
                self.dy_spin = QSpinBox()
                self.dy_spin.setRange(-999, 999)
                self.dy_spin.setValue(y_seq.get("step", 0))
                self.dy_spin.valueChanged.connect(lambda v: self._update_seq_var("y", "step", v))
                self.edit_layout.addRow("Y差分(ループ毎):", self.dy_spin)
                
        elif self.method == "type_text":
            self.text_edit = QLineEdit(self.args.get("text", ""))
            self.text_edit.textChanged.connect(lambda v: self._update_arg("text", v))
            self.edit_layout.addRow("テキスト(初期値):", self.text_edit)

            self.clear_check = QCheckBox("入力前に既存テキストを全消去する")
            self.clear_check.setChecked(bool(self.args.get("clear_before_typing", False)))
            self.clear_check.toggled.connect(lambda v: self._update_arg("clear_before_typing", v))
            self.edit_layout.addRow("", self.clear_check)

            self.clip_check = QCheckBox("クリップボード経由で貼り付ける (IME誤作動防止)")
            self.clip_check.setChecked(bool(self.args.get("use_clipboard", False)))
            self.clip_check.toggled.connect(lambda v: self._update_arg("use_clipboard", v))
            self.edit_layout.addRow("", self.clip_check)
            
            if self.is_in_loop:
                text_seq = self.args["seq_vars"].setdefault("text", {"step": 1})
                self.seq_step_spin = QSpinBox()
                self.seq_step_spin.setRange(-99999, 99999)
                self.seq_step_spin.setValue(text_seq.get("step", 1))
                self.seq_step_spin.valueChanged.connect(lambda v: self._update_seq_var("text", "step", v))
                self.edit_layout.addRow("テキスト加算(ループ毎):", self.seq_step_spin)

        elif self.method == "loop_start":
            self.mode_combo = QComboBox()
            self.mode_combo.addItem("固定回数ループ", "static")
            self.mode_combo.addItem("Excelデータ連携ループ", "excel")
            cur_mode = self.args.get("data_source", "static")
            self.mode_combo.setCurrentIndex(1 if cur_mode == "excel" else 0)
            self.edit_layout.addRow("ループ種別:", self.mode_combo)

            self.static_widget = QWidget()
            s_layout = QFormLayout(self.static_widget)
            s_layout.setContentsMargins(0, 0, 0, 0)
            self.count_spin = QSpinBox()
            self.count_spin.setRange(1, 99999)
            self.count_spin.setValue(self.args.get("loop_count", 1))
            self.count_spin.valueChanged.connect(lambda v: self._update_arg("loop_count", v))
            s_layout.addRow("リピート回数:", self.count_spin)
            self.edit_layout.addRow(self.static_widget)

            self.excel_widget = QWidget()
            e_layout = QFormLayout(self.excel_widget)
            e_layout.setContentsMargins(0, 0, 0, 0)

            file_box = QHBoxLayout()
            self.file_edit = QLineEdit(self.args.get("file_path", ""))
            self.file_edit.setPlaceholderText("Excelファイルパス...")
            self.file_edit.textChanged.connect(lambda v: self._update_arg("file_path", v))
            file_box.addWidget(self.file_edit)

            browse_btn = QPushButton("参照...")
            browse_btn.setFixedWidth(60)
            browse_btn.clicked.connect(self._browse_excel_file)
            file_box.addWidget(browse_btn)
            e_layout.addRow("Excelファイル:", file_box)

            self.sheet_edit = QLineEdit(self.args.get("sheet_name", ""))
            self.sheet_edit.setPlaceholderText("空欄でアクティブシート")
            self.sheet_edit.textChanged.connect(lambda v: self._update_arg("sheet_name", v))
            e_layout.addRow("シート名:", self.sheet_edit)

            row_box = QHBoxLayout()
            self.start_row_spin = QSpinBox()
            self.start_row_spin.setRange(1, 99999)
            self.start_row_spin.setValue(self.args.get("start_row", 2))
            self.start_row_spin.valueChanged.connect(lambda v: self._update_arg("start_row", v))
            row_box.addWidget(QLabel("開始:"))
            row_box.addWidget(self.start_row_spin)

            self.end_row_spin = QSpinBox()
            self.end_row_spin.setRange(0, 99999)
            self.end_row_spin.setValue(self.args.get("end_row") or 0)
            self.end_row_spin.setSpecialValueText("末尾まで")
            self.end_row_spin.valueChanged.connect(lambda v: self._update_arg("end_row", v if v > 0 else None))
            row_box.addWidget(QLabel("終了:"))
            row_box.addWidget(self.end_row_spin)
            e_layout.addRow("対象行範囲:", row_box)

            self.status_col_edit = QLineEdit(self.args.get("status_column", "E"))
            self.status_col_edit.setPlaceholderText("例: E")
            self.status_col_edit.textChanged.connect(lambda v: self._update_arg("status_column", v.upper()))
            e_layout.addRow("ステータス記録列:", self.status_col_edit)

            self.skip_check = QCheckBox("完了ステータス済みの行をスキップ")
            self.skip_check.setChecked(bool(self.args.get("skip_completed", True)))
            self.skip_check.toggled.connect(lambda v: self._update_arg("skip_completed", v))
            e_layout.addRow("", self.skip_check)

            self.cont_check = QCheckBox("エラー発生時に中断せず次の行へ継続")
            self.cont_check.setChecked(bool(self.args.get("continue_on_error", False)))
            self.cont_check.toggled.connect(lambda v: self._update_arg("continue_on_error", v))
            e_layout.addRow("", self.cont_check)

            self.edit_layout.addRow(self.excel_widget)

            def _toggle_mode(idx):
                is_excel = idx == 1
                self.static_widget.setVisible(not is_excel)
                self.excel_widget.setVisible(is_excel)
                self._update_arg("data_source", "excel" if is_excel else "static")

            self.mode_combo.currentIndexChanged.connect(_toggle_mode)
            _toggle_mode(self.mode_combo.currentIndex())
                
        elif self.method == "wait":
            self.duration_spin = QDoubleSpinBox()
            self.duration_spin.setRange(0.1, 3600.0)
            self.duration_spin.setValue(self.args.get("duration", 1.0))
            self.duration_spin.valueChanged.connect(lambda v: self._update_arg("duration", v))
            self.edit_layout.addRow("待機(秒):", self.duration_spin)
            
        elif self.method == "scroll":
            self.dy_spin = QDoubleSpinBox()
            self.dy_spin.setRange(-100.0, 100.0)
            self.dy_spin.setValue(self.args.get("dy", -5.0))
            self.dy_spin.valueChanged.connect(lambda v: self._update_arg("dy", v))
            self.edit_layout.addRow("垂直スクロール量(dy):", self.dy_spin)
            
            self.sx_spin = QSpinBox()
            self.sx_spin.setRange(-9999, 9999)
            self.sx_spin.setValue(self.args.get("x", 0))
            self.sx_spin.valueChanged.connect(lambda v: self._update_arg("x", v))
            self.edit_layout.addRow("X座標:", self.sx_spin)
            
            self.sy_spin = QSpinBox()
            self.sy_spin.setRange(-9999, 9999)
            self.sy_spin.setValue(self.args.get("y", 0))
            self.sy_spin.valueChanged.connect(lambda v: self._update_arg("y", v))
            self.edit_layout.addRow("Y座標:", self.sy_spin)

        elif self.method == "press_key":
            self.key_edit = QLineEdit(self.args.get("key", ""))
            self.key_edit.textChanged.connect(lambda v: self._update_arg("key", v))
            self.edit_layout.addRow("キー名:", self.key_edit)

        elif self.method == "activate_window":
            self.win_edit = QLineEdit(self.args.get("window_title", ""))
            self.win_edit.textChanged.connect(lambda v: self._update_arg("window_title", v))
            self.edit_layout.addRow("ウィンドウ名:", self.win_edit)

        # 詳細フォーム内に削除ボタンを配置
        delete_btn = QPushButton("このアクションを削除")
        delete_btn.setStyleSheet("QPushButton { color: #d13438; font-weight: bold; padding: 4px; }")
        delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.cmd_index))
        self.edit_layout.addRow("", delete_btn)

    def _update_arg(self, key, value):
        self.args[key] = value
        self.info_label.setText(self._get_info_text())
        self.content_changed.emit()
        
    def _browse_excel_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Excelファイルを選択", str(self.workflow_dir), "Excel Files (*.xlsx *.xls *.xlsm);;All Files (*.*)"
        )
        if file_path:
            self.file_edit.setText(file_path)
            self._update_arg("file_path", file_path)

    def _update_seq_var(self, target_key, param_key, value):
        if "seq_vars" not in self.args:
            self.args["seq_vars"] = {}
        if target_key not in self.args["seq_vars"]:
            self.args["seq_vars"][target_key] = {}
        self.args["seq_vars"][target_key][param_key] = value
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
        if self.method == "loop_start" and self.args.get("data_source") == "excel":
            return "Excelデータ連携ループ"
        method_map = {
            "click": "クリック", "move": "マウス移動", "type_text": "テキスト入力",
            "press_key": "キー入力", "wait": "待機", "scroll": "スクロール", 
            "activate_window": "ウィンドウアクティブ化", "loop_start": "ループ開始", "loop_end": "ループ終了"
        }
        return method_map.get(self.method, self.method)
        
    def _get_info_text(self) -> str:
        if self.method == "click":
            name = self.args.get("element_name", "")
            btn = "左" if self.args.get("button") == "left" else "右"
            coords = f"({self.args.get('x', 0)}, {self.args.get('y', 0)})"
            return f"対象: {name} | {btn}クリック {coords}" if name else f"{btn}クリック {coords}"
        elif self.method == "move":
            name = self.args.get("element_name", "")
            coords = f"({self.args.get('x', 0)}, {self.args.get('y', 0)})"
            return f"ホバー対象: {name} {coords}" if name else f"カーソル移動 {coords}"
        elif self.method == "scroll":
            dy = self.args.get("dy", 0.0)
            direction = "下スクロール" if dy < 0 else "上スクロール"
            coords = f"({self.args.get('x', 0)}, {self.args.get('y', 0)})"
            return f"{direction}: {abs(dy)} {coords}"
        elif self.method == "type_text":
            info = f"入力内容: {self.args.get('text', '')}"
            flags = []
            if self.args.get("clear_before_typing"):
                flags.append("クリア有")
            if self.args.get("use_clipboard"):
                flags.append("貼付")
            return f"{info} ({', '.join(flags)})" if flags else info
        elif self.method == "press_key":
            return f"キー: {self.args.get('key', '')}"
        elif self.method == "wait":
            return f"待機時間: {self.args.get('duration', 0)} 秒"
        elif self.method == "activate_window":
            return f"対象: {self.args.get('window_title', '')}"
        elif self.method == "loop_start":
            if self.args.get("data_source") == "excel":
                f_name = Path(self.args.get("file_path", "")).name if self.args.get("file_path") else "未選択"
                st_col = self.args.get("status_column") or "-"
                return f"Excel: {f_name} | ステータス列: {st_col}"
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