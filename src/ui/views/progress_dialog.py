# src/ui/views/progress_dialog.py
# @role: マクロ生成中の進捗状態を表示し、ViewModelを介してユーザーからのキャンセル要求を制御する専用のダイアログ。

from PySide6.QtWidgets import QDialog, QWidget, QPushButton, QLabel, QProgressBar, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Qt, Slot
from ui.viewmodels.main_viewmodel import MainViewModel

class ProgressDialog(QDialog):
    """AIマクロ生成中の非同期処理プログレスバーを管理するクラス"""

    def __init__(self, viewmodel: MainViewModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.viewmodel = viewmodel
        
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setModal(True)
        self.setWindowTitle("マクロ生成中")
        self.setFixedSize(400, 150)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        self.lbl_status = QLabel("AIマクロ生成中...", self)
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold;")
        
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        
        self.btn_cancel = QPushButton("キャンセル", self)
        self.btn_cancel.setFixedSize(120, 32)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addStretch()
        
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.progress_bar)
        layout.addLayout(btn_layout)
            
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