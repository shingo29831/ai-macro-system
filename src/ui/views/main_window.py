# @role: 仕様書の画面要件に基づき、ヘッダーのステータス管理、マクロ一覧テーブルの初期化・描画、および設定画面・実行画面への遷移を制御するメイン画面のビュークラス。
import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QPushButton, QTableWidget, 
    QTableWidgetItem, QAbstractItemView, QLabel, QHeaderView, QMessageBox
)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt, Slot
from ui.views.record_dialog import RecordDialog
from ui.views.running_dialog import RunningDialog
from ui.views.settings_dialog import SettingsDialog
from ui.views.progress_dialog import ProgressDialog
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
        
        self.btn_start_record = self.central_widget.findChild(QPushButton, "btnStartRecord")
        self.btn_run_selected = self.central_widget.findChild(QPushButton, "btnRunSelected")
        self.btn_delete_selected = self.central_widget.findChild(QPushButton, "btnDeleteSelected")
        self.btn_settings = self.central_widget.findChild(QPushButton, "btnSettings")
        self.table_macros = self.central_widget.findChild(QTableWidget, "tableMacros")
        
        self.progress_dialog = None
        
        if self.btn_run_selected:
            self.btn_run_selected.setEnabled(False)
        if self.btn_delete_selected:
            self.btn_delete_selected.setEnabled(False)
            
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
        if self.btn_start_record:
            self.btn_start_record.clicked.connect(self.open_record_dialog)
        if self.btn_run_selected:
            self.btn_run_selected.clicked.connect(self.open_running_dialog)
        if self.btn_delete_selected:
            self.btn_delete_selected.clicked.connect(self._on_delete_selected_clicked)
        if self.btn_settings:
            self.btn_settings.clicked.connect(self.open_settings_dialog)
            
        if self.table_macros:
            self.table_macros.itemSelectionChanged.connect(self._on_table_selection_changed)
            
        self.viewmodel.macros_updated.connect(self._render_table)
        self.viewmodel.can_run_changed.connect(self._update_control_buttons_state)
        self.viewmodel.execution_finished.connect(self._on_execution_finished)
        self.viewmodel.generation_finished.connect(self._on_generation_finished)

    def _on_table_selection_changed(self):
        selected_items = self.table_macros.selectedItems()
        if selected_items:
            row = selected_items[0].row()
            macro_name = self.table_macros.item(row, 0).text()
            self.viewmodel.select_macro(macro_name)
        else:
            self.viewmodel.select_macro("")

    def _update_control_buttons_state(self, can_run: bool):
        if self.btn_run_selected:
            self.btn_run_selected.setEnabled(can_run)
        if self.btn_delete_selected:
            self.btn_delete_selected.setEnabled(can_run)

    def _on_delete_selected_clicked(self):
        selected_macro_name = self.viewmodel._selected_macro
        if not selected_macro_name:
            return
            
        reply = QMessageBox.question(
            self,
            "削除の確認",
            f"「{selected_macro_name}」を完全に削除してもよろしいですか？\nこの操作は元に戻せません。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.viewmodel.delete_macro(selected_macro_name)

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
        self.table_macros.setColumnCount(4)
        self.table_macros.setHorizontalHeaderLabels(["マクロ名", "直近の結果", "自己修復", "最終実行日時"])
        
        for row, macro in enumerate(macros):
            self.table_macros.setItem(row, 0, QTableWidgetItem(macro.name))
            
            status_badge = self._create_badge(macro.status_text, macro.status)
            self.table_macros.setCellWidget(row, 1, status_badge)
            
            heal_badge = self._create_badge(macro.heals, f"heal_{macro.heal_level}")
            self.table_macros.setCellWidget(row, 2, heal_badge)
            
            self.table_macros.setItem(row, 3, QTableWidgetItem(macro.last_run))
            
        self.table_macros.resizeColumnsToContents()
        self.table_macros.setColumnWidth(0, 300)
        self.table_macros.setColumnWidth(1, 140)
        self.table_macros.setColumnWidth(2, 120)
        self.table_macros.horizontalHeader().setStretchLastSection(True)
        
        self.viewmodel.select_macro("")

    def open_record_dialog(self):
        try:
            self.viewmodel.start_recording()
            
            self.record_dialog = RecordDialog(self, on_stop_callback=self._on_recording_stopped)
            self.record_dialog.show()
            
            self.hide()
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"記録の開始に失敗しました:\n{e}")

    def _on_recording_stopped(self):
        try:
            self.viewmodel.stop_recording()
            # 記録終了後、メインウィンドウではなくプログレスダイアログを表示して進捗を見せる
            self.progress_dialog = ProgressDialog(self.viewmodel, self)
            self.progress_dialog.show()
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"記録の停止中にエラーが発生しました:\n{e}")
            self.show()
            self.raise_()
            self.activateWindow()

    @Slot(bool, str)
    def _on_generation_finished(self, success: bool, message: str):
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
            
        if not success and message != "キャンセルされました":
            QMessageBox.warning(self, "マクロ生成エラー", f"マクロの生成に失敗しました:\n{message}")
            
        self.show()
        self.raise_()
        self.activateWindow()

    def open_running_dialog(self):
        try:
            self.viewmodel.run_selected_macro()
            
            self.running_dialog = RunningDialog(self, on_stop_callback=self._on_emergency_stop_triggered)
            self.running_dialog.show()
            
            self.hide()
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"実行の開始に失敗しました:\n{e}")

    def _on_emergency_stop_triggered(self):
        try:
            self.viewmodel.trigger_emergency_stop()
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"強制停止中にエラーが発生しました:\n{e}")

    def _on_execution_finished(self):
        if hasattr(self, 'running_dialog') and self.running_dialog:
            self.running_dialog.close_dialog()
            self.running_dialog = None
            
        self.show()
        self.raise_()
        self.activateWindow()

    def open_settings_dialog(self):
        self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.exec()

    def _load_ui_and_style(self, ui_file_name: str) -> QWidget:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ui_path = os.path.join(os.path.dirname(base_dir), "ui", "resources", "ui", ui_file_name)
        
        if not os.path.exists(ui_path):
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
        css_path = os.path.join(os.path.dirname(base_dir), "ui", "resources", "css", css_name)
        if not os.path.exists(css_path):
            css_path = os.path.join(base_dir, "resources", "css", css_name)
            
        if os.path.exists(css_path):
            with open(css_path, "r", encoding="utf-8") as f:
                stylesheet = f.read()
                widget.setStyleSheet(stylesheet)
                
        return widget