"""Module: @role: ホーム画面。非ITユーザー向けのマクロ一覧表示・検索・並び替え・チェックボックス一括削除・実行制御を担当するビュー。"""

import re
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CheckBox,
    ComboBox,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
)

from models.data_types import MacroSummary
from ui.viewmodels.main_viewmodel import MainViewModel
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from ui.views.macro_card_widget import MacroCardWidget
from ui.views.macro_list_widget import MacroListWidget


class MacroCardWidget(QFrame):
    """単一マクロの情報を大画面でも視認性高く表示するカードウィジェット"""

    card_clicked = Signal(str)
    card_double_clicked = Signal(str)
    card_checked_changed = Signal(str, bool)
    run_requested = Signal(str)
    edit_requested = Signal(str)

    def __init__(
        self,
        macro: MacroSummary,
        is_delete_mode: bool = False,
        is_selected: bool = False,
        is_checked: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.macro = macro
        self._is_delete_mode = is_delete_mode
        self._is_selected = is_selected
        self._is_checked = is_checked

        self.setObjectName("MacroCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(156)

        self._build_ui()
        self._update_card_style()

    def _font(self, size: int, bold: bool = False) -> QFont:
        font = QFont("Yu Gothic UI", size)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return font

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        # 上部: チェックボックス + マクロ名 + 自己修復バッジ
        top_layout = QHBoxLayout()
        top_layout.setSpacing(10)

        self.chk_box = CheckBox(self)
        self.chk_box.setVisible(self._is_delete_mode)
        self.chk_box.setChecked(self._is_checked)
        self.chk_box.stateChanged.connect(self._on_check_changed)
        top_layout.addWidget(self.chk_box)

        self.lbl_name = QLabel(self.macro.name, self)
        self.lbl_name.setFont(self._font(12, bold=True))
        self.lbl_name.setStyleSheet("color: #0f172a;")
        top_layout.addWidget(self.lbl_name, 1)

        heal_count_str = str(self.macro.heals)
        is_healed = heal_count_str not in ("0", "", "0回")
        badge_bg = "#ecfdf5" if is_healed else "#f1f5f9"
        badge_color = "#059669" if is_healed else "#64748b"
        badge_border = "#a7f3d0" if is_healed else "#e2e8f0"

        self.lbl_badge = QLabel(f"修復: {self.macro.heals}", self)
        self.lbl_badge.setFont(self._font(9, bold=True))
        self.lbl_badge.setStyleSheet(
            f"background-color: {badge_bg}; color: {badge_color}; "
            f"border: 1px solid {badge_border}; border-radius: 6px; padding: 2px 8px;"
        )
        top_layout.addWidget(self.lbl_badge)

        layout.addLayout(top_layout)

        # メタ情報
        meta_layout = QVBoxLayout()
        meta_layout.setSpacing(3)

        self.lbl_updated = QLabel(f"最終更新: {self.macro.updated_at}", self)
        self.lbl_updated.setFont(self._font(9.5))
        self.lbl_updated.setStyleSheet("color: #475569;")

        self.lbl_created = QLabel(f"作成日時: {self.macro.created_at}", self)
        self.lbl_created.setFont(self._font(9))
        self.lbl_created.setStyleSheet("color: #94a3b8;")

        meta_layout.addWidget(self.lbl_updated)
        meta_layout.addWidget(self.lbl_created)
        layout.addLayout(meta_layout)

        layout.addStretch(1)

        # 下部アクションボタン
        self.action_layout = QHBoxLayout()
        self.action_layout.setSpacing(8)

        self.btn_edit = PushButton("✎ 編集", self)
        self.btn_edit.setFont(self._font(9.5))
        self.btn_edit.setFixedSize(76, 30)
        self.btn_edit.clicked.connect(lambda: self.edit_requested.emit(self.macro.name))

        self.btn_run = PrimaryPushButton("▶ 実行", self)
        self.btn_run.setFont(self._font(9.5, bold=True))
        self.btn_run.setFixedSize(80, 30)
        self.btn_run.clicked.connect(lambda: self.run_requested.emit(self.macro.name))

        self.action_layout.addStretch(1)
        self.action_layout.addWidget(self.btn_edit)
        self.action_layout.addWidget(self.btn_run)

        if self._is_delete_mode:
            self.btn_edit.setVisible(False)
            self.btn_run.setVisible(False)

        layout.addLayout(self.action_layout)

    def set_selected(self, selected: bool):
        if self._is_selected != selected:
            self._is_selected = selected
            self._update_card_style()

    def set_checked(self, checked: bool):
        self._is_checked = checked
        self.chk_box.blockSignals(True)
        self.chk_box.setChecked(checked)
        self.chk_box.blockSignals(False)
        self._update_card_style()

    def set_delete_mode(self, enabled: bool):
        self._is_delete_mode = enabled
        self.chk_box.setVisible(enabled)
        self.btn_edit.setVisible(not enabled)
        self.btn_run.setVisible(not enabled)
        self._update_card_style()

    def _update_card_style(self):
        if self._is_delete_mode and self._is_checked:
            self.setStyleSheet(
                "QFrame#MacroCard {"
                "  background-color: #fef2f2;"
                "  border: 2px solid #ef4444;"
                "  border-radius: 12px;"
                "}"
            )
        elif self._is_selected:
            self.setStyleSheet(
                "QFrame#MacroCard {"
                "  background-color: #eff6ff;"
                "  border: 2px solid #2563eb;"
                "  border-radius: 12px;"
                "}"
            )
        else:
            self.setStyleSheet(
                "QFrame#MacroCard {"
                "  background-color: #ffffff;"
                "  border: 1px solid #e2e8f0;"
                "  border-radius: 12px;"
                "}"
                "QFrame#MacroCard:hover {"
                "  border: 1.5px solid #60a5fa;"
                "  background-color: #f8fafc;"
                "}"
            )

    def _on_check_changed(self, state: int):
        self._is_checked = (state == 2 or state == Qt.CheckState.Checked.value)
        self._update_card_style()
        self.card_checked_changed.emit(self.macro.name, self._is_checked)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_delete_mode:
                new_state = not self._is_checked
                self.set_checked(new_state)
                self.card_checked_changed.emit(self.macro.name, new_state)
            else:
                self.card_clicked.emit(self.macro.name)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._is_delete_mode:
            self.card_double_clicked.emit(self.macro.name)
        super().mouseDoubleClickEvent(event)


class MainScreen(QWidget):
    """ホーム画面。マクロの記録・実行・削除・一覧表示を統括する。"""

    start_record_requested = Signal()
    run_macro_requested = Signal()
    delete_macro_requested = Signal()
    delete_macros_requested = Signal(list)
    edit_macro_requested = Signal()

    def __init__(self, viewmodel: MainViewModel, parent=None) -> None:
        super().__init__(parent)
        self.viewmodel = viewmodel
        self.setObjectName("MacroManager")

        self._all_macros: list[MacroSummary] = []
        self._is_delete_mode: bool = False

        self._build_ui()
        self._bind_viewmodel()
        self.viewmodel.load_macros()

    def _font(self, size: int | float, bold: bool = False) -> QFont:
        font = QFont("Yu Gothic UI", size)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return font

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(32, 28, 32, 28)
        main_layout.setSpacing(16)

        page_title = TitleLabel("ホーム", self)
        page_title.setFont(self._font(22, bold=True))

        page_description = QLabel("保存されたマクロの実行や、新しい操作の記録を行えます。", self)
        page_description.setFont(self._font(10))
        page_description.setStyleSheet("color: #64748b;")

        main_layout.addWidget(page_title)
        main_layout.addWidget(page_description)
        main_layout.addSpacing(4)

        main_layout.addLayout(self._build_record_area())
        main_layout.addSpacing(10)
        main_layout.addLayout(self._build_action_bar())
        main_layout.addLayout(self._build_control_bar())

        self.list_widget = MacroListWidget(self)
        main_layout.addWidget(self.list_widget, 1)

        self._update_view_toggle_style()

    def _build_record_area(self) -> QHBoxLayout:
        record_area = QHBoxLayout()
        record_area.setSpacing(16)

        record_text_layout = QVBoxLayout()
        record_text_layout.setSpacing(4)

        record_title = SubtitleLabel("新しいマクロを作成", self)
        record_title.setFont(self._font(15, bold=True))

        record_description = QLabel(
            "マウスのクリックやキーボード入力を記録し、繰り返し使える自動マクロを生成します。",
            self,
        )
        record_description.setFont(self._font(10))
        record_description.setStyleSheet("color: #64748b;")

        record_text_layout.addWidget(record_title)
        record_text_layout.addWidget(record_description)

        self.btn_start_record = PrimaryPushButton("●  記録を開始", self)
        self.btn_start_record.setFont(self._font(11, bold=True))
        self.btn_start_record.setFixedSize(200, 46)

        record_area.addLayout(record_text_layout)
        record_area.addStretch(1)
        record_area.addWidget(self.btn_start_record, alignment=Qt.AlignmentFlag.AlignVCenter)
        return record_area

    def _build_action_bar(self) -> QHBoxLayout:
        table_top_bar = QHBoxLayout()
        table_top_bar.setSpacing(12)

        title_layout = QHBoxLayout()
        title_layout.setSpacing(8)
        macro_list_title = SubtitleLabel("マクロ一覧", self)
        macro_list_title.setFont(self._font(15, bold=True))

        self.lbl_count = QLabel("(0件)", self)
        self.lbl_count.setFont(self._font(11))
        self.lbl_count.setStyleSheet("color: #64748b;")

        title_layout.addWidget(macro_list_title)
        title_layout.addWidget(self.lbl_count)

        self.btn_edit_selected = PushButton("✎ 編集", self)
        self.btn_edit_selected.setFont(self._font(10))
        self.btn_edit_selected.setFixedSize(90, 36)
        self.btn_edit_selected.setEnabled(False)

        self.btn_delete_selected = PushButton("🗑 削除", self)
        self.btn_delete_selected.setFont(self._font(10))
        self.btn_delete_selected.setFixedSize(90, 36)
        self.btn_delete_selected.setEnabled(False)

        self.btn_run_selected = PrimaryPushButton("▶ 実行", self)
        self.btn_run_selected.setFont(self._font(10, bold=True))
        self.btn_run_selected.setFixedSize(110, 36)
        self.btn_run_selected.setEnabled(False)

        self.chk_select_all = CheckBox("すべて選択", self)
        self.chk_select_all.setFont(self._font(10))
        self.chk_select_all.setVisible(False)

        self.btn_cancel_delete = PushButton("キャンセル", self)
        self.btn_cancel_delete.setFont(self._font(10))
        self.btn_cancel_delete.setFixedSize(96, 36)
        self.btn_cancel_delete.setVisible(False)

        self.btn_confirm_delete = PrimaryPushButton("🗑 削除する (0件)", self)
        self.btn_confirm_delete.setFont(self._font(10, bold=True))
        self.btn_confirm_delete.setFixedSize(140, 36)
        self.btn_confirm_delete.setStyleSheet(
            "QPushButton { background-color: #ef4444; border-color: #ef4444; color: white; }"
            "QPushButton:hover { background-color: #dc2626; }"
        )
        self.btn_confirm_delete.setVisible(False)
        self.btn_confirm_delete.setEnabled(False)

        table_top_bar.addLayout(title_layout)
        table_top_bar.addStretch(1)
        table_top_bar.addWidget(self.btn_edit_selected)
        table_top_bar.addWidget(self.btn_delete_selected)
        table_top_bar.addWidget(self.btn_run_selected)
        table_top_bar.addWidget(self.chk_select_all)
        table_top_bar.addWidget(self.btn_cancel_delete)
        table_top_bar.addWidget(self.btn_confirm_delete)
        return table_top_bar

    def _build_control_bar(self) -> QHBoxLayout:
        control_bar = QHBoxLayout()
        control_bar.setSpacing(10)

        self.search_box = SearchLineEdit(self)
        self.search_box.setPlaceholderText("マクロ名で検索...")
        self.search_box.setFont(self._font(10))
        self.search_box.setFixedWidth(240)

        sort_label = QLabel("並び替え:", self)
        sort_label.setFont(self._font(10))
        sort_label.setStyleSheet("color: #475569;")

        self.sort_combo = ComboBox(self)
        self.sort_combo.setFont(self._font(10))
        self.sort_combo.setFixedWidth(160)
        self.sort_combo.addItems([
            "更新が新しい順",
            "更新が古い順",
            "作成が新しい順",
            "作成が古い順",
            "マクロ名順",
            "マクロ名順（降順）",
        ])

        self.btn_view_cards = PushButton("⊞ カード", self)
        self.btn_view_cards.setFont(self._font(9.5))
        self.btn_view_cards.setFixedSize(82, 34)
        self.btn_view_cards.clicked.connect(lambda: self._switch_view_mode("cards"))

        self.btn_view_table = PushButton("☰ リスト", self)
        self.btn_view_table.setFont(self._font(9.5))
        self.btn_view_table.setFixedSize(82, 34)
        self.btn_view_table.clicked.connect(lambda: self._switch_view_mode("table"))

        hint_label = QLabel("※ カードの直接実行やダブルクリックで即時起動できます", self)
        hint_label.setFont(self._font(9))
        hint_label.setStyleSheet("color: #94a3b8;")

        control_bar.addWidget(self.search_box)
        control_bar.addWidget(sort_label)
        control_bar.addWidget(self.sort_combo)
        control_bar.addSpacing(6)
        control_bar.addWidget(self.btn_view_cards)
        control_bar.addWidget(self.btn_view_table)
        control_bar.addSpacing(6)
        control_bar.addWidget(hint_label)
        control_bar.addStretch(1)
        return control_bar

    def _bind_viewmodel(self) -> None:
        self.btn_start_record.clicked.connect(self.start_record_requested.emit)
        self.btn_run_selected.clicked.connect(self.run_macro_requested.emit)
        self.btn_edit_selected.clicked.connect(self.edit_macro_requested.emit)

        self.btn_delete_selected.clicked.connect(self._enter_delete_mode)
        self.btn_cancel_delete.clicked.connect(self._exit_delete_mode)
        self.btn_confirm_delete.clicked.connect(self._on_confirm_batch_delete)
        self.chk_select_all.stateChanged.connect(self._on_select_all_changed)

        self.list_widget.macro_selected.connect(self._on_macro_selected)
        self.list_widget.macro_double_clicked.connect(self._on_macro_double_clicked)
        self.list_widget.macro_run_requested.connect(lambda _: self.run_macro_requested.emit())
        self.list_widget.macro_edit_requested.connect(lambda _: self.edit_macro_requested.emit())
        self.list_widget.checked_status_changed.connect(self._on_checked_status_changed)

        self.search_box.textChanged.connect(self._apply_filter_and_sort)
        self.sort_combo.currentTextChanged.connect(self._apply_filter_and_sort)

        self.viewmodel.macros_updated.connect(self._on_macros_loaded)
        self.viewmodel.can_run_changed.connect(self._update_control_buttons_state)

    def _switch_view_mode(self, mode: str) -> None:
        self.list_widget.switch_view_mode(mode)
        self._update_view_toggle_style()

    def _update_view_toggle_style(self) -> None:
        active_style = (
            "QPushButton { background-color: #2563eb; color: #ffffff; border: 1px solid #2563eb; "
            "border-radius: 6px; font-weight: bold; } "
            "QPushButton:hover { background-color: #1d4ed8; }"
        )
        inactive_style = (
            "QPushButton { background-color: #ffffff; color: #475569; border: 1px solid #cbd5e1; "
            "border-radius: 6px; } "
            "QPushButton:hover { background-color: #f1f5f9; }"
        )
        if self.list_widget.current_view_mode() == "cards":
            self.btn_view_cards.setStyleSheet(active_style)
            self.btn_view_table.setStyleSheet(inactive_style)
        else:
            self.btn_view_cards.setStyleSheet(inactive_style)
            self.btn_view_table.setStyleSheet(active_style)

    @Slot(list)
    def _on_macros_loaded(self, macros: list) -> None:
        self._all_macros = list(macros)
        if self._is_delete_mode:
            self._exit_delete_mode()
        self._apply_filter_and_sort()
        self.btn_delete_selected.setEnabled(len(self._all_macros) > 0)

    def _natural_sort_key(self, text: str) -> list:
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", str(text))]

    @Slot()
    def _apply_filter_and_sort(self) -> None:
        query = self.search_box.text().strip().lower()
        items = [m for m in self._all_macros if query in m.name.lower()]

        sort_type = self.sort_combo.currentText()
        if sort_type == "更新が新しい順":
            items.sort(key=lambda m: m.updated_timestamp, reverse=True)
        elif sort_type == "更新が古い順":
            items.sort(key=lambda m: m.updated_timestamp, reverse=False)
        elif sort_type == "作成が新しい順":
            items.sort(key=lambda m: m.created_timestamp, reverse=True)
        elif sort_type == "作成が古い順":
            items.sort(key=lambda m: m.created_timestamp, reverse=False)
        elif sort_type == "マクロ名順":
            items.sort(key=lambda m: self._natural_sort_key(m.name), reverse=False)
        elif sort_type == "マクロ名順（降順）":
            items.sort(key=lambda m: self._natural_sort_key(m.name), reverse=True)

        total_count = len(self._all_macros)
        if query:
            self.lbl_count.setText(f"({len(items)}件 / 全{total_count}件)")
        else:
            self.lbl_count.setText(f"({total_count}件)")

        self.list_widget.set_macros(items, query=query)

    @Slot(str)
    def _on_macro_selected(self, macro_name: str) -> None:
        self.viewmodel.select_macro(macro_name)
        has_selection = bool(macro_name)
        self.btn_run_selected.setEnabled(has_selection)
        self.btn_edit_selected.setEnabled(has_selection)

    @Slot(str)
    def _on_macro_double_clicked(self, macro_name: str) -> None:
        if not self._is_delete_mode and self.btn_run_selected.isEnabled():
            self.run_macro_requested.emit()

    @Slot()
    def _enter_delete_mode(self) -> None:
        if not self._all_macros:
            return
        self._is_delete_mode = True
        self.btn_edit_selected.setVisible(False)
        self.btn_delete_selected.setVisible(False)
        self.btn_run_selected.setVisible(False)

        self.chk_select_all.setVisible(True)
        self.btn_cancel_delete.setVisible(True)
        self.btn_confirm_delete.setVisible(True)
        self.list_widget.enter_delete_mode()

    @Slot()
    def _exit_delete_mode(self) -> None:
        self._is_delete_mode = False
        self.list_widget.exit_delete_mode()

        self.chk_select_all.setVisible(False)
        self.btn_cancel_delete.setVisible(False)
        self.btn_confirm_delete.setVisible(False)

        self.btn_edit_selected.setVisible(True)
        self.btn_delete_selected.setVisible(True)
        self.btn_run_selected.setVisible(True)

        self.chk_select_all.blockSignals(True)
        self.chk_select_all.setChecked(False)
        self.chk_select_all.blockSignals(False)

    @Slot(int)
    def _on_select_all_changed(self, state: int) -> None:
        is_checked = state == 2 or state == Qt.CheckState.Checked.value
        self.list_widget.set_all_checked(is_checked)

    @Slot(int, int)
    def _on_checked_status_changed(self, checked_count: int, total_count: int) -> None:
        self.btn_confirm_delete.setText(f"🗑 削除する ({checked_count}件)")
        self.btn_confirm_delete.setEnabled(checked_count > 0)

        self.chk_select_all.blockSignals(True)
        self.chk_select_all.setChecked(total_count > 0 and checked_count == total_count)
        self.chk_select_all.blockSignals(False)

    @Slot()
    def _on_confirm_batch_delete(self) -> None:
        checked = self.list_widget.get_checked_macro_names()
        if checked:
            self.delete_macros_requested.emit(checked)

    @Slot(bool)
    def _update_control_buttons_state(self, can_run: bool) -> None:
        self.btn_run_selected.setEnabled(can_run)
        self.btn_edit_selected.setEnabled(can_run)
        self.btn_delete_selected.setEnabled(len(self._all_macros) > 0)
__all__ = ["MainScreen", "MacroCardWidget"]
