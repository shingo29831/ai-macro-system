"""Module: @role: マクロエディタのループ種別（固定回数・Excelデータ連携）を設定するモーダルダイアログ。"""

from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class LoopSettingDialog(QDialog):
    def __init__(self, cmd: Dict[str, Any], workflow_dir: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cmd = cmd
        self.workflow_dir = workflow_dir
        self.args: Dict[str, Any] = cmd.setdefault("args", {})
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

        self.static_widget = QWidget()
        s_layout = QFormLayout(self.static_widget)
        s_layout.setContentsMargins(0, 0, 0, 0)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 99999)
        self.count_spin.setValue(self.args.get("loop_count", 10))
        s_layout.addRow("リピート回数:", self.count_spin)
        form.addRow(self.static_widget)

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

        self.status_col_edit = QLineEdit(self.args.get("status_column", ""))
        self.status_col_edit.setPlaceholderText("空欄で書込無 (例: F)")
        e_layout.addRow("ステータス記録列:", self.status_col_edit)

        self.skip_check = QCheckBox("完了ステータス済みの行をスキップ")
        self.skip_check.setChecked(bool(self.args.get("skip_completed", True)))
        e_layout.addRow("", self.skip_check)

        self.cont_check = QCheckBox("エラー発生時に中断せず次の行へ継続")
        self.cont_check.setChecked(bool(self.args.get("continue_on_error", False)))
        e_layout.addRow("", self.cont_check)

        form.addRow(self.excel_widget)

        def _toggle(idx: int) -> None:
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

    def _browse_file(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(
            self,
            "Excelファイルを選択",
            str(self.workflow_dir),
            "Excel Files (*.xlsx *.xls *.xlsm);;All Files (*.*)",
        )
        if fp:
            self.file_edit.setText(fp)

    def _save(self) -> None:
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
