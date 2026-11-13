"""Module: @role: ホーム画面のカードグリッド上で単一マクロの要約情報と操作ボタンを表示するカードウィジェット。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout
from qfluentwidgets import CheckBox, PrimaryPushButton, PushButton

from models.data_types import MacroSummary


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
    ) -> None:
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

    def _font(self, size: int | float, bold: bool = False) -> QFont:
        font = QFont("Yu Gothic UI", size)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return font

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

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

    def set_selected(self, selected: bool) -> None:
        if self._is_selected != selected:
            self._is_selected = selected
            self._update_card_style()

    def set_checked(self, checked: bool) -> None:
        self._is_checked = checked
        self.chk_box.blockSignals(True)
        self.chk_box.setChecked(checked)
        self.chk_box.blockSignals(False)
        self._update_card_style()

    def set_delete_mode(self, enabled: bool) -> None:
        self._is_delete_mode = enabled
        self.chk_box.setVisible(enabled)
        self.btn_edit.setVisible(not enabled)
        self.btn_run.setVisible(not enabled)
        self._update_card_style()

    def _update_card_style(self) -> None:
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

    def _on_check_changed(self, state: int) -> None:
        self._is_checked = state == 2 or state == Qt.CheckState.Checked.value
        self._update_card_style()
        self.card_checked_changed.emit(self.macro.name, self._is_checked)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_delete_mode:
                new_state = not self._is_checked
                self.set_checked(new_state)
                self.card_checked_changed.emit(self.macro.name, new_state)
            else:
                self.card_clicked.emit(self.macro.name)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._is_delete_mode:
            self.card_double_clicked.emit(self.macro.name)
        super().mouseDoubleClickEvent(event)
