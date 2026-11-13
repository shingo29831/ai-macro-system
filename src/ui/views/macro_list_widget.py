"""Module: @role: マクロ一覧のカードグリッド・テーブル表示切替、選択同期、および一括削除チェック状態を管理するビューウィジェット。"""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import TableWidget

from models.data_types import MacroSummary
from ui.views.macro_card_widget import MacroCardWidget


class MacroListWidget(QWidget):
    """カードグリッド・テーブル形式でのマクロ一覧表示および選択・チェック同期を担うウィジェット"""

    macro_selected = Signal(str)
    macro_double_clicked = Signal(str)
    macro_run_requested = Signal(str)
    macro_edit_requested = Signal(str)
    checked_status_changed = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._displayed_macros: list[MacroSummary] = []
        self._card_widgets: list[MacroCardWidget] = []
        self._current_selected_name: str = ""
        self._is_delete_mode: bool = False
        self._current_view_mode: str = "cards"
        self._last_grid_cols: int = 0

        self._build_ui()
        self._setup_table()
        self._bind_signals()

    def _font(self, size: int | float, bold: bool = False) -> QFont:
        font = QFont("Yu Gothic UI", size)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return font

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cards_scroll_area = QScrollArea(self)
        self.cards_scroll_area.setWidgetResizable(True)
        self.cards_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet("background: transparent;")
        self.cards_grid_layout = QGridLayout(self.cards_container)
        self.cards_grid_layout.setContentsMargins(0, 4, 0, 16)
        self.cards_grid_layout.setSpacing(14)
        self.cards_scroll_area.setWidget(self.cards_container)
        layout.addWidget(self.cards_scroll_area, 1)

        self.table_macros = TableWidget(self)
        self.table_macros.setColumnCount(5)
        self.table_macros.setHorizontalHeaderLabels([
            "選択",
            "マクロ名",
            "最終更新日時",
            "作成日時",
            "自己修復",
        ])
        self.table_macros.setFont(self._font(10))
        self.table_macros.horizontalHeader().setFont(self._font(10, bold=True))
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

    def _setup_table(self) -> None:
        self.table_macros.setShowGrid(False)
        self.table_macros.setAlternatingRowColors(True)
        self.table_macros.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_macros.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self.table_macros.verticalHeader().setVisible(False)
        self.table_macros.verticalHeader().setDefaultSectionSize(54)

        header = self.table_macros.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)

        self.table_macros.setColumnWidth(0, 56)
        self.table_macros.setColumnWidth(2, 170)
        self.table_macros.setColumnWidth(3, 170)
        self.table_macros.setColumnWidth(4, 110)
        self.table_macros.setColumnHidden(0, True)

    def _bind_signals(self) -> None:
        self.table_macros.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.table_macros.itemDoubleClicked.connect(self._on_table_double_clicked)
        self.table_macros.cellClicked.connect(self._on_cell_clicked)
        self.table_macros.itemChanged.connect(self._on_table_item_changed)

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
        self._render_table(macros)
        self._render_cards(macros)

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
        for card in self._card_widgets:
            card.set_selected(card.macro.name == macro_name)

        self.table_macros.blockSignals(True)
        selected_row = -1
        for row in range(self.table_macros.rowCount()):
            item = self.table_macros.item(row, 1)
            if item and item.text().strip() == macro_name:
                selected_row = row
                break
        if selected_row >= 0:
            self.table_macros.selectRow(selected_row)
        else:
            self.table_macros.clearSelection()
        self.table_macros.blockSignals(False)

        if emit_signal:
            self.macro_selected.emit(macro_name)

    def enter_delete_mode(self) -> None:
        self._is_delete_mode = True
        self.table_macros.setColumnHidden(0, False)
        for card in self._card_widgets:
            card.set_delete_mode(True)

        if self._current_selected_name:
            for card in self._card_widgets:
                if card.macro.name == self._current_selected_name:
                    card.set_checked(True)
                    break
            self.table_macros.blockSignals(True)
            for row in range(self.table_macros.rowCount()):
                name_item = self.table_macros.item(row, 1)
                if name_item and name_item.text().strip() == self._current_selected_name:
                    chk_item = self.table_macros.item(row, 0)
                    if chk_item:
                        chk_item.setCheckState(Qt.CheckState.Checked)
                    break
            self.table_macros.blockSignals(False)

        self._emit_checked_status()

    def exit_delete_mode(self) -> None:
        self._is_delete_mode = False
        self.table_macros.setColumnHidden(0, True)
        for card in self._card_widgets:
            card.set_delete_mode(False)
            card.set_checked(False)

        self.table_macros.blockSignals(True)
        for row in range(self.table_macros.rowCount()):
            chk_item = self.table_macros.item(row, 0)
            if chk_item:
                chk_item.setCheckState(Qt.CheckState.Unchecked)
        self.table_macros.blockSignals(False)

    def set_all_checked(self, checked: bool) -> None:
        target_state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.table_macros.blockSignals(True)
        for row in range(self.table_macros.rowCount()):
            chk_item = self.table_macros.item(row, 0)
            if chk_item:
                chk_item.setCheckState(target_state)
        self.table_macros.blockSignals(False)

        for card in self._card_widgets:
            card.set_checked(checked)

        self._emit_checked_status()

    def get_checked_macro_names(self) -> list[str]:
        if self._current_view_mode == "cards" and self._card_widgets:
            return [card.macro.name for card in self._card_widgets if card._is_checked]
        checked = []
        for row in range(self.table_macros.rowCount()):
            chk = self.table_macros.item(row, 0)
            name_item = self.table_macros.item(row, 1)
            if chk and chk.checkState() == Qt.CheckState.Checked and name_item:
                checked.append(name_item.text().strip())
        return checked

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._current_view_mode == "cards" and self.cards_scroll_area.isVisible():
            viewport_width = self.cards_scroll_area.viewport().width()
            cols = max(1, min(4, viewport_width // 300))
            if cols != self._last_grid_cols:
                self.relayout_cards()

    def relayout_cards(self) -> None:
        if not self._card_widgets:
            return

        viewport_width = self.cards_scroll_area.viewport().width()
        cols = max(1, min(4, viewport_width // 300))
        self._last_grid_cols = cols

        while self.cards_grid_layout.count():
            self.cards_grid_layout.takeAt(0)

        for idx, card in enumerate(self._card_widgets):
            self.cards_grid_layout.addWidget(card, idx // cols, idx % cols)

        for col in range(cols):
            self.cards_grid_layout.setColumnStretch(col, 1)

    def _render_cards(self, macros: list[MacroSummary]) -> None:
        for card in self._card_widgets:
            card.deleteLater()
        self._card_widgets.clear()

        checked_names = set(self.get_checked_macro_names())
        for macro in macros:
            card = MacroCardWidget(
                macro=macro,
                is_delete_mode=self._is_delete_mode,
                is_selected=(macro.name == self._current_selected_name),
                is_checked=(macro.name in checked_names),
                parent=self.cards_container,
            )
            card.card_clicked.connect(lambda name: self.select_macro(name, emit_signal=True))
            card.card_double_clicked.connect(self._on_card_double_clicked)
            card.card_checked_changed.connect(self._on_card_checked_changed)
            card.run_requested.connect(self._on_card_run_clicked)
            card.edit_requested.connect(self._on_card_edit_clicked)
            self._card_widgets.append(card)

        self.relayout_cards()

    def _render_table(self, macros: list[MacroSummary]) -> None:
        self.table_macros.blockSignals(True)
        self.table_macros.clearContents()
        self.table_macros.setRowCount(len(macros))

        item_font = self._font(10)
        selected_row = -1

        for row, macro in enumerate(macros):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Unchecked)
            chk_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_macros.setItem(row, 0, chk_item)

            name_item = QTableWidgetItem(f"  {macro.name}")
            name_item.setFont(self._font(10.5, bold=True))
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_macros.setItem(row, 1, name_item)

            updated_item = QTableWidgetItem(macro.updated_at)
            updated_item.setFont(item_font)
            updated_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            updated_item.setFlags(updated_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_macros.setItem(row, 2, updated_item)

            created_item = QTableWidgetItem(macro.created_at)
            created_item.setFont(item_font)
            created_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            created_item.setFlags(created_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_macros.setItem(row, 3, created_item)

            heals_item = QTableWidgetItem(macro.heals)
            heals_item.setFont(item_font)
            heals_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            heals_item.setFlags(heals_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_macros.setItem(row, 4, heals_item)

            if macro.name == self._current_selected_name:
                selected_row = row

        self.table_macros.blockSignals(False)

        if selected_row >= 0:
            self.table_macros.selectRow(selected_row)
        elif not self._current_selected_name:
            self.macro_selected.emit("")

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
        self.table_macros.blockSignals(True)
        for row in range(self.table_macros.rowCount()):
            name_item = self.table_macros.item(row, 1)
            if name_item and name_item.text().strip() == macro_name:
                chk_item = self.table_macros.item(row, 0)
                if chk_item:
                    chk_item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                break
        self.table_macros.blockSignals(False)
        self._emit_checked_status()

    @Slot(int, int)
    def _on_cell_clicked(self, row: int, column: int) -> None:
        if not self._is_delete_mode or column == 0:
            return

        chk_item = self.table_macros.item(row, 0)
        name_item = self.table_macros.item(row, 1)
        if chk_item and name_item:
            new_state = chk_item.checkState() != Qt.CheckState.Checked
            chk_item.setCheckState(Qt.CheckState.Checked if new_state else Qt.CheckState.Unchecked)
            name = name_item.text().strip()
            for card in self._card_widgets:
                if card.macro.name == name:
                    card.set_checked(new_state)
                    break
        self._emit_checked_status()

    @Slot(QTableWidgetItem)
    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if not self._is_delete_mode or item.column() != 0:
            return
        name_item = self.table_macros.item(item.row(), 1)
        if name_item:
            name = name_item.text().strip()
            is_chk = item.checkState() == Qt.CheckState.Checked
            for card in self._card_widgets:
                if card.macro.name == name:
                    card.set_checked(is_chk)
                    break
        self._emit_checked_status()

    @Slot()
    def _on_table_selection_changed(self) -> None:
        selected_items = self.table_macros.selectedItems()
        if not selected_items:
            return
        name_item = self.table_macros.item(selected_items[0].row(), 1)
        if name_item:
            self.select_macro(name_item.text().strip(), emit_signal=True)

    @Slot(QTableWidgetItem)
    def _on_table_double_clicked(self, item: QTableWidgetItem) -> None:
        if not self._is_delete_mode and item:
            self.macro_double_clicked.emit(self._current_selected_name)
