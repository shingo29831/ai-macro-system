"""Module: @role: ActionBlockEditFormから分離した共通スタイル定数およびループ・ブラウザ・Excel用フォームフィールド構築ヘルパーを提供する。"""

from typing import Any, Dict

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

FORM_STYLESHEET = """
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


def build_loop_start_fields(form: Any) -> None:
    """loop_startコマンドの固定回数・Excel連携設定フィールドをフォームへ構築する。"""
    form.mode_combo = QComboBox()
    form.mode_combo.addItem("固定回数ループ", "static")
    form.mode_combo.addItem("Excelデータ連携ループ", "excel")
    cur_mode = form.args.get("data_source", "static")
    form.mode_combo.setCurrentIndex(1 if cur_mode == "excel" else 0)
    form.edit_layout.addRow("ループ種別:", form.mode_combo)

    form.static_widget = QWidget()
    s_layout = QFormLayout(form.static_widget)
    s_layout.setContentsMargins(0, 0, 0, 0)
    form.count_spin = QSpinBox()
    form.count_spin.setRange(1, 99999)
    form.count_spin.setValue(form.args.get("loop_count", 1))
    form.count_spin.valueChanged.connect(lambda v: form._emit_arg_update("loop_count", v))
    s_layout.addRow("リピート回数:", form.count_spin)
    form.edit_layout.addRow(form.static_widget)

    form.excel_widget = QWidget()
    e_layout = QFormLayout(form.excel_widget)
    e_layout.setContentsMargins(0, 0, 0, 0)

    file_box = QHBoxLayout()
    form.file_edit = QLineEdit(form.args.get("file_path", ""))
    form.file_edit.setPlaceholderText("Excelファイルパス...")
    form.file_edit.textChanged.connect(lambda v: form._emit_arg_update("file_path", v))
    file_box.addWidget(form.file_edit)

    browse_btn = QPushButton("参照...")
    browse_btn.setFixedWidth(60)
    browse_btn.clicked.connect(lambda: form._browse_excel_into(form.file_edit))
    file_box.addWidget(browse_btn)
    e_layout.addRow("Excelファイル:", file_box)

    form.sheet_edit = QLineEdit(form.args.get("sheet_name", ""))
    form.sheet_edit.setPlaceholderText("空欄でアクティブシート")
    form.sheet_edit.textChanged.connect(lambda v: form._emit_arg_update("sheet_name", v))
    e_layout.addRow("シート名:", form.sheet_edit)

    row_box = QHBoxLayout()
    form.start_row_spin = QSpinBox()
    form.start_row_spin.setRange(1, 99999)
    form.start_row_spin.setValue(form.args.get("start_row", 2))
    form.start_row_spin.valueChanged.connect(lambda v: form._emit_arg_update("start_row", v))
    row_box.addWidget(QLabel("開始:"))
    row_box.addWidget(form.start_row_spin)

    form.end_row_spin = QSpinBox()
    form.end_row_spin.setRange(0, 99999)
    form.end_row_spin.setValue(form.args.get("end_row") or 0)
    form.end_row_spin.setSpecialValueText("末尾まで")
    form.end_row_spin.valueChanged.connect(lambda v: form._emit_arg_update("end_row", v if v > 0 else None))
    row_box.addWidget(QLabel("終了:"))
    row_box.addWidget(form.end_row_spin)
    e_layout.addRow("対象行範囲:", row_box)

    form.status_col_edit = QLineEdit(form.args.get("status_column", "E"))
    form.status_col_edit.setPlaceholderText("例: E")
    form.status_col_edit.textChanged.connect(lambda v: form._emit_arg_update("status_column", v.upper()))
    e_layout.addRow("ステータス記録列:", form.status_col_edit)

    form.skip_check = QCheckBox("完了ステータス済みの行をスキップ")
    form.skip_check.setChecked(bool(form.args.get("skip_completed", True)))
    form.skip_check.toggled.connect(lambda v: form._emit_arg_update("skip_completed", v))
    e_layout.addRow("", form.skip_check)

    form.cont_check = QCheckBox("エラー発生時に中断せず次の行へ継続")
    form.cont_check.setChecked(bool(form.args.get("continue_on_error", False)))
    form.cont_check.toggled.connect(lambda v: form._emit_arg_update("continue_on_error", v))
    e_layout.addRow("", form.cont_check)

    form.edit_layout.addRow(form.excel_widget)

    def _toggle_mode(idx: int) -> None:
        is_excel = idx == 1
        form.static_widget.setVisible(not is_excel)
        form.excel_widget.setVisible(is_excel)
        form._emit_arg_update("data_source", "excel" if is_excel else "static")

    form.mode_combo.currentIndexChanged.connect(_toggle_mode)
    _toggle_mode(form.mode_combo.currentIndex())


def build_browser_fields(form: Any) -> None:
    """browser_actionコマンドの引数編集フィールドをフォームへ構築する。"""
    cur_act = form.args.get("action", "open_url")
    if cur_act == "open_url":
        form.b_url_edit = QLineEdit(form.args.get("url", ""))
        form.b_url_edit.setPlaceholderText("https://example.com")
        form.b_url_edit.textChanged.connect(lambda v: form._emit_arg_update("url", v))
        form.edit_layout.addRow("URL:", form.b_url_edit)
        return

    if cur_act in ("click_element", "wait_element", "type_text", "select_option", "set_checkbox", "read_text"):
        placeholder = {
            "select_option": "CSSセレクタ (例: #plan)",
            "set_checkbox": "CSSセレクタ (例: #newsletter)",
        }.get(cur_act, "CSSセレクタ または XPath")
        form.b_sel_edit = QLineEdit(form.args.get("selector", ""))
        form.b_sel_edit.setPlaceholderText(placeholder)
        form.b_sel_edit.textChanged.connect(lambda v: form._emit_arg_update("selector", v))
        form.edit_layout.addRow("対象セレクタ:", form.b_sel_edit)

    if cur_act == "type_text":
        form.b_text_edit = QLineEdit(form.args.get("text", ""))
        form.b_text_edit.setPlaceholderText("入力テキスト または {{row.列名}}")
        form.b_text_edit.textChanged.connect(lambda v: form._emit_arg_update("text", v))
        form.edit_layout.addRow("入力テキスト:", form._create_var_input_row(form.b_text_edit))
    elif cur_act == "select_option":
        form.b_opt_edit = QLineEdit(str(form.args.get("value") or form.args.get("text", "")))
        form.b_opt_edit.setPlaceholderText("選択項目名 または {{row.列名}}")
        form.b_opt_edit.textChanged.connect(lambda v: (form._emit_arg_update("value", v), form._emit_arg_update("text", v)))
        form.edit_layout.addRow("選択する値:", form._create_var_input_row(form.b_opt_edit))
    elif cur_act == "set_checkbox":
        form.b_chk_edit = QLineEdit(str(form.args.get("value", True)))
        form.b_chk_edit.setPlaceholderText("True / False または {{row.列名}}")
        form.b_chk_edit.textChanged.connect(lambda v: form._emit_arg_update("value", v))
        form.edit_layout.addRow("チェック状態:", form._create_var_input_row(form.b_chk_edit))
    elif cur_act == "read_text":
        form.b_var_edit = QLineEdit(form.args.get("variable_name", "extracted_val"))
        form.b_var_edit.setPlaceholderText("格納変数名")
        form.b_var_edit.textChanged.connect(lambda v: form._emit_arg_update("variable_name", v))
        form.edit_layout.addRow("格納変数名:", form.b_var_edit)


def build_excel_fields(form: Any) -> None:
    """excel_actionコマンドの引数編集フィールドをフォームへ構築する。"""
    cur_act = form.args.get("action", "write_cell")
    if cur_act in ("open_workbook", "write_cell", "read_cell"):
        e_file_box = QHBoxLayout()
        form.e_file_edit = QLineEdit(form.args.get("file_path", ""))
        form.e_file_edit.setPlaceholderText("Excelファイルパス（空欄で現在のアクティブブック）")
        form.e_file_edit.textChanged.connect(lambda v: form._emit_arg_update("file_path", v))
        e_file_box.addWidget(form.e_file_edit)

        e_browse_btn = QPushButton("参照...")
        e_browse_btn.setFixedWidth(60)
        e_browse_btn.clicked.connect(lambda: form._browse_excel_into(form.e_file_edit))
        e_file_box.addWidget(e_browse_btn)
        form.edit_layout.addRow("Excelファイル:", e_file_box)

    if cur_act in ("write_cell", "read_cell", "select_sheet"):
        form.e_sheet_edit = QLineEdit(form.args.get("sheet_name", ""))
        form.e_sheet_edit.setPlaceholderText("空欄でアクティブシート")
        form.e_sheet_edit.textChanged.connect(lambda v: form._emit_arg_update("sheet_name", v))
        form.edit_layout.addRow("シート名:", form.e_sheet_edit)

    if cur_act in ("write_cell", "read_cell"):
        form.e_cell_edit = QLineEdit(form.args.get("cell", ""))
        form.e_cell_edit.setPlaceholderText("例: A1")
        form.e_cell_edit.textChanged.connect(lambda v: form._emit_arg_update("cell", v))
        form.edit_layout.addRow("セル番地:", form.e_cell_edit)

    if cur_act == "write_cell":
        form.e_val_edit = QLineEdit(str(form.args.get("value", "")))
        form.e_val_edit.setPlaceholderText("書き込む値 または {{row.列名}}")
        form.e_val_edit.textChanged.connect(lambda v: form._emit_arg_update("value", v))
        form.edit_layout.addRow("書き込み値:", form._create_var_input_row(form.e_val_edit))
    elif cur_act == "read_cell":
        form.e_var_edit = QLineEdit(form.args.get("variable_name", "cell_val"))
        form.e_var_edit.setPlaceholderText("格納変数名")
        form.e_var_edit.textChanged.connect(lambda v: form._emit_arg_update("variable_name", v))
        form.edit_layout.addRow("格納変数名:", form.e_var_edit)


def populate_var_combo(combo: QComboBox, available_vars: Dict[str, Any]) -> None:
    """変数選択コンボボックスへヘッダー名優先・列記号優先セクションを構築する。"""
    # Why: ヘッダー名優先セクションと列記号優先セクションを明瞭に分離し選択性を最大化
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("+ 変数を挿入...", "")
    if available_vars:
        combo.addItem("── 【ヘッダー名で選択】 ──", "")
        for col_key, col_name in available_vars.items():
            clean_name = str(col_name).strip()
            combo.addItem(f"🏷️ {clean_name}  ({col_key}列)", f"{{{{row.{clean_name}}}}}")

        combo.addItem("── 【列記号で選択】 ──", "")
        for col_key, col_name in available_vars.items():
            clean_name = str(col_name).strip()
            combo.addItem(f"🔤 {col_key}列  ({clean_name})", f"{{{{row.{col_key}}}}}")
    else:
        combo.addItem("(Excel未読込: 📂から選択)", "")
    combo.blockSignals(False)


def build_coordinate_fields(form: Any) -> None:
    """click/moveコマンドの座標およびループ差分編集フィールドを構築する。"""
    form.x_spin = QSpinBox()
    form.x_spin.setRange(-9999, 9999)
    form.x_spin.setValue(form.args.get("x", 0))
    form.x_spin.valueChanged.connect(lambda v: form._emit_arg_update("x", v))
    form.edit_layout.addRow("X座標(初期値):", form.x_spin)

    form.y_spin = QSpinBox()
    form.y_spin.setRange(-9999, 9999)
    form.y_spin.setValue(form.args.get("y", 0))
    form.y_spin.valueChanged.connect(lambda v: form._emit_arg_update("y", v))
    form.edit_layout.addRow("Y座標(初期値):", form.y_spin)

    if form.is_in_loop:
        x_seq = form.args["seq_vars"].setdefault("x", {"step": 0})
        form.dx_spin = QSpinBox()
        form.dx_spin.setRange(-999, 999)
        form.dx_spin.setValue(x_seq.get("step", 0))
        form.dx_spin.valueChanged.connect(lambda v: form._update_seq_var("x", "step", v))
        form.edit_layout.addRow("X差分(ループ毎):", form.dx_spin)

        y_seq = form.args["seq_vars"].setdefault("y", {"step": 0})
        form.dy_spin = QSpinBox()
        form.dy_spin.setRange(-999, 999)
        form.dy_spin.setValue(y_seq.get("step", 0))
        form.dy_spin.valueChanged.connect(lambda v: form._update_seq_var("y", "step", v))
        form.edit_layout.addRow("Y差分(ループ毎):", form.dy_spin)


def build_type_text_fields(form: Any) -> None:
    """type_textコマンドの入力文字列およびオプション編集フィールドを構築する。"""
    form.text_edit = QLineEdit(form.args.get("text", ""))
    form.text_edit.setPlaceholderText("入力テキスト または {{row.列名}}")
    form.text_edit.textChanged.connect(lambda v: form._emit_arg_update("text", v))
    form.edit_layout.addRow("テキスト(初期値):", form._create_var_input_row(form.text_edit))

    form.clear_check = QCheckBox("入力前に既存テキストを全消去する")
    form.clear_check.setChecked(bool(form.args.get("clear_before_typing", False)))
    form.clear_check.toggled.connect(lambda v: form._emit_arg_update("clear_before_typing", v))
    form.edit_layout.addRow("", form.clear_check)

    form.clip_check = QCheckBox("クリップボード経由で貼り付ける (IME誤作動防止)")
    form.clip_check.setChecked(bool(form.args.get("use_clipboard", False)))
    form.clip_check.toggled.connect(lambda v: form._emit_arg_update("use_clipboard", v))
    form.edit_layout.addRow("", form.clip_check)

    if form.is_in_loop:
        text_seq = form.args["seq_vars"].setdefault("text", {"step": 1})
        form.seq_step_spin = QSpinBox()
        form.seq_step_spin.setRange(-99999, 99999)
        form.seq_step_spin.setValue(text_seq.get("step", 1))
        form.seq_step_spin.valueChanged.connect(lambda v: form._update_seq_var("text", "step", v))
        form.edit_layout.addRow("テキスト加算(ループ毎):", form.seq_step_spin)


def build_wait_fields(form: Any) -> None:
    """waitコマンドの待機秒数編集フィールドを構築する。"""
    form.duration_spin = QDoubleSpinBox()
    form.duration_spin.setRange(0.1, 3600.0)
    form.duration_spin.setValue(form.args.get("duration", 1.0))
    form.duration_spin.valueChanged.connect(lambda v: form._emit_arg_update("duration", v))
    form.edit_layout.addRow("待機(秒):", form.duration_spin)


def build_scroll_fields(form: Any) -> None:
    """scrollコマンドのスクロール量・座標編集フィールドを構築する。"""
    form.dy_spin = QDoubleSpinBox()
    form.dy_spin.setRange(-100.0, 100.0)
    form.dy_spin.setValue(form.args.get("dy", -5.0))
    form.dy_spin.valueChanged.connect(lambda v: form._emit_arg_update("dy", v))
    form.edit_layout.addRow("垂直スクロール量(dy):", form.dy_spin)

    form.sx_spin = QSpinBox()
    form.sx_spin.setRange(-9999, 9999)
    form.sx_spin.setValue(form.args.get("x", 0))
    form.sx_spin.valueChanged.connect(lambda v: form._emit_arg_update("x", v))
    form.edit_layout.addRow("X座標:", form.sx_spin)

    form.sy_spin = QSpinBox()
    form.sy_spin.setRange(-9999, 9999)
    form.sy_spin.setValue(form.args.get("y", 0))
    form.sy_spin.valueChanged.connect(lambda v: form._emit_arg_update("y", v))
    form.edit_layout.addRow("Y座標:", form.sy_spin)
