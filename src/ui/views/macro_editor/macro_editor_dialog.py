# src/ui/views/macro_editor/macro_editor_dialog.py
from pathlib import Path
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QScrollArea
from ui.views.macro_editor.macro_visual_canvas import MacroVisualCanvas

class MacroEditorDialog(QDialog):
    def __init__(self, macro_name: str, commands: list, workflow_dir: Path, is_temporary: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"マクロ編集 - {macro_name}" + (" (実行前確認)" if is_temporary else ""))
        self.resize(800, 600)
        
        self.commands = commands
        
        layout = QVBoxLayout(self)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        
        self.canvas = MacroVisualCanvas(self.commands, workflow_dir)
        self.scroll_area.setWidget(self.canvas)
        
        layout.addWidget(self.scroll_area)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)
        
        self.btn_save = QPushButton("実行" if is_temporary else "保存")
        self.btn_save.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold;")
        self.btn_save.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_save)
        
        layout.addLayout(btn_layout)
        
    def get_commands(self):
        return self.commands