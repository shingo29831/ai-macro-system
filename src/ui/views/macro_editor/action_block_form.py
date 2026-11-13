"""Module: @role: アクションブロック内の各種コマンド引数インライン編集フォームおよび変数参照UIの構築・制御を担当する。"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)


_FORM_STYLESHEET = """
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
"""


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
        self.setStyleSheet(_FORM_STYLESHEET)
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
            self._build_coordinate_fields()
        elif self.method == "type_text":
            self._build_type_text_fields()
        elif self.method == "loop_start":
            self._build_loop_start_fields()
        elif self.method == "wait":
            self._build_wait_fields()
        elif self.method == "scroll":
            self._build_scroll_fields()
        elif self.method == "press_key":
            self.key_edit = QLineEdit(self.args.get("key", ""))
            self.key_edit.textChanged.connect(lambda v: self._emit_arg_update("key", v))
            self.edit_layout.addRow("キー名:", self.key_edit)
        elif self.method == "activate_window":
            self.win_edit = QLineEdit(self.args.get("window_title", ""))
            self.win_edit.textChanged.connect(lambda v: self._emit_arg_update("window_title", v))
            self.edit_layout.addRow("ウィンドウ名:", self.win_edit)
        elif self.method == "browser_action":
            self._build_browser_fields()
        elif self.method == "excel_action":
            self._build_excel_fields()

        delete_btn = QPushButton("このアクションを削除")
        delete_btn.setStyleSheet("QPushButton { color: #d13438; font-weight: bold; padding: 4px; }")
        delete_btn.clicked.connect(self.delete_clicked.emit)
        self.edit_layout.addRow("", delete_btn)

    def _build_coordinate_fields(self) -> None:
        self.x_spin = QSpinBox()
        self.x_spin.setRange(-9999, 9999)
        self.x_spin.setValue(self.args.get("x", 0))
        self.x_spin.valueChanged.connect(lambda v: self._emit_arg_update("x", v))
        self.edit_layout.addRow("X座標(初期値):", self.x_spin)

        self.y_spin = QSpinBox()
        self.y_spin.setRange(-9999, 9999)
        self.y_spin.setValue(self.args.get("y", 0))
        self.y_spin.valueChanged.connect(lambda v: self._emit_arg_update("y", v))
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

    def _build_type_text_fields(self) -> None:
        self.text_edit = QLineEdit(self.args.get("text", ""))
        self.text_edit.setPlaceholderText("入力テキスト または {{row.列名}}")
        self.text_edit.textChanged.connect(lambda v: self._emit_arg_update("text", v))
        self.edit_layout.addRow("テキスト(初期値):", self._create_var_input_row(self.text_edit))

        self.clear_check = QCheckBox("入力前に既存テキストを全消去する")
        self.clear_check.setChecked(bool(self.args.get("clear_before_typing", False)))
        self.clear_check.toggled.connect(lambda v: self._emit_arg_update("clear_before_typing", v))
        self.edit_layout.addRow("", self.clear_check)

        self.clip_check = QCheckBox("クリップボード経由で貼り付ける (IME誤作動防止)")
        self.clip_check.setChecked(bool(self.args.get("use_clipboard", False)))
        self.clip_check.toggled.connect(lambda v: self._emit_arg_update("use_clipboard", v))
        self.edit_layout.addRow("", self.clip_check)

        if self.is_in_loop:
            text_seq = self.args["seq_vars"].setdefault("text", {"step": 1})
            self.seq_step_spin = QSpinBox()
            self.seq_step_spin.setRange(-99999, 99999)
            self.seq_step_spin.setValue(text_seq.get("step", 1))
            self.seq_step_spin.valueChanged.connect(lambda v: self._update_seq_var("text", "step", v))
            self.edit_layout.addRow("テキスト加算(ループ毎):", self.seq_step_spin)

    def _build_loop_start_fields(self) -> None:
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
        self.count_spin.valueChanged.connect(lambda v: self._emit_arg_update("loop_count", v))
        s_layout.addRow("リピート回数:", self.count_spin)
        self.edit_layout.addRow(self.static_widget)

        self.excel_widget = QWidget()
        e_layout = QFormLayout(self.excel_widget)
        e_layout.setContentsMargins(0, 0, 0, 0)

        file_box = QHBoxLayout()
        self.file_edit = QLineEdit(self.args.get("file_path", ""))
        self.file_edit.setPlaceholderText("Excelファイルパス...")
        self.file_edit.textChanged.connect(lambda v: self._emit_arg_update("file_path", v))
        file_box.addWidget(self.file_edit)

        browse_btn = QPushButton("参照...")
        browse_btn.setFixedWidth(60)
        browse_btn.clicked.connect(lambda: self._browse_excel_into(self.file_edit))
        file_box.addWidget(browse_btn)
        e_layout.addRow("Excelファイル:", file_box)

        self.sheet_edit = QLineEdit(self.args.get("sheet_name", ""))
        self.sheet_edit.setPlaceholderText("空欄でアクティブシート")
        self.sheet_edit.textChanged.connect(lambda v: self._emit_arg_update("sheet_name", v))
        e_layout.addRow("シート名:", self.sheet_edit)

        row_box = QHBoxLayout()
        self.start_row_spin = QSpinBox()
        self.start_row_spin.setRange(1, 99999)
        self.start_row_spin.setValue(self.args.get("start_row", 2))
        self.start_row_spin.valueChanged.connect(lambda v: self._emit_arg_update("start_row", v))
        row_box.addWidget(QLabel("開始:"))
        row_box.addWidget(self.start_row_spin)

        self.end_row_spin = QSpinBox()
        self.end_row_spin.setRange(0, 99999)
        self.end_row_spin.setValue(self.args.get("end_row") or 0)
        self.end_row_spin.setSpecialValueText("末尾まで")
        self.end_row_spin.valueChanged.connect(lambda v: self._emit_arg_update("end_row", v if v > 0 else None))
        row_box.addWidget(QLabel("終了:"))
        row_box.addWidget(self.end_row_spin)
        e_layout.addRow("対象行範囲:", row_box)

        self.status_col_edit = QLineEdit(self.args.get("status_column", "E"))
        self.status_col_edit.setPlaceholderText("例: E")
        self.status_col_edit.textChanged.connect(lambda v: self._emit_arg_update("status_column", v.upper()))
        e_layout.addRow("ステータス記録列:", self.status_col_edit)

        self.skip_check = QCheckBox("完了ステータス済みの行をスキップ")
        self.skip_check.setChecked(bool(self.args.get("skip_completed", True)))
        self.skip_check.toggled.connect(lambda v: self._emit_arg_update("skip_completed", v))
        e_layout.addRow("", self.skip_check)

        self.cont_check = QCheckBox("エラー発生時に中断せず次の行へ継続")
        self.cont_check.setChecked(bool(self.args.get("continue_on_error", False)))
        self.cont_check.toggled.connect(lambda v: self._emit_arg_update("continue_on_error", v))
        e_layout.addRow("", self.cont_check)

        self.edit_layout.addRow(self.excel_widget)

        def _toggle_mode(idx: int) -> None:
            is_excel = idx == 1
            self.static_widget.setVisible(not is_excel)
            self.excel_widget.setVisible(is_excel)
            self._emit_arg_update("data_source", "excel" if is_excel else "static")

        self.mode_combo.currentIndexChanged.connect(_toggle_mode)
        _toggle_mode(self.mode_combo.currentIndex())

    def _build_wait_fields(self) -> None:
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.1, 3600.0)
        self.duration_spin.setValue(self.args.get("duration", 1.0))
        self.duration_spin.valueChanged.connect(lambda v: self._emit_arg_update("duration", v))
        self.edit_layout.addRow("待機(秒):", self.duration_spin)

    def _build_scroll_fields(self) -> None:
        self.dy_spin = QDoubleSpinBox()
        self.dy_spin.setRange(-100.0, 100.0)
        self.dy_spin.setValue(self.args.get("dy", -5.0))
        self.dy_spin.valueChanged.connect(lambda v: self._emit_arg_update("dy", v))
        self.edit_layout.addRow("垂直スクロール量(dy):", self.dy_spin)

        self.sx_spin = QSpinBox()
        self.sx_spin.setRange(-9999, 9999)
        self.sx_spin.setValue(self.args.get("x", 0))
        self.sx_spin.valueChanged.connect(lambda v: self._emit_arg_update("x", v))
        self.edit_layout.addRow("X座標:", self.sx_spin)

        self.sy_spin = QSpinBox()
        self.sy_spin.setRange(-9999, 9999)
        self.sy_spin.setValue(self.args.get("y", 0))
        self.sy_spin.valueChanged.connect(lambda v: self._emit_arg_update("y", v))
        self.edit_layout.addRow("Y座標:", self.sy_spin)

    def _build_browser_fields(self) -> None:
        cur_act = self.args.get("action", "open_url")
        if cur_act == "open_url":
            self.b_url_edit = QLineEdit(self.args.get("url", ""))
            self.b_url_edit.setPlaceholderText("https://example.com")
            self.b_url_edit.textChanged.connect(lambda v: self._emit_arg_update("url", v))
            self.edit_layout.addRow("URL:", self.b_url_edit)
            return

        if cur_act in ("click_element", "wait_element", "type_text", "select_option", "set_checkbox", "read_text"):
            placeholder = {
                "select_option": "CSSセレクタ (例: #plan)",
                "set_checkbox": "CSSセレクタ (例: #newsletter)",
            }.get(cur_act, "CSSセレクタ または XPath")
            self.b_sel_edit = QLineEdit(self.args.get("selector", ""))
            self.b_sel_edit.setPlaceholderText(placeholder)
            self.b_sel_edit.textChanged.connect(lambda v: self._emit_arg_update("selector", v))
            self.edit_layout.addRow("対象セレクタ:", self.b_sel_edit)

        if cur_act == "type_text":
            self.b_text_edit = QLineEdit(self.args.get("text", ""))
            self.b_text_edit.setPlaceholderText("入力テキスト または {{row.列名}}")
            self.b_text_edit.textChanged.connect(lambda v: self._emit_arg_update("text", v))
            self.edit_layout.addRow("入力テキスト:", self._create_var_input_row(self.b_text_edit))
        elif cur_act == "select_option":
            self.b_opt_edit = QLineEdit(str(self.args.get("value") or self.args.get("text", "")))
            self.b_opt_edit.setPlaceholderText("選択項目名 または {{row.列名}}")
            self.b_opt_edit.textChanged.connect(lambda v: (self._emit_arg_update("value", v), self._emit_arg_update("text", v)))
            self.edit_layout.addRow("選択する値:", self._create_var_input_row(self.b_opt_edit))
        elif cur_act == "set_checkbox":
            self.b_chk_edit = QLineEdit(str(self.args.get("value", True)))
            self.b_chk_edit.setPlaceholderText("True / False または {{row.列名}}")
            self.b_chk_edit.textChanged.connect(lambda v: self._emit_arg_update("value", v))
            self.edit_layout.addRow("チェック状態:", self._create_var_input_row(self.b_chk_edit))
        elif cur_act == "read_text":
            self.b_var_edit = QLineEdit(self.args.get("variable_name", "extracted_val"))
            self.b_var_edit.setPlaceholderText("格納変数名")
            self.b_var_edit.textChanged.connect(lambda v: self._emit_arg_update("variable_name", v))
            self.edit_layout.addRow("格納変数名:", self.b_var_edit)

    def _build_excel_fields(self) -> None:
        cur_act = self.args.get("action", "write_cell")
        if cur_act in ("open_workbook", "write_cell", "read_cell"):
            e_file_box = QHBoxLayout()
            self.e_file_edit = QLineEdit(self.args.get("file_path", ""))
            self.e_file_edit.setPlaceholderText("Excelファイルパス（空欄で現在のアクティブブック）")
            self.e_file_edit.textChanged.connect(lambda v: self._emit_arg_update("file_path", v))
            e_file_box.addWidget(self.e_file_edit)

            e_browse_btn = QPushButton("参照...")
            e_browse_btn.setFixedWidth(60)
            e_browse_btn.clicked.connect(lambda: self._browse_excel_into(self.e_file_edit))
            e_file_box.addWidget(e_browse_btn)
            self.edit_layout.addRow("Excelファイル:", e_file_box)

        if cur_act in ("write_cell", "read_cell", "select_sheet"):
            self.e_sheet_edit = QLineEdit(self.args.get("sheet_name", ""))
            self.e_sheet_edit.setPlaceholderText("空欄でアクティブシート")
            self.e_sheet_edit.textChanged.connect(lambda v: self._emit_arg_update("sheet_name", v))
            self.edit_layout.addRow("シート名:", self.e_sheet_edit)

        if cur_act in ("write_cell", "read_cell"):
            self.e_cell_edit = QLineEdit(self.args.get("cell", ""))
            self.e_cell_edit.setPlaceholderText("例: A1")
            self.e_cell_edit.textChanged.connect(lambda v: self._emit_arg_update("cell", v))
            self.edit_layout.addRow("セル番地:", self.e_cell_edit)

        if cur_act == "write_cell":
            self.e_val_edit = QLineEdit(str(self.args.get("value", "")))
            self.e_val_edit.setPlaceholderText("書き込む値 または {{row.列名}}")
            self.e_val_edit.textChanged.connect(lambda v: self._emit_arg_update("value", v))
            self.edit_layout.addRow("書き込み値:", self._create_var_input_row(self.e_val_edit))
        elif cur_act == "read_cell":
            self.e_var_edit = QLineEdit(self.args.get("variable_name", "cell_val"))
            self.e_var_edit.setPlaceholderText("格納変数名")
            self.e_var_edit.textChanged.connect(lambda v: self._emit_arg_update("variable_name", v))
            self.edit_layout.addRow("格納変数名:", self.e_var_edit)

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
        # Why: ヘッダー名優先セクションと列記号優先セクションを明瞭に分離し選択性を最大化
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("+ 変数を挿入...", "")
        if self.available_vars:
            combo.addItem("── 【ヘッダー名で選択】 ──", "")
            for col_key, col_name in self.available_vars.items():
                clean_name = str(col_name).strip()
                combo.addItem(f"🏷️ {clean_name}  ({col_key}列)", f"{{{{row.{clean_name}}}}}")

            combo.addItem("── 【列記号で選択】 ──", "")
            for col_key, col_name in self.available_vars.items():
                clean_name = str(col_name).strip()
                combo.addItem(f"🔤 {col_key}列  ({clean_name})", f"{{{{row.{col_key}}}}}")
        else:
            combo.addItem("(Excel未読込: 📂から選択)", "")
        combo.blockSignals(False)

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
