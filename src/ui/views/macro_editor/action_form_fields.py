"""Module: @role: ActionBlockEditFormから分離した共通スタイル定数およびループ・ブラウザ・Excel用フォームフィールド構築ヘルパーを提供する。"""

from typing import Any, Dict

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
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


def build_browser_fields(form: Any) -> None:
    """browser_actionコマンドの引数編集フィールドをフォームへ構築する。"""
    cur_act = form.args.get("action", "open_url")
    if cur_act == "open_url":
        url_edit = QLineEdit(form.args.get("url", ""))
        url_edit.setPlaceholderText("https://example.com")
        url_edit.textChanged.connect(lambda v: form.update_arg("url", v))
        form.add_field_row("URL:", url_edit)
        return

    if cur_act in ("click_element", "wait_element", "type_text", "select_option", "set_checkbox", "read_text"):
        placeholder = {
            "select_option": "CSSセレクタ (例: #plan)",
            "set_checkbox": "CSSセレクタ (例: #newsletter)",
        }.get(cur_act, "CSSセレクタ または XPath")
        sel_edit = QLineEdit(form.args.get("selector", ""))
        sel_edit.setPlaceholderText(placeholder)
        sel_edit.textChanged.connect(lambda v: form.update_arg("selector", v))
        form.add_field_row("対象セレクタ:", sel_edit)

    if cur_act == "type_text":
        text_edit = QLineEdit(form.args.get("text", ""))
        text_edit.setPlaceholderText("入力テキスト または {{row.列名}}")
        text_edit.textChanged.connect(lambda v: form.update_arg("text", v))
        form.add_field_row("入力テキスト:", form.create_var_input_row(text_edit))
    elif cur_act == "select_option":
        opt_edit = QLineEdit(str(form.args.get("value") or form.args.get("text", "")))
        opt_edit.setPlaceholderText("選択項目名 または {{row.列名}}")
        opt_edit.textChanged.connect(lambda v: (form.update_arg("value", v), form.update_arg("text", v)))
        form.add_field_row("選択する値:", form.create_var_input_row(opt_edit))
    elif cur_act == "set_checkbox":
        chk_edit = QLineEdit(str(form.args.get("value", True)))
        chk_edit.setPlaceholderText("True / False または {{row.列名}}")
        chk_edit.textChanged.connect(lambda v: form.update_arg("value", v))
        form.add_field_row("チェック状態:", form.create_var_input_row(chk_edit))
    elif cur_act == "read_text":
        var_edit = QLineEdit(form.args.get("variable_name", "extracted_val"))
        var_edit.setPlaceholderText("格納変数名")
        var_edit.textChanged.connect(lambda v: form.update_arg("variable_name", v))
        form.add_field_row("格納変数名:", var_edit)


def build_excel_fields(form: Any) -> None:
    """excel_actionコマンドの引数編集フィールドをフォームへ構築する。"""
    cur_act = form.args.get("action", "write_cell")
    if cur_act in ("open_workbook", "write_cell", "read_cell"):
        e_file_box = QHBoxLayout()
        file_edit = QLineEdit(form.args.get("file_path", ""))
        file_edit.setPlaceholderText("Excelファイルパス（空欄で現在のアクティブブック）")
        file_edit.textChanged.connect(lambda v: form.update_arg("file_path", v))
        e_file_box.addWidget(file_edit)

        e_browse_btn = QPushButton("参照...")
        e_browse_btn.setFixedWidth(60)
        e_browse_btn.clicked.connect(lambda: form.browse_excel_into(file_edit))
        e_file_box.addWidget(e_browse_btn)
        form.add_field_row("Excelファイル:", e_file_box)

    if cur_act in ("write_cell", "read_cell", "select_sheet"):
        sheet_edit = QLineEdit(form.args.get("sheet_name", ""))
        sheet_edit.setPlaceholderText("空欄でアクティブシート")
        sheet_edit.textChanged.connect(lambda v: form.update_arg("sheet_name", v))
        form.add_field_row("シート名:", sheet_edit)

    if cur_act in ("write_cell", "read_cell"):
        cell_edit = QLineEdit(form.args.get("cell", ""))
        cell_edit.setPlaceholderText("例: A1")
        cell_edit.textChanged.connect(lambda v: form.update_arg("cell", v))
        form.add_field_row("セル番地:", cell_edit)

    if cur_act == "write_cell":
        val_edit = QLineEdit(str(form.args.get("value", "")))
        val_edit.setPlaceholderText("書き込む値 または {{row.列名}}")
        val_edit.textChanged.connect(lambda v: form.update_arg("value", v))
        form.add_field_row("書き込み値:", form.create_var_input_row(val_edit))
    elif cur_act == "read_cell":
        var_edit = QLineEdit(form.args.get("variable_name", "cell_val"))
        var_edit.setPlaceholderText("格納変数名")
        var_edit.textChanged.connect(lambda v: form.update_arg("variable_name", v))
        form.add_field_row("格納変数名:", var_edit)


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
    x_spin = QSpinBox()
    x_spin.setRange(-9999, 9999)
    x_spin.setValue(form.args.get("x", 0))
    x_spin.valueChanged.connect(lambda v: form.update_arg("x", v))
    form.add_field_row("X座標(初期値):", x_spin)

    y_spin = QSpinBox()
    y_spin.setRange(-9999, 9999)
    y_spin.setValue(form.args.get("y", 0))
    y_spin.valueChanged.connect(lambda v: form.update_arg("y", v))
    form.add_field_row("Y座標(初期値):", y_spin)

    if form.is_in_loop:
        x_seq = form.args["seq_vars"].setdefault("x", {"step": 0})
        dx_spin = QSpinBox()
        dx_spin.setRange(-999, 999)
        dx_spin.setValue(x_seq.get("step", 0))
        dx_spin.valueChanged.connect(lambda v: form.update_seq_var("x", "step", v))
        form.add_field_row("X差分(ループ毎):", dx_spin)

        y_seq = form.args["seq_vars"].setdefault("y", {"step": 0})
        dy_spin = QSpinBox()
        dy_spin.setRange(-999, 999)
        dy_spin.setValue(y_seq.get("step", 0))
        dy_spin.valueChanged.connect(lambda v: form.update_seq_var("y", "step", v))
        form.add_field_row("Y差分(ループ毎):", dy_spin)


def build_type_text_fields(form: Any) -> None:
    """type_textコマンドの入力文字列およびオプション編集フィールドを構築する。"""
    text_edit = QLineEdit(form.args.get("text", ""))
    text_edit.setPlaceholderText("入力テキスト または {{row.列名}}")
    text_edit.textChanged.connect(lambda v: form.update_arg("text", v))
    form.add_field_row("テキスト(初期値):", form.create_var_input_row(text_edit))

    clear_check = QCheckBox("入力前に既存テキストを全消去する")
    clear_check.setChecked(bool(form.args.get("clear_before_typing", False)))
    clear_check.toggled.connect(lambda v: form.update_arg("clear_before_typing", v))
    form.add_field_row("", clear_check)

    clip_check = QCheckBox("クリップボード経由で貼り付ける (IME誤作動防止)")
    clip_check.setChecked(bool(form.args.get("use_clipboard", False)))
    clip_check.toggled.connect(lambda v: form.update_arg("use_clipboard", v))
    form.add_field_row("", clip_check)

    if form.is_in_loop:
        text_seq = form.args["seq_vars"].setdefault("text", {"step": 1})
        seq_step_spin = QSpinBox()
        seq_step_spin.setRange(-99999, 99999)
        seq_step_spin.setValue(text_seq.get("step", 1))
        seq_step_spin.valueChanged.connect(lambda v: form.update_seq_var("text", "step", v))
        form.add_field_row("テキスト加算(ループ毎):", seq_step_spin)


def build_wait_fields(form: Any) -> None:
    """waitコマンドの待機秒数編集フィールドを構築する。"""
    duration_spin = QDoubleSpinBox()
    duration_spin.setRange(0.1, 3600.0)
    duration_spin.setValue(form.args.get("duration", 1.0))
    duration_spin.valueChanged.connect(lambda v: form.update_arg("duration", v))
    form.add_field_row("待機(秒):", duration_spin)


def build_scroll_fields(form: Any) -> None:
    """scrollコマンドのスクロール量・座標編集フィールドを構築する。"""
    dy_spin = QDoubleSpinBox()
    dy_spin.setRange(-100.0, 100.0)
    dy_spin.setValue(form.args.get("dy", -5.0))
    dy_spin.valueChanged.connect(lambda v: form.update_arg("dy", v))
    form.add_field_row("垂直スクロール量(dy):", dy_spin)

    sx_spin = QSpinBox()
    sx_spin.setRange(-9999, 9999)
    sx_spin.setValue(form.args.get("x", 0))
    sx_spin.valueChanged.connect(lambda v: form.update_arg("x", v))
    form.add_field_row("X座標:", sx_spin)

    sy_spin = QSpinBox()
    sy_spin.setRange(-9999, 9999)
    sy_spin.setValue(form.args.get("y", 0))
    sy_spin.valueChanged.connect(lambda v: form.update_arg("y", v))
    form.add_field_row("Y座標:", sy_spin)
