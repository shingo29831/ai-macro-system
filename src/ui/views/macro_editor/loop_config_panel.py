"""Module: @role: 固定回数ループおよびExcelデータ連携ループの共通設定フォームUIを提供する再利用可能パネル。"""

from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from .excel_column_scanner import get_excel_columns_map


class LoopConfigPanel(QWidget):
    """ループ種別（固定回数 / Excel連携）の編集フィールドを一元管理する共通パネル。"""

    config_changed = Signal()
    excel_source_changed = Signal()

    def __init__(
        self,
        args: Dict[str, Any],
        workflow_dir: Path,
        commit_on_change: bool = True,
        show_static_count: bool = True,
        show_columns_preview: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.args = args
        self.workflow_dir = workflow_dir
        self._commit_on_change = commit_on_change
        self._show_static_count = show_static_count
        self._show_columns_preview = show_columns_preview
        self._build_ui()

    def _build_ui(self) -> None:
        self.form_layout = QFormLayout(self)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setSpacing(8)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("固定回数ループ", "static")
        self.mode_combo.addItem("Excelデータ連携ループ", "excel")
        cur_mode = self.args.get("data_source") or "static"
        self.mode_combo.setCurrentIndex(1 if cur_mode == "excel" else 0)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.form_layout.addRow("種別:", self.mode_combo)

        self.static_widget = QWidget()
        s_layout = QFormLayout(self.static_widget)
        s_layout.setContentsMargins(0, 0, 0, 0)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 99999)
        self.count_spin.setValue(int(self.args.get("loop_count") or 10))
        self.count_spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.count_spin.valueChanged.connect(lambda v: self._commit_field("loop_count", v))
        s_layout.addRow("リピート回数:", self.count_spin)
        self.form_layout.addRow(self.static_widget)

        self.excel_widget = QWidget()
        e_layout = QFormLayout(self.excel_widget)
        e_layout.setContentsMargins(0, 0, 0, 0)
        e_layout.setSpacing(7)

        file_box = QHBoxLayout()
        self.file_edit = QLineEdit(self.args.get("file_path") or "")
        self.file_edit.setPlaceholderText("Excelファイル...")
        self.file_edit.textChanged.connect(self._on_file_changed)
        file_box.addWidget(self.file_edit)

        browse_btn = QPushButton("参照")
        browse_btn.setFixedWidth(52)
        browse_btn.clicked.connect(self._browse_excel_file)
        file_box.addWidget(browse_btn)
        e_layout.addRow("ファイル:", file_box)

        self.sheet_edit = QLineEdit(self.args.get("sheet_name") or "")
        self.sheet_edit.setPlaceholderText("空欄でアクティブシート")
        self.sheet_edit.textChanged.connect(self._on_sheet_changed)
        e_layout.addRow("シート:", self.sheet_edit)

        self.cols_preview_label = QLabel()
        self.cols_preview_label.setWordWrap(True)
        self.cols_preview_label.setStyleSheet(
            "font-size: 11px; color: #107c41; background: #eaf6ee; padding: 6px; border-radius: 4px;"
        )
        if self._show_columns_preview:
            e_layout.addRow("検出列変数:", self.cols_preview_label)

        # Why: 開始行と終了行を独立行に配置しスピンボタンの重なりを防止
        self.start_row_spin = QSpinBox()
        self.start_row_spin.setRange(1, 99999)
        self.start_row_spin.setValue(int(self.args.get("start_row") or 2))
        self.start_row_spin.setFixedWidth(110)
        self.start_row_spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.start_row_spin.valueChanged.connect(lambda v: self._commit_field("start_row", v))
        e_layout.addRow("開始行:", self.start_row_spin)

        self.end_row_spin = QSpinBox()
        self.end_row_spin.setRange(0, 99999)
        self.end_row_spin.setValue(int(self.args.get("end_row") or 0))
        self.end_row_spin.setSpecialValueText("末尾まで")
        self.end_row_spin.setFixedWidth(110)
        self.end_row_spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.end_row_spin.valueChanged.connect(lambda v: self._commit_field("end_row", v if v > 0 else None))
        e_layout.addRow("終了行:", self.end_row_spin)

        self.status_col_edit = QLineEdit(self.args.get("status_column") or "")
        self.status_col_edit.setPlaceholderText("空欄で書込無 (例: F)")
        self.status_col_edit.setFixedWidth(110)
        self.status_col_edit.textChanged.connect(
            lambda v: self._commit_field("status_column", v.strip().upper() or None)
        )
        e_layout.addRow("ステータス列:", self.status_col_edit)

        skip_completed = self.args.get("skip_completed")
        self.skip_check = QCheckBox("完了行をスキップ")
        self.skip_check.setChecked(True if skip_completed is None else bool(skip_completed))
        self.skip_check.toggled.connect(lambda v: self._commit_field("skip_completed", v))
        e_layout.addRow("", self.skip_check)

        self.cont_check = QCheckBox("エラー時次行継続")
        self.cont_check.setChecked(bool(self.args.get("continue_on_error", False)))
        self.cont_check.toggled.connect(lambda v: self._commit_field("continue_on_error", v))
        e_layout.addRow("", self.cont_check)

        self.form_layout.addRow(self.excel_widget)
        self._sync_visibility()
        if self.mode_combo.currentIndex() == 1:
            self.refresh_columns_preview()

    def _sync_visibility(self) -> None:
        is_excel = self.mode_combo.currentIndex() == 1
        self.static_widget.setVisible(not is_excel and self._show_static_count)
        self.excel_widget.setVisible(is_excel)

    def _commit_field(self, key: str, value: Any) -> None:
        if self._commit_on_change:
            self.args[key] = value
            self.config_changed.emit()

    def _on_mode_changed(self, idx: int) -> None:
        mode = "excel" if idx == 1 else "static"
        self._sync_visibility()
        if self._commit_on_change:
            # Why: UI表示上のデフォルト値とargs辞書の不整合を防ぐため切替時に現在値を同期
            self.apply_to_args()
            self.config_changed.emit()
        elif mode == "excel":
            self.refresh_columns_preview()

    def _on_file_changed(self, value: str) -> None:
        if self._commit_on_change:
            self.args["file_path"] = value
        self.refresh_columns_preview()
        if self._commit_on_change:
            self.excel_source_changed.emit()
            self.config_changed.emit()

    def _on_sheet_changed(self, value: str) -> None:
        if self._commit_on_change:
            self.args["sheet_name"] = value
        self.refresh_columns_preview()
        if self._commit_on_change:
            self.excel_source_changed.emit()
            self.config_changed.emit()

    def _browse_excel_file(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(
            self,
            "Excelファイルを選択",
            str(self.workflow_dir),
            "Excel Files (*.xlsx *.xls *.xlsm);;All Files (*.*)",
        )
        if fp:
            self.file_edit.setText(fp)

    def refresh_columns_preview(self) -> None:
        if not self._show_columns_preview:
            return
        f_path = self.file_edit.text().strip()
        s_name = self.sheet_edit.text().strip() or None
        cols_map = get_excel_columns_map(f_path, s_name, self.workflow_dir)
        if self._commit_on_change:
            self.args["columns_map"] = cols_map
        preview_texts = [f"{col}列:{name}" for col, name in list(cols_map.items())[:6]]
        if len(cols_map) > 6:
            preview_texts.append(f"...他{len(cols_map) - 6}列")
        self.cols_preview_label.setText(" | ".join(preview_texts) if preview_texts else "列未検出")

    def apply_to_args(self) -> None:
        is_excel = self.mode_combo.currentIndex() == 1
        self.args["data_source"] = "excel" if is_excel else "static"
        if is_excel:
            self.args["file_path"] = self.file_edit.text()
            self.args["sheet_name"] = self.sheet_edit.text()
            self.args["start_row"] = self.start_row_spin.value()
            ed = self.end_row_spin.value()
            self.args["end_row"] = ed if ed > 0 else None
            self.args["status_column"] = self.status_col_edit.text().strip().upper() or None
            self.args["skip_completed"] = self.skip_check.isChecked()
            self.args["continue_on_error"] = self.cont_check.isChecked()
            self.args["columns_map"] = get_excel_columns_map(
                self.args["file_path"], self.args["sheet_name"] or None, self.workflow_dir
            )
        else:
            self.args["loop_count"] = self.count_spin.value()
