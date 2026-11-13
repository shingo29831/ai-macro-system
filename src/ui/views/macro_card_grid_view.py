"""Module: @role: マクロ一覧のカードグリッド表示・動的リレイアウト・選択およびチェック状態管理を担当するビュー。"""

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QScrollArea, QWidget

from models.data_types import MacroSummary
from ui.views.macro_card_widget import MacroCardWidget


class MacroCardGridView(QScrollArea):
    """カードグリッド形式でマクロ一覧を表示・操作するスクロールビュー"""

    card_clicked = Signal(str)
    card_double_clicked = Signal(str)
    card_checked_changed = Signal(str, bool)
    run_requested = Signal(str)
    edit_requested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._card_widgets: list[MacroCardWidget] = []
        self._last_grid_cols: int = 0

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._cards_container = QWidget()
        self._cards_container.setStyleSheet("background: transparent;")
        self._cards_grid_layout = QGridLayout(self._cards_container)
        self._cards_grid_layout.setContentsMargins(0, 4, 0, 16)
        self._cards_grid_layout.setSpacing(14)
        self.setWidget(self._cards_container)

    @property
    def card_widgets(self) -> list[MacroCardWidget]:
        return self._card_widgets

    def set_macros(
        self,
        macros: list[MacroSummary],
        is_delete_mode: bool,
        selected_name: str,
        checked_names: set[str],
    ) -> None:
        for card in self._card_widgets:
            card.deleteLater()
        self._card_widgets.clear()

        for macro in macros:
            card = MacroCardWidget(
                macro=macro,
                is_delete_mode=is_delete_mode,
                is_selected=(macro.name == selected_name),
                is_checked=(macro.name in checked_names),
                parent=self._cards_container,
            )
            card.card_clicked.connect(self.card_clicked)
            card.card_double_clicked.connect(self.card_double_clicked)
            card.card_checked_changed.connect(self.card_checked_changed)
            card.run_requested.connect(self.run_requested)
            card.edit_requested.connect(self.edit_requested)
            self._card_widgets.append(card)

        self.relayout_cards()

    def select_macro(self, macro_name: str) -> None:
        for card in self._card_widgets:
            card.set_selected(card.macro.name == macro_name)

    def set_delete_mode(self, enabled: bool, selected_name_to_check: str = "") -> None:
        for card in self._card_widgets:
            card.set_delete_mode(enabled)
            if enabled:
                if selected_name_to_check and card.macro.name == selected_name_to_check:
                    card.set_checked(True)
            else:
                card.set_checked(False)

    def set_all_checked(self, checked: bool) -> None:
        for card in self._card_widgets:
            card.set_checked(checked)

    def set_macro_checked(self, macro_name: str, checked: bool) -> None:
        for card in self._card_widgets:
            if card.macro.name == macro_name:
                card.set_checked(checked)
                break

    def get_checked_macro_names(self) -> list[str]:
        return [card.macro.name for card in self._card_widgets if card._is_checked]

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.isVisible():
            viewport_width = self.viewport().width()
            cols = max(1, min(4, viewport_width // 300))
            if cols != self._last_grid_cols:
                self.relayout_cards()

    def relayout_cards(self) -> None:
        if not self._card_widgets:
            return

        viewport_width = self.viewport().width()
        cols = max(1, min(4, viewport_width // 300))
        self._last_grid_cols = cols

        while self._cards_grid_layout.count():
            self._cards_grid_layout.takeAt(0)

        for idx, card in enumerate(self._card_widgets):
            self._cards_grid_layout.addWidget(card, idx // cols, idx % cols)

        for col in range(cols):
            self._cards_grid_layout.setColumnStretch(col, 1)
