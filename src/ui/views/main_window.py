# @role: 仕様書の画面要件に基づき、ヘッダーのステータス管理、緊急停止、マクロ一覧テーブルの初期化・描画を制御するメイン画面のビュークラス。
import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QPushButton, QTableWidget, 
    QTableWidgetItem, QAbstractItemView, QLabel, QHeaderView
)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt
from ui.views.record_dialog import RecordDialog
from ui.viewmodels.main_viewmodel import MainViewModel

class MainWindow(QMainWindow):
    """メインウィンドウ（ホーム画面）のインタラクションとUI表示を管理するクラス"""
    
    def __init__(self, viewmodel: MainViewModel):
        super().__init__()
        self.viewmodel = viewmodel
        self.setWindowTitle("Macro Manager")
        self.resize(900, 600)
        
        self.central_widget = self._load_ui_and_style("main_window.ui")
        self.setCentralWidget(self.central_widget)
        
        self.btn_emergency_stop = self.central_widget.findChild(QPushButton, "btnEmergencyStop")
        self.btn_status = self.central_widget.findChild(QPushButton, "btnStatus")
        self.btn_start_record = self.central_widget.findChild(QPushButton, "btnStartRecord")
        self.btn_run_selected = self.central_widget.findChild(QPushButton, "btnRunSelected")
        self.table_macros = self.central_widget.findChild(QTableWidget, "tableMacros")
        
        if self.btn_run_selected:
            self.btn_run_selected.setEnabled(False)
            
        if self.table_macros:
            self.table_macros.setShowGrid(False)
            self.table_macros.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table_macros.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table_macros.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table_macros.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
            self.table_macros.verticalHeader().setDefaultSectionSize(60)
        
        self._bind_viewmodel()
        self.viewmodel.load_macros()

    def _bind_viewmodel(self):
        if self.btn_emergency_stop:
            self.btn_emergency_stop.clicked.connect(self.viewmodel.trigger_emergency_stop)
        if self.btn_status:
            self.btn_status.clicked.connect(self.viewmodel.toggle_status)
        if self.btn_start_record:
            self.btn_start_record.clicked.connect(self.open_record_dialog)
        if self.btn_run_selected:
            self.btn_run_selected.clicked.connect(self.viewmodel.run_selected_macro)
            
        if self.table_macros:
            self.table_macros.itemSelectionChanged.connect(self._on_table_selection_changed)
            
        self.viewmodel.macros_updated.connect(self._render_table)
        self.viewmodel.status_changed.connect(self._update_status_ui)
        self.viewmodel.can_run_changed.connect(self._update_run_button_state)

    def _on_table_selection_changed(self):
        selected_items = self.table_macros.selectedItems()
        if selected_items:
            row = selected_items[0].row()
            macro_name = self.table_macros.item(row, 0).text()
            self.viewmodel.select_macro(macro_name)
        else:
            self.viewmodel.select_macro("")

    def _update_run_button_state(self, can_run: bool):
        if self.btn_run_selected:
            self.btn_run_selected.setEnabled(can_run)

    def _update_status_ui(self, state: str, label: str):
        if not self.btn_status:
            return
            
        self.btn_status.setText(label)
        self.btn_status.setProperty("state", state)
        
        self.btn_status.style().unpolish(self.btn_status)
        self.btn_status.style().polish(self.btn_status)

    def _create_badge(self, text: str, badge_type: str) -> QWidget:
        container = QWidget()
        lbl = QLabel(text)
        lbl.setProperty("badge", badge_type)
        lbl.setAlignment(Qt.AlignCenter)
        
        from PySide6.QtWidgets import QHBoxLayout
        h_layout = QHBoxLayout(container)
        h_layout.setContentsMargins(4, 4, 4, 4)
        h_layout.addWidget(lbl)
        
        return container

    def _render_table(self, macros: list):
        if not self.table_macros:
            return
            
        self.table_macros.setRowCount(len(macros))
        self.table_macros.setColumnCount(5)
        self.table_macros.setHorizontalHeaderLabels(["マクロ名", "直近の結果", "自己修復", "最終実行日時", "削除"])
        
        for row, macro in enumerate(macros):
            self.table_macros.setItem(row, 0, QTableWidgetItem(macro.name))
            
            status_badge = self._create_badge(macro.status_text, macro.status)
            self.table_macros.setCellWidget(row, 1, status_badge)
            
            heal_badge = self._create_badge(macro.heals, f"heal_{macro.heal_level}")
            self.table_macros.setCellWidget(row, 2, heal_badge)
            
            self.table_macros.setItem(row, 3, QTableWidgetItem(macro.last_run))
            
            btn_delete = QPushButton("🗑 削除")
            btn_delete.setObjectName("btnDelete")
            btn_delete.setCursor(Qt.PointingHandCursor)
            btn_delete.clicked.connect(lambda checked, m=macro.name: self.viewmodel.delete_macro(m))
            self.table_macros.setCellWidget(row, 4, btn_delete)

        self.table_macros.resizeColumnsToContents()
        self.table_macros.setColumnWidth(0, 220)
        self.table_macros.setColumnWidth(1, 110)
        self.table_macros.setColumnWidth(2, 90)
        self.table_macros.horizontalHeader().setStretchLastSection(True)

    def open_record_dialog(self):
        self.record_dialog = RecordDialog(self)
        self.record_dialog.show()

    def _load_ui_and_style(self, ui_file_name: str) -> QWidget:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ui_path = os.path.join(base_dir, "resources", "ui", ui_file_name)
        
        loader = QUiLoader()
        ui_file = QFile(ui_path)
        if not ui_file.open(QFile.ReadOnly):
            raise FileNotFoundError(f"Cannot open UI file: {ui_path}")
            
        widget = loader.load(ui_file, self)
        ui_file.close()
        
        if widget is None:
            raise RuntimeError(f"Failed to load UI file: {ui_path}")
        
        css_name = os.path.splitext(ui_file_name)[0] + ".css"
        css_path = os.path.join(base_dir, "resources", "css", css_name)
        
        if os.path.exists(css_path):
            with open(css_path, "r", encoding="utf-8") as f:
                stylesheet = f.read()
                widget.setStyleSheet(stylesheet)
                
        return widget