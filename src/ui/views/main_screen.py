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
    """ホーム画面。マクロの記録・実行・削除・一覧表示を担当する。"""

    start_record_requested = Signal()
    run_macro_requested = Signal()
    delete_macro_requested = Signal()
    delete_macros_requested = Signal(list)
    edit_macro_requested = Signal()

    def __init__(self, viewmodel: MainViewModel, parent=None):
        super().__init__(parent)

        self.viewmodel = viewmodel
        self.setObjectName("MacroManager")

        self._all_macros: list[MacroSummary] = []
        self._displayed_macros: list[MacroSummary] = []
        self._card_widgets: list[MacroCardWidget] = []
        self._current_selected_name: str = ""
        self._is_delete_mode: bool = False
        self._current_view_mode: str = "cards"
        self._last_grid_cols: int = 0

        self.btn_start_record = None
        self.btn_run_selected = None
        self.btn_delete_selected = None
        self.btn_edit_selected = None

        self.chk_select_all = None
        self.btn_cancel_delete = None
        self.btn_confirm_delete = None

        self.btn_view_cards = None
        self.btn_view_table = None

        self.cards_scroll_area = None
        self.cards_container = None
        self.cards_grid_layout = None

        self.table_macros = None
        self.search_box = None
        self.sort_combo = None
        self.lbl_count = None
        self.empty_state_label = None

        self._build_ui()
        self._setup_table()
        self._bind_viewmodel()

        self.viewmodel.load_macros()

    def _font(self, size: int, bold: bool = False) -> QFont:
        font = QFont("Yu Gothic UI", size)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return font

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(32, 28, 32, 28)
        main_layout.setSpacing(16)

        # ページヘッダー
        page_title = TitleLabel("ホーム", self)
        page_title.setFont(self._font(22, bold=True))

        page_description = QLabel("保存されたマクロの実行や、新しい操作の記録を行えます。", self)
        page_description.setFont(self._font(10))
        page_description.setStyleSheet("color: #64748b;")

        main_layout.addWidget(page_title)
        main_layout.addWidget(page_description)
        main_layout.addSpacing(4)

        # 録画エリア
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

        main_layout.addLayout(record_area)
        main_layout.addSpacing(10)

        # マクロ一覧タイトル＆アクションボタンバー
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

        # 通常操作ボタン
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

        # 一括削除モード用コントロール
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

        main_layout.addLayout(table_top_bar)

        # 検索・並び替え・表示形式切替コントロールバー
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

        # ビュー切替トグル
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

        main_layout.addLayout(control_bar)

        # 1. カードグリッドエリア (デフォルト)
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

        main_layout.addWidget(self.cards_scroll_area, 1)

        # 2. テーブルエリア
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

        main_layout.addWidget(self.table_macros, 1)

        # 空状態ラベル
        self.empty_state_label = QLabel(
            "保存されているマクロがありません。\n「● 記録を開始」ボタンを押して最初のマクロを作成しましょう。",
            self,
        )
        self.empty_state_label.setFont(self._font(11))
        self.empty_state_label.setStyleSheet("color: #64748b; line-height: 1.6;")
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_label.setVisible(False)
        main_layout.addWidget(self.empty_state_label)

        self._update_view_toggle_style()

    def _setup_table(self):
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

    def _bind_viewmodel(self):
        self.btn_start_record.clicked.connect(self.start_record_requested.emit)
        self.btn_run_selected.clicked.connect(self.run_macro_requested.emit)
        self.btn_edit_selected.clicked.connect(self.edit_macro_requested.emit)

        self.btn_delete_selected.clicked.connect(self._enter_delete_mode)
        self.btn_cancel_delete.clicked.connect(self._exit_delete_mode)
        self.btn_confirm_delete.clicked.connect(self._on_confirm_batch_delete)
        self.chk_select_all.stateChanged.connect(self._on_select_all_changed)

        self.table_macros.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.table_macros.itemDoubleClicked.connect(self._on_table_double_clicked)
        self.table_macros.cellClicked.connect(self._on_cell_clicked)
        self.table_macros.itemChanged.connect(self._on_table_item_changed)

        self.search_box.textChanged.connect(self._apply_filter_and_sort)
        self.sort_combo.currentTextChanged.connect(self._apply_filter_and_sort)

        self.viewmodel.macros_updated.connect(self._on_macros_loaded)
        self.viewmodel.can_run_changed.connect(self._update_control_buttons_state)

    def _switch_view_mode(self, mode: str):
        if self._current_view_mode == mode:
            return
        self._current_view_mode = mode
        self._update_view_toggle_style()

        if not self._displayed_macros:
            self.cards_scroll_area.setVisible(False)
            self.table_macros.setVisible(False)
            self.empty_state_label.setVisible(True)
            return

        self.empty_state_label.setVisible(False)
        if mode == "cards":
            self.cards_scroll_area.setVisible(True)
            self.table_macros.setVisible(False)
            self._relayout_cards()
        else:
            self.cards_scroll_area.setVisible(False)
            self.table_macros.setVisible(True)

    def _update_view_toggle_style(self):
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
        if self._current_view_mode == "cards":
            self.btn_view_cards.setStyleSheet(active_style)
            self.btn_view_table.setStyleSheet(inactive_style)
        else:
            self.btn_view_cards.setStyleSheet(inactive_style)
            self.btn_view_table.setStyleSheet(active_style)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._current_view_mode == "cards" and self.cards_scroll_area.isVisible():
            viewport_width = self.cards_scroll_area.viewport().width()
            cols = max(1, min(4, viewport_width // 300))
            if cols != self._last_grid_cols:
                self._relayout_cards()

    @Slot(list)
    def _on_macros_loaded(self, macros: list):
        self._all_macros = list(macros)
        if self._is_delete_mode:
            self._exit_delete_mode()
        self._apply_filter_and_sort()
        self.btn_delete_selected.setEnabled(len(self._all_macros) > 0)

    def _natural_sort_key(self, text: str):
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', str(text))]

    @Slot()
    def _apply_filter_and_sort(self):
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

        self._displayed_macros = items

        total_count = len(self._all_macros)
        filtered_count = len(items)
        if query:
            self.lbl_count.setText(f"({filtered_count}件 / 全{total_count}件)")
        else:
            self.lbl_count.setText(f"({total_count}件)")

        if not items:
            self.table_macros.setVisible(False)
            self.cards_scroll_area.setVisible(False)
            self.empty_state_label.setVisible(True)
            if query:
                self.empty_state_label.setText(f"「{query}」に一致するマクロは見つかりませんでした。")
            else:
                self.empty_state_label.setText(
                    "保存されているマクロがありません。\n「● 記録を開始」ボタンを押して操作を記録しましょう。"
                )
        else:
            self.empty_state_label.setVisible(False)
            if self._current_view_mode == "cards":
                self.cards_scroll_area.setVisible(True)
                self.table_macros.setVisible(False)
            else:
                self.cards_scroll_area.setVisible(False)
                self.table_macros.setVisible(True)

        self._render_table(items)
        self._render_cards(items)

    def _render_cards(self, macros: list[MacroSummary]):
        for card in self._card_widgets:
            card.deleteLater()
        self._card_widgets.clear()

        checked_names = set(self._get_checked_macro_names())

        for macro in macros:
            is_selected = (macro.name == self._current_selected_name)
            is_checked = (macro.name in checked_names)
            card = MacroCardWidget(
                macro=macro,
                is_delete_mode=self._is_delete_mode,
                is_selected=is_selected,
                is_checked=is_checked,
                parent=self.cards_container,
            )
            card.card_clicked.connect(self._select_macro)
            card.card_double_clicked.connect(self._on_card_double_clicked)
            card.card_checked_changed.connect(self._on_card_checked_changed)
            card.run_requested.connect(self._on_card_run_clicked)
            card.edit_requested.connect(self._on_card_edit_clicked)

            self._card_widgets.append(card)

        self._relayout_cards()

    def _relayout_cards(self):
        if not self._card_widgets:
            return

        viewport_width = self.cards_scroll_area.viewport().width()
        cols = max(1, min(4, viewport_width // 300))
        self._last_grid_cols = cols

        while self.cards_grid_layout.count():
            self.cards_grid_layout.takeAt(0)

        for idx, card in enumerate(self._card_widgets):
            row = idx // cols
            col = idx % cols
            self.cards_grid_layout.addWidget(card, row, col)

        for c in range(cols):
            self.cards_grid_layout.setColumnStretch(c, 1)

    def _render_table(self, macros: list[MacroSummary]):
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
            self.viewmodel.select_macro("")

    def _select_macro(self, macro_name: str):
        self._current_selected_name = macro_name
        self.viewmodel.select_macro(macro_name)

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

        has_selection = bool(macro_name)
        self.btn_run_selected.setEnabled(has_selection)
        self.btn_edit_selected.setEnabled(has_selection)

    @Slot(str)
    def _on_card_double_clicked(self, macro_name: str):
        if not self._is_delete_mode and self.btn_run_selected.isEnabled():
            self.run_macro_requested.emit()

    @Slot(str)
    def _on_card_run_clicked(self, macro_name: str):
        self._select_macro(macro_name)
        self.run_macro_requested.emit()

    @Slot(str)
    def _on_card_edit_clicked(self, macro_name: str):
        self._select_macro(macro_name)
        self.edit_macro_requested.emit()

    @Slot(str, bool)
    def _on_card_checked_changed(self, macro_name: str, checked: bool):
        self.table_macros.blockSignals(True)
        for row in range(self.table_macros.rowCount()):
            name_item = self.table_macros.item(row, 1)
            if name_item and name_item.text().strip() == macro_name:
                chk_item = self.table_macros.item(row, 0)
                if chk_item:
                    chk_item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                break
        self.table_macros.blockSignals(False)
        self._update_batch_delete_status()

    @Slot()
    def _enter_delete_mode(self):
        if not self._all_macros:
            return

        self._is_delete_mode = True
        self.table_macros.setColumnHidden(0, False)
        for card in self._card_widgets:
            card.set_delete_mode(True)

        self.btn_edit_selected.setVisible(False)
        self.btn_delete_selected.setVisible(False)
        self.btn_run_selected.setVisible(False)

        self.chk_select_all.setVisible(True)
        self.btn_cancel_delete.setVisible(True)
        self.btn_confirm_delete.setVisible(True)

        if self._current_selected_name:
            for card in self._card_widgets:
                if card.macro.name == self._current_selected_name:
                    card.set_checked(True)
                    break
            for row in range(self.table_macros.rowCount()):
                name_item = self.table_macros.item(row, 1)
                if name_item and name_item.text().strip() == self._current_selected_name:
                    chk_item = self.table_macros.item(row, 0)
                    if chk_item:
                        chk_item.setCheckState(Qt.CheckState.Checked)
                    break

        self._update_batch_delete_status()

    @Slot()
    def _exit_delete_mode(self):
        self._is_delete_mode = False
        self.table_macros.setColumnHidden(0, True)
        for card in self._card_widgets:
            card.set_delete_mode(False)
            card.set_checked(False)

        self.chk_select_all.setVisible(False)
        self.btn_cancel_delete.setVisible(False)
        self.btn_confirm_delete.setVisible(False)

        self.btn_edit_selected.setVisible(True)
        self.btn_delete_selected.setVisible(True)
        self.btn_run_selected.setVisible(True)

        self.table_macros.blockSignals(True)
        for row in range(self.table_macros.rowCount()):
            chk_item = self.table_macros.item(row, 0)
            if chk_item:
                chk_item.setCheckState(Qt.CheckState.Unchecked)
        self.table_macros.blockSignals(False)

        self.chk_select_all.blockSignals(True)
        self.chk_select_all.setChecked(False)
        self.chk_select_all.blockSignals(False)

    @Slot(int)
    def _on_select_all_changed(self, state: int):
        target_state = Qt.CheckState.Checked if (state == 2 or state == Qt.CheckState.Checked.value) else Qt.CheckState.Unchecked
        is_checked = (target_state == Qt.CheckState.Checked)

        self.table_macros.blockSignals(True)
        for row in range(self.table_macros.rowCount()):
            chk_item = self.table_macros.item(row, 0)
            if chk_item:
                chk_item.setCheckState(target_state)
        self.table_macros.blockSignals(False)

        for card in self._card_widgets:
            card.set_checked(is_checked)

        self._update_batch_delete_status()

    @Slot(int, int)
    def _on_cell_clicked(self, row: int, column: int):
        if not self._is_delete_mode or column == 0:
            return

        chk_item = self.table_macros.item(row, 0)
        name_item = self.table_macros.item(row, 1)
        if chk_item and name_item:
            current = chk_item.checkState()
            new_state = (current != Qt.CheckState.Checked)
            chk_item.setCheckState(Qt.CheckState.Checked if new_state else Qt.CheckState.Unchecked)
            name = name_item.text().strip()
            for card in self._card_widgets:
                if card.macro.name == name:
                    card.set_checked(new_state)
                    break
        self._update_batch_delete_status()

    @Slot(QTableWidgetItem)
    def _on_table_item_changed(self, item: QTableWidgetItem):
        if not self._is_delete_mode or item.column() != 0:
            return
        row = item.row()
        name_item = self.table_macros.item(row, 1)
        if name_item:
            name = name_item.text().strip()
            is_chk = (item.checkState() == Qt.CheckState.Checked)
            for card in self._card_widgets:
                if card.macro.name == name:
                    card.set_checked(is_chk)
                    break
        self._update_batch_delete_status()

    def _get_checked_macro_names(self) -> list[str]:
        if self._current_view_mode == "cards" and self._card_widgets:
            return [card.macro.name for card in self._card_widgets if card._is_checked]
        checked = []
        for row in range(self.table_macros.rowCount()):
            chk = self.table_macros.item(row, 0)
            name_item = self.table_macros.item(row, 1)
            if chk and chk.checkState() == Qt.CheckState.Checked and name_item:
                checked.append(name_item.text().strip())
        return checked

    def _update_batch_delete_status(self):
        checked = self._get_checked_macro_names()
        count = len(checked)
        self.btn_confirm_delete.setText(f"🗑 削除する ({count}件)")
        self.btn_confirm_delete.setEnabled(count > 0)

        total = len(self._displayed_macros)
        self.chk_select_all.blockSignals(True)
        self.chk_select_all.setChecked(total > 0 and count == total)
        self.chk_select_all.blockSignals(False)

    @Slot()
    def _on_confirm_batch_delete(self):
        checked = self._get_checked_macro_names()
        if checked:
            self.delete_macros_requested.emit(checked)

    @Slot()
    def _on_table_selection_changed(self):
        selected_items = self.table_macros.selectedItems()
        if not selected_items:
            return

        row = selected_items[0].row()
        name_item = self.table_macros.item(row, 1)
        if name_item:
            clean_name = name_item.text().strip()
            self._select_macro(clean_name)

    @Slot(QTableWidgetItem)
    def _on_table_double_clicked(self, item: QTableWidgetItem):
        if not self._is_delete_mode and item and self.btn_run_selected.isEnabled():
            self.run_macro_requested.emit()

    @Slot(bool)
    def _update_control_buttons_state(self, can_run: bool):
        self.btn_run_selected.setEnabled(can_run)
        self.btn_edit_selected.setEnabled(can_run)
        self.btn_delete_selected.setEnabled(len(self._all_macros) > 0)
