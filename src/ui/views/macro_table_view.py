"""Module: @role: マクロ一覧のテーブル形式表示・行選択・チェック状態管理を担当するビュー。"""

from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidgetItem, QWidget
from qfluentwidgets import TableWidget

from models.data_types import MacroSummary


def _make_font(size: int | float, bold: bool = False) -> QFont:
    font = QFont("Yu Gothic UI", size)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    return font


class MacroTableView(TableWidget):
    """テーブル形式でマクロ一覧を表示・操作するビューウィジェット"""

    row_selected = Signal(str)
    row_double_clicked = Signal()
    row_checked_changed = Signal(str, bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._is_delete_mode: bool = False
        self._setup_table()
        self._bind_signals()

    def _setup_table(self) -> None:
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels([
            "選択",
            "マクロ名",
            "最終更新日時",
            "作成日時",
            "自己修復",
        ])
        self.setFont(_make_font(10))
        self.horizontalHeader().setFont(_make_font(10, bold=True))

        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(54)

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)

        self.setColumnWidth(0, 56)
        self.setColumnWidth(2, 170)
        self.setColumnWidth(3, 170)
        self.setColumnWidth(4, 110)
        self.setColumnHidden(0, True)

    def _bind_signals(self) -> None:
        self.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.itemDoubleClicked.connect(self._on_table_double_clicked)
        self.cellClicked.connect(self._on_cell_clicked)
        self.itemChanged.connect(self._on_table_item_changed)

    def set_macros(self, macros: list[MacroSummary], selected_name: str) -> bool:
        """テーブル行を描画し、既存選択行を復元できた場合はTrueを返す。"""
        self.blockSignals(True)
        self.clearContents()
        self.setRowCount(len(macros))

        item_font = _make_font(10)
        selected_row = -1

        for row, macro in enumerate(macros):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Unchecked)
            chk_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(row, 0, chk_item)

            name_item = QTableWidgetItem(f"  {macro.name}")
            name_item.setFont(_make_font(10.5, bold=True))
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.setItem(row, 1, name_item)

            updated_item = QTableWidgetItem(macro.updated_at)
            updated_item.setFont(item_font)
            updated_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            updated_item.setFlags(updated_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.setItem(row, 2, updated_item)

            created_item = QTableWidgetItem(macro.created_at)
            created_item.setFont(item_font)
            created_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            created_item.setFlags(created_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.setItem(row, 3, created_item)

            heals_item = QTableWidgetItem(macro.heals)
            heals_item.setFont(item_font)
            heals_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            heals_item.setFlags(heals_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.setItem(row, 4, heals_item)

            if macro.name == selected_name:
                selected_row = row

        self.blockSignals(False)

        if selected_row >= 0:
            self.selectRow(selected_row)
            return True
        return False

    def select_macro(self, macro_name: str) -> None:
        self.blockSignals(True)
        selected_row = -1
        for row in range(self.rowCount()):
            item = self.item(row, 1)
            if item and item.text().strip() == macro_name:
                selected_row = row
                break
        if selected_row >= 0:
            self.selectRow(selected_row)
        else:
            self.clearSelection()
        self.blockSignals(False)

    def set_delete_mode(self, enabled: bool, selected_name_to_check: str = "") -> None:
        self._is_delete_mode = enabled
        self.setColumnHidden(0, not enabled)
        self.blockSignals(True)
        for row in range(self.rowCount()):
            chk_item = self.item(row, 0)
            if not chk_item:
                continue
            if enabled:
                name_item = self.item(row, 1)
                if selected_name_to_check and name_item and name_item.text().strip() == selected_name_to_check:
                    chk_item.setCheckState(Qt.CheckState.Checked)
            else:
                chk_item.setCheckState(Qt.CheckState.Unchecked)
        self.blockSignals(False)

    def set_all_checked(self, checked: bool) -> None:
        target_state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.blockSignals(True)
        for row in range(self.rowCount()):
            chk_item = self.item(row, 0)
            if chk_item:
                chk_item.setCheckState(target_state)
        self.blockSignals(False)

    def set_macro_checked(self, macro_name: str, checked: bool) -> None:
        self.blockSignals(True)
        for row in range(self.rowCount()):
            name_item = self.item(row, 1)
            if name_item and name_item.text().strip() == macro_name:
                chk_item = self.item(row, 0)
                if chk_item:
                    chk_item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                break
        self.blockSignals(False)

    def get_checked_macro_names(self) -> list[str]:
        checked: list[str] = []
        for row in range(self.rowCount()):
            chk = self.item(row, 0)
            name_item = self.item(row, 1)
            if chk and chk.checkState() == Qt.CheckState.Checked and name_item:
                checked.append(name_item.text().strip())
        return checked

    @Slot(int, int)
    def _on_cell_clicked(self, row: int, column: int) -> None:
        if not self._is_delete_mode or column == 0:
            return
        chk_item = self.item(row, 0)
        name_item = self.item(row, 1)
        if chk_item and name_item:
            new_state = chk_item.checkState() != Qt.CheckState.Checked
            chk_item.setCheckState(Qt.CheckState.Checked if new_state else Qt.CheckState.Unchecked)
            self.row_checked_changed.emit(name_item.text().strip(), new_state)

    @Slot(QTableWidgetItem)
    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if not self._is_delete_mode or item.column() != 0:
            return
        name_item = self.item(item.row(), 1)
        if name_item:
            is_chk = item.checkState() == Qt.CheckState.Checked
            self.row_checked_changed.emit(name_item.text().strip(), is_chk)

    @Slot()
    def _on_table_selection_changed(self) -> None:
        selected_items = self.selectedItems()
        if not selected_items:
            return
        name_item = self.item(selected_items[0].row(), 1)
        if name_item:
            self.row_selected.emit(name_item.text().strip())

    @Slot(QTableWidgetItem)
    def _on_table_double_clicked(self, item: QTableWidgetItem) -> None:
        if not self._is_delete_mode and item:
            self.row_double_clicked.emit()
