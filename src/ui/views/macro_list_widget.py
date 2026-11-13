"""Module: @role: カードグリッドビューとテーブルビューの表示切替、選択状態同期、および一括削除チェック連携を統括するコーディネーター。"""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from models.data_types import MacroSummary
from ui.views.macro_card_grid_view import MacroCardGridView
from ui.views.macro_card_widget import MacroCardWidget
from ui.views.macro_table_view import MacroTableView


class MacroListWidget(QWidget):
    """カードグリッド・テーブル両ビューの切替および選択・チェック状態同期を担う統合ウィジェット"""

    macro_selected = Signal(str)
    macro_double_clicked = Signal(str)
    macro_run_requested = Signal(str)
    macro_edit_requested = Signal(str)
    checked_status_changed = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._displayed_macros: list[MacroSummary] = []
        self._current_selected_name: str = ""
        self._is_delete_mode: bool = False
        self._current_view_mode: str = "cards"

        self._build_ui()
        self._bind_signals()

    @property
    def _card_widgets(self) -> list[MacroCardWidget]:
        return self.cards_scroll_area.card_widgets

    def _font(self, size: int | float, bold: bool = False) -> QFont:
        font = QFont("Yu Gothic UI", size)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return font

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cards_scroll_area = MacroCardGridView(self)
        layout.addWidget(self.cards_scroll_area, 1)

        self.table_macros = MacroTableView(self)
        self.table_macros.setVisible(False)
        layout.addWidget(self.table_macros, 1)

        self.empty_state_label = QLabel(
            "保存されているマクロがありません。\n「● 記録を開始」ボタンを押して最初のマクロを作成しましょう。",
            self,
        )
        self.empty_state_label.setFont(self._font(11))
        self.empty_state_label.setStyleSheet("color: #64748b; line-height: 1.6;")
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_label.setVisible(False)
        layout.addWidget(self.empty_state_label, 1)

    def _bind_signals(self) -> None:
        self.cards_scroll_area.card_clicked.connect(lambda name: self.select_macro(name, emit_signal=True))
        self.cards_scroll_area.card_double_clicked.connect(self._on_card_double_clicked)
        self.cards_scroll_area.card_checked_changed.connect(self._on_card_checked_changed)
        self.cards_scroll_area.run_requested.connect(self._on_card_run_clicked)
        self.cards_scroll_area.edit_requested.connect(self._on_card_edit_clicked)

        self.table_macros.row_selected.connect(lambda name: self.select_macro(name, emit_signal=True))
        self.table_macros.row_double_clicked.connect(self._on_table_double_clicked)
        self.table_macros.row_checked_changed.connect(self._on_table_checked_changed)

    def current_view_mode(self) -> str:
        return self._current_view_mode

    def switch_view_mode(self, mode: str) -> None:
        if self._current_view_mode == mode:
            return
        self._current_view_mode = mode
        self._update_visibility()
        if mode == "cards" and self._displayed_macros:
            self.relayout_cards()

    def set_macros(self, macros: list[MacroSummary], query: str = "") -> None:
        self._displayed_macros = list(macros)
        if not macros:
            if query:
                self.empty_state_label.setText(f"「{query}」に一致するマクロは見つかりませんでした。")
            else:
                self.empty_state_label.setText(
                    "保存されているマクロがありません。\n「● 記録を開始」ボタンを押して操作を記録しましょう。"
                )
        self._update_visibility()

        checked_names = set(self.get_checked_macro_names())
        selected_restored = self.table_macros.set_macros(macros, self._current_selected_name)
        if not selected_restored and not self._current_selected_name:
            self.macro_selected.emit("")

        self.cards_scroll_area.set_macros(
            macros=macros,
            is_delete_mode=self._is_delete_mode,
            selected_name=self._current_selected_name,
            checked_names=checked_names,
        )

    def _update_visibility(self) -> None:
        if not self._displayed_macros:
            self.table_macros.setVisible(False)
            self.cards_scroll_area.setVisible(False)
            self.empty_state_label.setVisible(True)
            return

        self.empty_state_label.setVisible(False)
        if self._current_view_mode == "cards":
            self.cards_scroll_area.setVisible(True)
            self.table_macros.setVisible(False)
        else:
            self.cards_scroll_area.setVisible(False)
            self.table_macros.setVisible(True)

    def select_macro(self, macro_name: str, emit_signal: bool = False) -> None:
        self._current_selected_name = macro_name
        self.cards_scroll_area.select_macro(macro_name)
        self.table_macros.select_macro(macro_name)
        if emit_signal:
            self.macro_selected.emit(macro_name)

    def enter_delete_mode(self) -> None:
        self._is_delete_mode = True
        self.table_macros.set_delete_mode(True, self._current_selected_name)
        self.cards_scroll_area.set_delete_mode(True, self._current_selected_name)
        self._emit_checked_status()

    def exit_delete_mode(self) -> None:
        self._is_delete_mode = False
        self.table_macros.set_delete_mode(False)
        self.cards_scroll_area.set_delete_mode(False)

    def set_all_checked(self, checked: bool) -> None:
        self.table_macros.set_all_checked(checked)
        self.cards_scroll_area.set_all_checked(checked)
        self._emit_checked_status()

    def get_checked_macro_names(self) -> list[str]:
        if self._current_view_mode == "cards" and self.cards_scroll_area.card_widgets:
            return self.cards_scroll_area.get_checked_macro_names()
        return self.table_macros.get_checked_macro_names()

    def relayout_cards(self) -> None:
        self.cards_scroll_area.relayout_cards()

    def _emit_checked_status(self) -> None:
        checked_count = len(self.get_checked_macro_names())
        self.checked_status_changed.emit(checked_count, len(self._displayed_macros))

    @Slot(str)
    def _on_card_double_clicked(self, macro_name: str) -> None:
        if not self._is_delete_mode:
            self.macro_double_clicked.emit(macro_name)

    @Slot(str)
    def _on_card_run_clicked(self, macro_name: str) -> None:
        self.select_macro(macro_name, emit_signal=True)
        self.macro_run_requested.emit(macro_name)

    @Slot(str)
    def _on_card_edit_clicked(self, macro_name: str) -> None:
        self.select_macro(macro_name, emit_signal=True)
        self.macro_edit_requested.emit(macro_name)

    @Slot(str, bool)
    def _on_card_checked_changed(self, macro_name: str, checked: bool) -> None:
        self.table_macros.set_macro_checked(macro_name, checked)
        self._emit_checked_status()

    @Slot(str, bool)
    def _on_table_checked_changed(self, macro_name: str, checked: bool) -> None:
        self.cards_scroll_area.set_macro_checked(macro_name, checked)
        self._emit_checked_status()

    @Slot()
    def _on_table_double_clicked(self) -> None:
        if not self._is_delete_mode:
            self.macro_double_clicked.emit(self._current_selected_name)
