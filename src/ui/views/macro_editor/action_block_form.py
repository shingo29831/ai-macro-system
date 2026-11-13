"""Module: @role: アクションブロック内の各種コマンド引数インライン編集フォームおよび変数参照UIの構築・制御を担当する。"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from .action_form_fields import (
    FORM_STYLESHEET,
    build_browser_fields,
    build_coordinate_fields,
    build_excel_fields,
    build_loop_start_fields,
    build_scroll_fields,
    build_type_text_fields,
    build_wait_fields,
    populate_var_combo,
)


class ActionBlockEditForm(QWidget):
    """単一アクション引数のインライン編集フォームウィジェット。"""

    arg_updated = Signal()
    delete_clicked = Signal()
    variables_loaded = Signal(dict)

    def __init__(
        self,
        method: str,
        args: Dict[str, Any],
        workflow_dir: Path,
        is_in_loop: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.method = method
        self.args = args
        self.workflow_dir = workflow_dir
        self.is_in_loop = is_in_loop
        self.available_vars: Dict[str, Any] = {}
        self._var_combos: List[QComboBox] = []

        # Why: アクションブロック内の入力・メニュー項目の文字色とコントラストを明示固定
        self.setStyleSheet(FORM_STYLESHEET)
        self.edit_layout = QFormLayout(self)
        self.edit_layout.setContentsMargins(0, 10, 0, 0)
        self._build_form()

    def set_available_variables(self, var_map: Dict[str, Any]) -> None:
        """キャンバスまたはExcelファイルから渡された列ヘッダー変数を全コンボボックスへ反映する。"""
        self.available_vars = var_map or {}
        for combo in self._var_combos:
            self._populate_var_combo_widget(combo)

    def _emit_arg_update(self, key: str, value: Any) -> None:
        self.args[key] = value
        self.arg_updated.emit()

    def _update_seq_var(self, target_key: str, param_key: str, value: Any) -> None:
        seq_vars = self.args.setdefault("seq_vars", {})
        target_seq = seq_vars.setdefault(target_key, {})
        target_seq[param_key] = value
        self.arg_updated.emit()

    def _build_form(self) -> None:
        self.args.setdefault("seq_vars", {})

        if self.method in ("click", "move"):
            build_coordinate_fields(self)
        elif self.method == "type_text":
            build_type_text_fields(self)
        elif self.method == "loop_start":
            build_loop_start_fields(self)
        elif self.method == "wait":
            build_wait_fields(self)
        elif self.method == "scroll":
            build_scroll_fields(self)
        elif self.method == "press_key":
            self.key_edit = QLineEdit(self.args.get("key", ""))
            self.key_edit.textChanged.connect(lambda v: self._emit_arg_update("key", v))
            self.edit_layout.addRow("キー名:", self.key_edit)
        elif self.method == "activate_window":
            self.win_edit = QLineEdit(self.args.get("window_title", ""))
            self.win_edit.textChanged.connect(lambda v: self._emit_arg_update("window_title", v))
            self.edit_layout.addRow("ウィンドウ名:", self.win_edit)
        elif self.method == "browser_action":
            build_browser_fields(self)
        elif self.method == "excel_action":
            build_excel_fields(self)

        delete_btn = QPushButton("このアクションを削除")
        delete_btn.setStyleSheet("QPushButton { color: #d13438; font-weight: bold; padding: 4px; }")
        delete_btn.clicked.connect(self.delete_clicked.emit)
        self.edit_layout.addRow("", delete_btn)

    def _create_var_input_row(self, line_edit: QLineEdit) -> QHBoxLayout:
        # Why: 入力欄・変数選択コンボ・参照ボタンを水平統合し統一的UIを提供
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(line_edit)

        combo = QComboBox()
        combo.setFixedWidth(140)
        combo.view().setMinimumWidth(280)
        self._var_combos.append(combo)
        self._populate_var_combo_widget(combo)
        combo.currentIndexChanged.connect(lambda idx: self._on_var_selected_for_edit(combo, line_edit, idx))
        row.addWidget(combo)

        pick_btn = QPushButton("📂")
        pick_btn.setFixedWidth(28)
        pick_btn.setToolTip("Excelファイルを開いて列変数を読み込む")
        pick_btn.clicked.connect(self._open_excel_column_picker)
        row.addWidget(pick_btn)
        return row

    def _populate_var_combo_widget(self, combo: QComboBox) -> None:
        populate_var_combo(combo, self.available_vars)

    def _open_excel_column_picker(self) -> None:
        # Why: 循環インポート回避のため選択時のみloop_widgetsを参照
        from .loop_widgets import get_excel_columns_map

        fp, _ = QFileDialog.getOpenFileName(
            self,
            "列を読み込むExcelファイルを選択",
            str(self.workflow_dir),
            "Excel Files (*.xlsx *.xls *.xlsm);;All Files (*.*)",
        )
        if fp:
            cols_map = get_excel_columns_map(fp, base_dir=self.workflow_dir)
            if cols_map:
                self.set_available_variables(cols_map)
                self.variables_loaded.emit(cols_map)
                self.arg_updated.emit()

    def _on_var_selected_for_edit(self, combo: QComboBox, target_edit: QLineEdit, idx: int) -> None:
        if idx <= 0:
            return
        var_text = combo.itemData(idx)
        if not var_text:
            combo.setCurrentIndex(0)
            return
        if target_edit:
            # Why: カーソル位置への挿入により固定文言と変数の自由な組み合わせを可能化
            if target_edit.text():
                target_edit.insert(var_text)
            else:
                target_edit.setText(var_text)
            target_edit.setFocus()
        combo.setCurrentIndex(0)

    def _browse_excel_into(self, target_edit: QLineEdit) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Excelファイルを選択",
            str(self.workflow_dir),
            "Excel Files (*.xlsx *.xls *.xlsm);;All Files (*.*)",
        )
        if file_path:
            target_edit.setText(file_path)
            self._emit_arg_update("file_path", file_path)
