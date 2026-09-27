"""Module: @role: マクロの一覧表示、選択状態の管理、新規記録および実行・編集・削除トリガーを担うメイン画面（ホーム）ビュー。"""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
)

from ui.viewmodels.main_viewmodel import MainViewModel


class MainScreen(QWidget):
    """ホーム画面。マクロの記録・実行・削除・一覧表示を担当する。"""

    start_record_requested = Signal()
    run_macro_requested = Signal()
    delete_macro_requested = Signal()
    edit_macro_requested = Signal()

    def __init__(self, viewmodel: MainViewModel, parent=None):
        super().__init__(parent)

        self.viewmodel = viewmodel
        self.setObjectName("MacroManager")

        self.btn_start_record = None
        self.btn_run_selected = None
        self.btn_delete_selected = None
        self.table_macros = None

        self._build_ui()
        self._setup_table()
        self._bind_viewmodel()

        self.viewmodel.load_macros()

    def _font(self, size: int, bold: bool = False) -> QFont:
        """日本語表示が安定しやすいフォントを生成する。"""
        font = QFont("Yu Gothic UI", size)
        font.setStyleHint(QFont.StyleHint.SansSerif)

        if bold:
            font.setWeight(QFont.Weight.Bold)
        else:
            font.setWeight(QFont.Weight.Normal)

        return font

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(20)

        page_title = TitleLabel("ホーム", self)
        page_title.setFont(self._font(22, bold=True))

        page_description = QLabel(
            "マクロの記録、実行、管理を行えます",
            self,
        )
        page_description.setFont(self._font(10))
        page_description.setStyleSheet("color: #64748b;")

        main_layout.addWidget(page_title)
        main_layout.addWidget(page_description)
        main_layout.addSpacing(4)

        record_area = QHBoxLayout()
        record_area.setSpacing(16)

        record_text_layout = QVBoxLayout()
        record_text_layout.setSpacing(4)

        record_title = SubtitleLabel("新しいマクロを作成", self)
        record_title.setFont(self._font(15, bold=True))

        record_description = QLabel(
            "クリック、キーボード入力、スクロールなどの操作を記録します。",
            self,
        )
        record_description.setFont(self._font(10))
        record_description.setStyleSheet("color: #64748b;")

        record_text_layout.addWidget(record_title)
        record_text_layout.addWidget(record_description)

        self.btn_start_record = PrimaryPushButton(
            "●  記録を開始",
            self,
        )
        self.btn_start_record.setFont(self._font(11, bold=True))
        self.btn_start_record.setFixedSize(210, 48)

        record_area.addLayout(record_text_layout)
        record_area.addStretch(1)
        record_area.addWidget(
            self.btn_start_record,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )

        main_layout.addLayout(record_area)
        main_layout.addSpacing(10)

        table_header = QHBoxLayout()
        table_header.setSpacing(10)

        table_title_layout = QVBoxLayout()
        table_title_layout.setSpacing(2)

        macro_list_title = SubtitleLabel("マクロ一覧", self)
        macro_list_title.setFont(self._font(15, bold=True))

        macro_list_description = QLabel(
            "実行したいマクロを選択してください",
            self,
        )
        macro_list_description.setFont(self._font(10))
        macro_list_description.setStyleSheet("color: #64748b;")

        table_title_layout.addWidget(macro_list_title)
        table_title_layout.addWidget(macro_list_description)

        self.btn_edit_selected = PushButton("編集", self)
        self.btn_edit_selected.setFont(self._font(10))
        self.btn_edit_selected.setFixedSize(100, 36)
        self.btn_edit_selected.setEnabled(False)

        self.btn_delete_selected = PushButton("削除", self)
        self.btn_delete_selected.setFont(self._font(10))
        self.btn_delete_selected.setFixedSize(100, 36)
        self.btn_delete_selected.setEnabled(False)

        self.btn_run_selected = PrimaryPushButton("▶ 実行", self)
        self.btn_run_selected.setFont(self._font(10, bold=True))
        self.btn_run_selected.setFixedSize(110, 36)
        self.btn_run_selected.setEnabled(False)

        table_header.addLayout(table_title_layout)
        table_header.addStretch(1)
        table_header.addWidget(self.btn_edit_selected)
        table_header.addWidget(self.btn_delete_selected)
        table_header.addWidget(self.btn_run_selected)

        main_layout.addLayout(table_header)

        self.table_macros = TableWidget(self)
        self.table_macros.setColumnCount(3)
        self.table_macros.setHorizontalHeaderLabels(
            [
                "マクロ名",
                "自己修復",
                "最終実行日時",
            ]
        )
        self.table_macros.setFont(self._font(10))
        self.table_macros.horizontalHeader().setFont(self._font(10, bold=True))

        main_layout.addWidget(self.table_macros, 1)

    def _setup_table(self):
        self.table_macros.setShowGrid(False)
        self.table_macros.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table_macros.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )

        self.table_macros.verticalHeader().setVisible(False)
        self.table_macros.verticalHeader().setDefaultSectionSize(58)

        header = self.table_macros.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)

        self.table_macros.setColumnWidth(1, 115)
        self.table_macros.setColumnWidth(2, 200)

    def _bind_viewmodel(self):
        self.btn_start_record.clicked.connect(
            self.start_record_requested.emit
        )
        self.btn_run_selected.clicked.connect(
            self.run_macro_requested.emit
        )
        self.btn_delete_selected.clicked.connect(
            self.delete_macro_requested.emit
        )
        self.btn_edit_selected.clicked.connect(
            self.edit_macro_requested.emit
        )

        self.table_macros.itemSelectionChanged.connect(
            self._on_table_selection_changed
        )

        self.viewmodel.macros_updated.connect(self._render_table)
        self.viewmodel.can_run_changed.connect(
            self._update_control_buttons_state
        )

    @Slot()
    def _on_table_selection_changed(self):
        selected_items = self.table_macros.selectedItems()

        if not selected_items:
            self.viewmodel.select_macro("")
            return

        row = selected_items[0].row()
        macro_name_item = self.table_macros.item(row, 0)

        if macro_name_item:
            self.viewmodel.select_macro(macro_name_item.text())

    @Slot(bool)
    def _update_control_buttons_state(self, can_run: bool):
        self.btn_run_selected.setEnabled(can_run)
        self.btn_delete_selected.setEnabled(can_run)
        self.btn_edit_selected.setEnabled(can_run)

    @Slot(list)
    def _render_table(self, macros: list):
        self.table_macros.clearContents()
        self.table_macros.setRowCount(len(macros))

        item_font = self._font(10)
        self.table_macros.setFont(item_font)
        self.table_macros.horizontalHeader().setFont(
            self._font(10, bold=True)
        )

        for row, macro in enumerate(macros):
            macro_name = QTableWidgetItem(macro.name)
            macro_name.setFont(item_font)
            macro_name.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter
            )

            self.table_macros.setItem(row, 0, macro_name)

            heals = QTableWidgetItem(macro.heals)
            heals.setFont(item_font)
            heals.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter
            )
            heals.setFlags(
                heals.flags()
                & ~Qt.ItemFlag.ItemIsEditable
            )
            self.table_macros.setItem(row, 1, heals)

            last_run = QTableWidgetItem(macro.last_run)
            last_run.setFont(item_font)
            last_run.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter
            )
            last_run.setFlags(
                last_run.flags()
                & ~Qt.ItemFlag.ItemIsEditable
            )
            self.table_macros.setItem(row, 2, last_run)

        self.viewmodel.select_macro("")
