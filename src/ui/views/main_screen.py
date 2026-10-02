"""Module: @role: ホーム画面。非ITユーザー向けのマクロ一覧表示・検索・並び替え・チェックボックス一括削除・実行制御を担当するビュー。"""

import re
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QLabel,
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
        self._current_selected_name: str = ""
        self._is_delete_mode: bool = False

        self.btn_start_record = None
        self.btn_run_selected = None
        self.btn_delete_selected = None
        self.btn_edit_selected = None

        self.chk_select_all = None
        self.btn_cancel_delete = None
        self.btn_confirm_delete = None

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

        # 録画エリア（ボーダーラインのないクリーンなレイアウト）
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

        # 通常ボタングループ
        table_top_bar.addWidget(self.btn_edit_selected)
        table_top_bar.addWidget(self.btn_delete_selected)
        table_top_bar.addWidget(self.btn_run_selected)

        # 一括削除ボタングループ
        table_top_bar.addWidget(self.chk_select_all)
        table_top_bar.addWidget(self.btn_cancel_delete)
        table_top_bar.addWidget(self.btn_confirm_delete)

        main_layout.addLayout(table_top_bar)

        # 検索・並び替えコントロールバー
        control_bar = QHBoxLayout()
        control_bar.setSpacing(12)

        self.search_box = SearchLineEdit(self)
        self.search_box.setPlaceholderText("マクロ名で検索...")
        self.search_box.setFont(self._font(10))
        self.search_box.setFixedWidth(240)

        sort_label = QLabel("並び替え:", self)
        sort_label.setFont(self._font(10))
        sort_label.setStyleSheet("color: #475569;")

        self.sort_combo = ComboBox(self)
        self.sort_combo.setFont(self._font(10))
        self.sort_combo.setFixedWidth(170)
        self.sort_combo.addItems([
            "更新が新しい順",
            "更新が古い順",
            "作成が新しい順",
            "作成が古い順",
            "マクロ名順",
            "マクロ名順（降順）",
        ])

        hint_label = QLabel("※ ダブルクリックでも即時実行できます", self)
        hint_label.setFont(self._font(9))
        hint_label.setStyleSheet("color: #94a3b8;")

        control_bar.addWidget(self.search_box)
        control_bar.addWidget(sort_label)
        control_bar.addWidget(self.sort_combo)
        control_bar.addSpacing(8)
        control_bar.addWidget(hint_label)
        control_bar.addStretch(1)

        main_layout.addLayout(control_bar)

        # マクロテーブル (列0: チェックボックス, 列1: マクロ名, 列2: 更新日時, 列3: 作成日時, 列4: 自己修復)
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

    def _setup_table(self):
        self.table_macros.setShowGrid(False)
        self.table_macros.setAlternatingRowColors(True)
        self.table_macros.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_macros.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self.table_macros.verticalHeader().setVisible(False)
        self.table_macros.verticalHeader().setDefaultSectionSize(50)

        header = self.table_macros.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)

        self.table_macros.setColumnWidth(0, 56)
        self.table_macros.setColumnWidth(2, 160)
        self.table_macros.setColumnWidth(3, 160)
        self.table_macros.setColumnWidth(4, 110)

        # 通常時はチェックボックス列を隠す
        self.table_macros.setColumnHidden(0, True)

    def _bind_viewmodel(self):
        self.btn_start_record.clicked.connect(self.start_record_requested.emit)
        self.btn_run_selected.clicked.connect(self.run_macro_requested.emit)
        self.btn_edit_selected.clicked.connect(self.edit_macro_requested.emit)

        # 削除ボタン押下で複数選択モードに移行
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

        total_count = len(self._all_macros)
        filtered_count = len(items)
        if query:
            self.lbl_count.setText(f"({filtered_count}件 / 全{total_count}件)")
        else:
            self.lbl_count.setText(f"({total_count}件)")

        if not items:
            self.table_macros.setVisible(False)
            self.empty_state_label.setVisible(True)
            if query:
                self.empty_state_label.setText(f"「{query}」に一致するマクロは見つかりませんでした。")
            else:
                self.empty_state_label.setText(
                    "保存されているマクロがありません。\n「● 記録を開始」ボタンを押して操作を記録しましょう。"
                )
        else:
            self.table_macros.setVisible(True)
            self.empty_state_label.setVisible(False)

        self._render_table(items)

    def _render_table(self, macros: list[MacroSummary]):
        self.table_macros.blockSignals(True)
        self.table_macros.clearContents()
        self.table_macros.setRowCount(len(macros))

        item_font = self._font(10)
        selected_row = -1

        for row, macro in enumerate(macros):
            # チェックボックス (列0)
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Unchecked)
            chk_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_macros.setItem(row, 0, chk_item)

            # マクロ名 (列1)
            name_item = QTableWidgetItem(f"  {macro.name}")
            name_item.setFont(self._font(10, bold=True))
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_macros.setItem(row, 1, name_item)

            # 最終更新日時 (列2)
            updated_item = QTableWidgetItem(macro.updated_at)
            updated_item.setFont(item_font)
            updated_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            updated_item.setFlags(updated_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_macros.setItem(row, 2, updated_item)

            # 作成日時 (列3)
            created_item = QTableWidgetItem(macro.created_at)
            created_item.setFont(item_font)
            created_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            created_item.setFlags(created_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_macros.setItem(row, 3, created_item)

            # 自己修復回数 (列4)
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
        else:
            self.viewmodel.select_macro("")

    @Slot()
    def _enter_delete_mode(self):
        """削除ボタン押下時にチェックボックス列と一括削除ツールバーを表示する"""
        if not self._all_macros:
            return

        self._is_delete_mode = True
        self.table_macros.setColumnHidden(0, False)

        self.btn_edit_selected.setVisible(False)
        self.btn_delete_selected.setVisible(False)
        self.btn_run_selected.setVisible(False)

        self.chk_select_all.setVisible(True)
        self.btn_cancel_delete.setVisible(True)
        self.btn_confirm_delete.setVisible(True)

        # 既に選択中のマクロがあれば初期チェックを付与
        if self._current_selected_name:
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
        """一括削除モードを終了して通常の表示状態に戻す"""
        self._is_delete_mode = False
        self.table_macros.setColumnHidden(0, True)

        self.chk_select_all.setVisible(False)
        self.btn_cancel_delete.setVisible(False)
        self.btn_confirm_delete.setVisible(False)

        self.btn_edit_selected.setVisible(True)
        self.btn_delete_selected.setVisible(True)
        self.btn_run_selected.setVisible(True)

        # 全チェックをクリア
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
        target_state = Qt.CheckState.Checked if state == 2 else Qt.CheckState.Unchecked
        self.table_macros.blockSignals(True)
        for row in range(self.table_macros.rowCount()):
            chk_item = self.table_macros.item(row, 0)
            if chk_item:
                chk_item.setCheckState(target_state)
        self.table_macros.blockSignals(False)
        self._update_batch_delete_status()

    @Slot(int, int)
    def _on_cell_clicked(self, row: int, column: int):
        # 削除モード中は行全体のクリックでチェックボックスをトグル可能にする
        if not self._is_delete_mode or column == 0:
            return

        chk_item = self.table_macros.item(row, 0)
        if chk_item:
            current = chk_item.checkState()
            chk_item.setCheckState(
                Qt.CheckState.Unchecked if current == Qt.CheckState.Checked else Qt.CheckState.Checked
            )

    @Slot(QTableWidgetItem)
    def _on_table_item_changed(self, item: QTableWidgetItem):
        if not self._is_delete_mode or item.column() != 0:
            return
        self._update_batch_delete_status()

    def _get_checked_macro_names(self) -> list[str]:
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

        total = self.table_macros.rowCount()
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
            self._current_selected_name = ""
            self.viewmodel.select_macro("")
            return

        row = selected_items[0].row()
        name_item = self.table_macros.item(row, 1)
        if name_item:
            clean_name = name_item.text().strip()
            self._current_selected_name = clean_name
            self.viewmodel.select_macro(clean_name)

    @Slot(QTableWidgetItem)
    def _on_table_double_clicked(self, item: QTableWidgetItem):
        if not self._is_delete_mode and item and self.btn_run_selected.isEnabled():
            self.run_macro_requested.emit()

    @Slot(bool)
    def _update_control_buttons_state(self, can_run: bool):
        self.btn_run_selected.setEnabled(can_run)
        self.btn_edit_selected.setEnabled(can_run)
        self.btn_delete_selected.setEnabled(len(self._all_macros) > 0)
