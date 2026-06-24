# @role: マクロ生成中の進捗状態を表示し、ViewModelを介してユーザーからのキャンセル要求を制御する専用のダイアログ。

import os
from PySide6.QtWidgets import QDialog, QWidget, QPushButton, QLabel, QProgressBar
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt, Slot
from ui.viewmodels.main_viewmodel import MainViewModel

class ProgressDialog(QDialog):
    """AIマクロ生成中の非同期処理プログレスバーを管理するクラス"""

    def __init__(self, viewmodel: MainViewModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.viewmodel = viewmodel
        
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setModal(True)
        
        self.ui_widget = self._load_ui_and_style("progress_dialog.ui")
        
        self.lbl_status = self.ui_widget.findChild(QLabel, "lblStatus")
        self.progress_bar = self.ui_widget.findChild(QProgressBar, "progressBar")
        self.btn_cancel = self.ui_widget.findChild(QPushButton, "btnCancel")
        
        if self.ui_widget.layout():
            self.setLayout(self.ui_widget.layout())
            
        if self.btn_cancel:
            self.btn_cancel.clicked.connect(self._on_cancel_clicked)
            
        self.viewmodel.generation_progress.connect(self._update_progress)
        
    @Slot(int, str)
    def _update_progress(self, value: int, message: str):
        if self.progress_bar:
            self.progress_bar.setValue(value)
        if self.lbl_status:
            self.lbl_status.setText(message)

    @Slot()
    def _on_cancel_clicked(self):
        if self.btn_cancel:
            self.btn_cancel.setEnabled(False)
            self.btn_cancel.setText("キャンセル中...")
        self.viewmodel.cancel_generation()

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