# src/ui/views/macro_editor/macro_editor_dialog.py
from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QScrollArea, QLabel
from PySide6.QtCore import Signal
from ui.views.macro_editor.macro_visual_canvas import MacroVisualCanvas

class MacroEditorScreen(QWidget):
    saved = Signal(str, list)
    canceled = Signal()
    run_requested = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MacroEditorScreen")
        
        self.macro_name = ""
        self.commands = []
        self.workflow_dir = None
        self.is_temporary = False
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)
        
        self.title_label = QLabel("マクロ編集")
        font = self.title_label.font()
        font.setPointSize(22)
        font.setBold(True)
        self.title_label.setFont(font)
        layout.addWidget(self.title_label)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        self.canvas_container = QWidget()
        self.canvas_layout = QVBoxLayout(self.canvas_container)
        self.canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area.setWidget(self.canvas_container)
        
        layout.addWidget(self.scroll_area)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.setFixedSize(120, 36)
        self.btn_cancel.clicked.connect(self.canceled.emit)
        btn_layout.addWidget(self.btn_cancel)
        
        self.btn_save = QPushButton("保存")
        self.btn_save.setFixedSize(120, 36)
        self.btn_save.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold;")
        self.btn_save.clicked.connect(self._on_save_clicked)
        btn_layout.addWidget(self.btn_save)
        
        layout.addLayout(btn_layout)
        
    def load_macro(self, macro_name: str, commands: list, workflow_dir: Path, is_temporary: bool = False):
        self.macro_name = macro_name
        self.commands = commands
        self.workflow_dir = workflow_dir
        self.is_temporary = is_temporary
        
        self.title_label.setText(f"マクロ編集 - {macro_name}" + (" (実行前確認)" if is_temporary else ""))
        self.btn_save.setText("実行" if is_temporary else "保存")
        
        while self.canvas_layout.count():
            item = self.canvas_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
                
        self.canvas = MacroVisualCanvas(self.commands, self.workflow_dir)
        self.canvas_layout.addWidget(self.canvas)
        
    def _on_save_clicked(self):
        if self.is_temporary:
            self.run_requested.emit(self.commands)
        else:
            self.saved.emit(self.macro_name, self.commands)