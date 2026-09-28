"""Module: @role: マクロエディタのキャンバス上でループ回数の設定、Excelデータ連携ダイアログ、および逆転警告を表示する補助UIウィジェット群。"""

from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSpinBox, QWidget, 
                               QPushButton, QDialog, QVBoxLayout, QFormLayout, 
                               QLineEdit, QCheckBox, QComboBox, QFileDialog)


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


class LoopSettingDialog(QDialog):
    def __init__(self, cmd: dict, workflow_dir: Path, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.workflow_dir = workflow_dir
        self.args = cmd.setdefault("args", {})
        self.setWindowTitle("ループ設定")
        self.setMinimumWidth(440)
        self.setStyleSheet("""
            QDialog { background-color: #ffffff; }
            QLabel { color: #333333; font-size: 13px; }
            QLineEdit, QSpinBox, QComboBox { padding: 4px; border: 1px solid #d0d0d0; border-radius: 4px; font-size: 13px; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        form = QFormLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("固定回数ループ", "static")
        self.mode_combo.addItem("Excelデータ連携ループ", "excel")
        cur_mode = self.args.get("data_source", "static")
        self.mode_combo.setCurrentIndex(1 if cur_mode == "excel" else 0)
        form.addRow("ループ種別:", self.mode_combo)

        # 固定回数設定
        self.static_widget = QWidget()
        s_layout = QFormLayout(self.static_widget)
        s_layout.setContentsMargins(0, 0, 0, 0)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 99999)
        self.count_spin.setValue(self.args.get("loop_count", 10))
        s_layout.addRow("リピート回数:", self.count_spin)
        form.addRow(self.static_widget)

        # Excelデータ連携設定
        self.excel_widget = QWidget()
        e_layout = QFormLayout(self.excel_widget)
        e_layout.setContentsMargins(0, 0, 0, 0)

        file_box = QHBoxLayout()
        self.file_edit = QLineEdit(self.args.get("file_path", ""))
        self.file_edit.setPlaceholderText("Excelファイルパス...")
        file_box.addWidget(self.file_edit)

        browse_btn = QPushButton("参照...")
        browse_btn.setFixedWidth(60)
        browse_btn.clicked.connect(self._browse_file)
        file_box.addWidget(browse_btn)
        e_layout.addRow("Excelファイル:", file_box)

        self.sheet_edit = QLineEdit(self.args.get("sheet_name", ""))
        self.sheet_edit.setPlaceholderText("空欄でアクティブシート")
        e_layout.addRow("シート名:", self.sheet_edit)

        row_box = QHBoxLayout()
        self.start_row_spin = QSpinBox()
        self.start_row_spin.setRange(1, 99999)
        self.start_row_spin.setValue(self.args.get("start_row", 2))
        row_box.addWidget(QLabel("開始:"))
        row_box.addWidget(self.start_row_spin)

        self.end_row_spin = QSpinBox()
        self.end_row_spin.setRange(0, 99999)
        self.end_row_spin.setValue(self.args.get("end_row") or 0)
        self.end_row_spin.setSpecialValueText("末尾まで")
        row_box.addWidget(QLabel("終了:"))
        row_box.addWidget(self.end_row_spin)
        e_layout.addRow("対象行範囲:", row_box)

        self.status_col_edit = QLineEdit(self.args.get("status_column", "E"))
        self.status_col_edit.setPlaceholderText("例: E")
        e_layout.addRow("ステータス記録列:", self.status_col_edit)

        self.skip_check = QCheckBox("完了ステータス済みの行をスキップ")
        self.skip_check.setChecked(bool(self.args.get("skip_completed", True)))
        e_layout.addRow("", self.skip_check)

        self.cont_check = QCheckBox("エラー発生時に中断せず次の行へ継続")
        self.cont_check.setChecked(bool(self.args.get("continue_on_error", False)))
        e_layout.addRow("", self.cont_check)

        form.addRow(self.excel_widget)

        def _toggle(idx):
            is_ex = idx == 1
            self.static_widget.setVisible(not is_ex)
            self.excel_widget.setVisible(is_ex)

        self.mode_combo.currentIndexChanged.connect(_toggle)
        _toggle(self.mode_combo.currentIndex())

        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.setStyleSheet("padding: 6px 14px; border: 1px solid #d0d0d0; border-radius: 4px;")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("決定")
        save_btn.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; padding: 6px 18px; border-radius: 4px;")
        save_btn.clicked.connect(self._save)
        btn_box.addWidget(cancel_btn)
        btn_box.addWidget(save_btn)
        layout.addLayout(btn_box)

    def _browse_file(self):
        fp, _ = QFileDialog.getOpenFileName(self, "Excelファイルを選択", str(self.workflow_dir), "Excel Files (*.xlsx *.xls *.xlsm);;All Files (*.*)")
        if fp:
            self.file_edit.setText(fp)

    def _save(self):
        is_ex = self.mode_combo.currentIndex() == 1
        self.args["data_source"] = "excel" if is_ex else "static"
        if is_ex:
            self.args["file_path"] = self.file_edit.text()
            self.args["sheet_name"] = self.sheet_edit.text()
            self.args["start_row"] = self.start_row_spin.value()
            ed = self.end_row_spin.value()
            self.args["end_row"] = ed if ed > 0 else None
            self.args["status_column"] = self.status_col_edit.text().strip().upper() or None
            self.args["skip_completed"] = self.skip_check.isChecked()
            self.args["continue_on_error"] = self.cont_check.isChecked()
        else:
            self.args["loop_count"] = self.count_spin.value()
        self.accept()


class LoopCountWidget(QFrame):
    count_changed = Signal(int, int)
    setting_requested = Signal(int)

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
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

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

        self.label = QLabel("回")
        self.label.setStyleSheet("color: #333333; font-weight: bold; font-size: 13px;")

        self.excel_badge = QLabel("Excel連携")
        self.excel_badge.setStyleSheet("""
            QLabel {
                color: #ffffff;
                background-color: #107c41;
                font-weight: bold;
                font-size: 11px;
                padding: 2px 6px;
                border-radius: 4px;
            }
        """)
        self.excel_badge.hide()

        self.setting_btn = QPushButton("⚙")
        self.setting_btn.setFixedSize(22, 22)
        self.setting_btn.setToolTip("ループ設定（Excel連携など）")
        self.setting_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #555555;
                font-size: 13px;
            }
            QPushButton:hover {
                color: #0078d4;
                font-weight: bold;
            }
        """)
        self.setting_btn.clicked.connect(lambda: self.setting_requested.emit(self.loop_start_idx))

        layout.addWidget(self.spin_box)
        layout.addWidget(self.label)
        layout.addWidget(self.excel_badge)
        layout.addWidget(self.setting_btn)

        self.spin_box.valueChanged.connect(self._on_value_changed)

    def set_data_source(self, data_source: str):
        # Why: データ駆動ループ時に回数スピンを隠しExcel連携バッジへ切り替え
        if data_source == "excel":
            self.spin_box.hide()
            self.label.hide()
            self.excel_badge.show()
            self.setStyleSheet("""
                #LoopCountWidget {
                    background-color: #ffffff;
                    border: 2px solid #107c41;
                    border-radius: 6px;
                }
            """)
        else:
            self.excel_badge.hide()
            self.spin_box.show()
            self.label.show()
            self.setStyleSheet("""
                #LoopCountWidget {
                    background-color: #ffffff;
                    border: 2px solid #0078d4;
                    border-radius: 6px;
                }
            """)

    def _on_value_changed(self, val):
        self.count_changed.emit(self.loop_start_idx, val)
