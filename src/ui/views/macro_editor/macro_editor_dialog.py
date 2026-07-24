# src/ui/views/macro_editor/macro_editor_dialog.py
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QScrollArea, QLabel, QStackedWidget, QFrame, QScrollArea)
from PySide6.QtCore import Signal, Qt, QMimeData
from PySide6.QtGui import QDrag, QMouseEvent
from qfluentwidgets import TransparentToolButton, FluentIcon
from ui.views.macro_editor.macro_visual_canvas import MacroVisualCanvas

class ToolItemWidget(QFrame):
    def __init__(self, label_text: str, action_type: str, parent=None):
        super().__init__(parent)
        self.action_type = action_type
        self.setObjectName("ToolItem")
        self.setStyleSheet("""
            #ToolItem {
                background-color: #ffffff;
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                padding: 10px;
            }
            #ToolItem:hover {
                border: 1px solid #0078d4;
                background-color: #f3f2f1;
            }
        """)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        label = QLabel(label_text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        layout.addWidget(label)
        
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() > 5:
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setText(f"new_action:{self.action_type}")
            drag.setMimeData(mime_data)
            drag.exec_(Qt.DropAction.CopyAction)
        super().mouseMoveEvent(event)


class IndexItemWidget(QFrame):
    clicked = Signal(int)
    
    def __init__(self, title: str, index: int, depth: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.setObjectName("IndexItem")
        
        indent = depth * 20
        self.setStyleSheet(f"""
            #IndexItem {{
                background-color: transparent;
                border: none;
                border-radius: 4px;
                padding: 6px;
                padding-left: {indent + 6}px;
            }}
            #IndexItem:hover {{
                background-color: #e1dfdd;
            }}
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        label = QLabel(title)
        label.setStyleSheet("color: #333333;")
        layout.addWidget(label)
        
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


class MacroEditorScreen(QWidget):
    saved = Signal(str, list)
    canceled = Signal()
    run_requested = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MacroEditorScreen")
        self.setStyleSheet("background-color: #f9f9f9;")
        
        self.macro_name = ""
        self.commands = []
        self.workflow_dir = None
        self.is_temporary = False
        
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 1. 左端サイドバー（アイコンのみ）
        sidebar = QFrame()
        sidebar.setFixedWidth(48)
        sidebar.setStyleSheet("background-color: #f3f2f1; border-right: 1px solid #e0e0e0;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(4, 20, 4, 20)
        sidebar_layout.setSpacing(16)
        
        self.btn_tool = TransparentToolButton(FluentIcon.ADD)
        self.btn_tool.setToolTip("ツール")
        self.btn_tool.clicked.connect(lambda: self.side_panel_stack.setCurrentIndex(0))
        
        self.btn_index = TransparentToolButton(FluentIcon.MENU)
        self.btn_index.setToolTip("目次")
        self.btn_index.clicked.connect(lambda: self.side_panel_stack.setCurrentIndex(1))
        
        sidebar_layout.addWidget(self.btn_tool)
        sidebar_layout.addWidget(self.btn_index)
        sidebar_layout.addStretch()
        main_layout.addWidget(sidebar)
        
        # 2. サイドパネル（ツール / 目次）
        self.side_panel_stack = QStackedWidget()
        self.side_panel_stack.setFixedWidth(240)
        self.side_panel_stack.setStyleSheet("background-color: #ffffff; border-right: 1px solid #e0e0e0;")
        
        # 2.1 ツールパネル
        tool_panel = QWidget()
        tool_layout = QVBoxLayout(tool_panel)
        tool_layout.setContentsMargins(16, 20, 16, 20)
        tool_layout.setSpacing(12)
        tool_title = QLabel("ツール")
        tool_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #333;")
        tool_layout.addWidget(tool_title)
        
        tools = [
            ("クリック", "click"),
            ("テキスト入力", "type_text"),
            ("待機", "wait"),
            ("キー入力", "press_key")
        ]
        for label, action_type in tools:
            tool_layout.addWidget(ToolItemWidget(label, action_type))
        tool_layout.addStretch()
        self.side_panel_stack.addWidget(tool_panel)
        
        # 2.2 目次パネル
        index_panel = QWidget()
        index_layout = QVBoxLayout(index_panel)
        index_layout.setContentsMargins(16, 20, 16, 20)
        index_title = QLabel("目次")
        index_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #333;")
        index_layout.addWidget(index_title)
        
        self.index_scroll = QScrollArea()
        self.index_scroll.setWidgetResizable(True)
        self.index_scroll.setStyleSheet("border: none; background: transparent;")
        self.index_content = QWidget()
        self.index_content_layout = QVBoxLayout(self.index_content)
        self.index_content_layout.setContentsMargins(0, 0, 0, 0)
        self.index_content_layout.setSpacing(2)
        self.index_content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.index_scroll.setWidget(self.index_content)
        index_layout.addWidget(self.index_scroll)
        
        self.side_panel_stack.addWidget(index_panel)
        main_layout.addWidget(self.side_panel_stack)
        
        # 3. メインキャンバスエリア
        canvas_area = QWidget()
        canvas_layout = QVBoxLayout(canvas_area)
        canvas_layout.setContentsMargins(20, 20, 20, 20)
        
        header_layout = QHBoxLayout()
        self.title_label = QLabel("マクロ編集")
        font = self.title_label.font()
        font.setPointSize(18)
        font.setBold(True)
        self.title_label.setFont(font)
        header_layout.addWidget(self.title_label)
        canvas_layout.addLayout(header_layout)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        self.canvas_container = QWidget()
        self.canvas_container_layout = QVBoxLayout(self.canvas_container)
        self.canvas_container_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area.setWidget(self.canvas_container)
        
        canvas_layout.addWidget(self.scroll_area)
        
        footer_layout = QHBoxLayout()
        footer_layout.addStretch()
        
        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.setFixedSize(100, 32)
        self.btn_cancel.clicked.connect(self.canceled.emit)
        footer_layout.addWidget(self.btn_cancel)
        
        self.btn_save = QPushButton("保存")
        self.btn_save.setFixedSize(100, 32)
        self.btn_save.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold;")
        self.btn_save.clicked.connect(self._on_save_clicked)
        footer_layout.addWidget(self.btn_save)
        
        canvas_layout.addLayout(footer_layout)
        main_layout.addWidget(canvas_area, 1)
        
    def load_macro(self, macro_name: str, commands: list, workflow_dir: Path, is_temporary: bool = False):
        self.macro_name = macro_name
        self.commands = commands
        self.workflow_dir = workflow_dir
        self.is_temporary = is_temporary
        
        self.title_label.setText(f"{macro_name}" + (" (実行前確認)" if is_temporary else ""))
        self.btn_save.setText("実行" if is_temporary else "保存")
        
        while self.canvas_container_layout.count():
            item = self.canvas_container_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
                
        self.canvas = MacroVisualCanvas(self.commands, self.workflow_dir)
        self.canvas.commands_changed.connect(self.update_index)
        self.canvas_container_layout.addWidget(self.canvas)
        
        self.update_index()
        
    def update_index(self):
        while self.index_content_layout.count():
            item = self.index_content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
                
        depth = 0
        method_map = {
            "click": "クリック", "move": "マウス移動", "type_text": "テキスト入力",
            "press_key": "キー入力", "wait": "待機", "scroll": "スクロール", 
            "activate_window": "ウィンドウアクティブ化", "loop_start": "ループ開始", "loop_end": "ループ終了"
        }
        
        for i, cmd in enumerate(self.commands):
            method = cmd.get("method", "")
            if method == "loop_end":
                depth = max(0, depth - 1)
                
            title = method_map.get(method, method)
            item = IndexItemWidget(title, i, depth)
            item.clicked.connect(self._scroll_to_block)
            self.index_content_layout.addWidget(item)
            
            if method == "loop_start":
                depth += 1

    def _scroll_to_block(self, index: int):
        block_widget = self.canvas.get_block_widget(index)
        if block_widget:
            self.scroll_area.ensureWidgetVisible(block_widget, 50, 50)
        
    def _on_save_clicked(self):
        if self.is_temporary:
            self.run_requested.emit(self.commands)
        else:
            self.saved.emit(self.macro_name, self.commands)