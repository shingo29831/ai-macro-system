"""Module: @role: マクロ編集画面全体のレイアウト構成、ツールバー・目次サイドバーの開閉、保存および実行前確認の統合制御を担当する。"""

from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QScrollArea, QLabel, QStackedWidget, QFrame, QMessageBox, QTabWidget)
from PySide6.QtCore import Signal, Qt
from qfluentwidgets import TransparentToolButton, FluentIcon
from ui.views.macro_editor.macro_visual_canvas import MacroVisualCanvas
from .editor_sidebar_items import IndexItemWidget, ToolItemWidget


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
        
        # スクロールバーのモダンスタイル
        modern_scrollbar_style = """
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                border: none;
                background-color: transparent;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: rgba(0, 0, 0, 0.2);
                min-height: 30px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: rgba(0, 0, 0, 0.4);
            }
            QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
            QScrollBar:horizontal {
                border: none;
                background-color: transparent;
                height: 8px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background-color: rgba(0, 0, 0, 0.2);
                min-width: 30px;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: rgba(0, 0, 0, 0.4);
            }
            QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {
                width: 0px;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: none;
            }
        """

        # 1. ツールパネル（左側）
        self.tool_panel = QWidget()
        self.tool_panel.setFixedWidth(270)
        self.tool_panel.setStyleSheet("background-color: #ffffff; border-right: 1px solid #e0e0e0;")
        tool_layout = QVBoxLayout(self.tool_panel)
        tool_layout.setContentsMargins(12, 16, 12, 16)
        tool_layout.setSpacing(10)

        tool_header = QHBoxLayout()
        tool_title = QLabel("ツール")
        tool_title.setStyleSheet("font-weight: bold; font-size: 15px; color: #333;")
        tool_header.addWidget(tool_title)
        tool_header.addStretch()
        tool_close_btn = TransparentToolButton(FluentIcon.CLOSE)
        tool_close_btn.setToolTip("閉じる")
        tool_close_btn.clicked.connect(self._toggle_tool_panel)
        tool_header.addWidget(tool_close_btn)
        tool_layout.addLayout(tool_header)

        self.tool_tabs = QTabWidget()
        self.tool_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background-color: #ffffff;
            }
            QTabBar::tab {
                background-color: transparent;
                color: #555555;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: bold;
                border-bottom: 2px solid transparent;
            }
            QTabBar::tab:selected {
                color: #0078d4;
                border-bottom: 2px solid #0078d4;
            }
            QTabBar::tab:hover {
                color: #0078d4;
            }
        """)

        def _create_tool_tab(items):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet(modern_scrollbar_style)
            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(4, 10, 4, 10)
            layout.setSpacing(8)
            for label, act_type in items:
                layout.addWidget(ToolItemWidget(label, act_type))
            layout.addStretch()
            scroll.setWidget(container)
            return scroll

        basic_tools = [
            ("ループ (繰り返し)", "loop"),
            ("クリック", "click"),
            ("マウス移動(ホバー)", "move"),
            ("スクロール", "scroll"),
            ("テキスト入力", "type_text"),
            ("キー入力", "press_key"),
            ("待機", "wait")
        ]
        browser_tools = [
            ("URLを開く", "browser_open_url"),
            ("要素クリック", "browser_click"),
            ("テキスト入力", "browser_type"),
            ("テキスト取得", "browser_read"),
            ("要素待機", "browser_wait"),
            ("タブを閉じる", "browser_close")
        ]
        excel_tools = [
            ("セル書き込み", "excel_write"),
            ("セル読み取り", "excel_read"),
            ("ブックを開く", "excel_open"),
            ("ブックを保存", "excel_save"),
            ("シート選択", "excel_sheet")
        ]

        self.tool_tabs.addTab(_create_tool_tab(basic_tools), "基本")
        self.tool_tabs.addTab(_create_tool_tab(browser_tools), "ブラウザ")
        self.tool_tabs.addTab(_create_tool_tab(excel_tools), "Excel")
        tool_layout.addWidget(self.tool_tabs)

        main_layout.addWidget(self.tool_panel)
        
        # 3. メインキャンバスエリア
        canvas_area = QWidget()
        canvas_layout = QVBoxLayout(canvas_area)
        canvas_layout.setContentsMargins(20, 20, 20, 20)
        
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_tool = TransparentToolButton(FluentIcon.ADD)
        self.btn_tool.setToolTip("ツールパネルの開閉")
        self.btn_tool.clicked.connect(self._toggle_tool_panel)
        header_layout.addWidget(self.btn_tool)

        self.title_label = QLabel("マクロ編集")
        font = self.title_label.font()
        font.setPointSize(18)
        font.setBold(True)
        self.title_label.setFont(font)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.btn_index = TransparentToolButton(FluentIcon.MENU)
        self.btn_index.setToolTip("目次パネルの開閉")
        self.btn_index.clicked.connect(self._toggle_index_panel)
        header_layout.addWidget(self.btn_index)

        canvas_layout.addLayout(header_layout)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet(modern_scrollbar_style)
        
        self.canvas_container = QWidget()
        self.canvas_container_layout = QVBoxLayout(self.canvas_container)
        self.canvas_container_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area.setWidget(self.canvas_container)
        
        canvas_layout.addWidget(self.scroll_area)
        
        footer_layout = QHBoxLayout()
        footer_layout.addStretch()
        
        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.setFixedSize(100, 32)
        self.btn_cancel.setStyleSheet("color: #333333; background-color: #f3f2f1; border: 1px solid #d0d0d0; border-radius: 4px;")
        self.btn_cancel.clicked.connect(self.canceled.emit)
        footer_layout.addWidget(self.btn_cancel)
        
        self.btn_save = QPushButton("保存")
        self.btn_save.setFixedSize(100, 32)
        self.btn_save.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold;")
        self.btn_save.clicked.connect(self._on_save_clicked)
        footer_layout.addWidget(self.btn_save)
        
        canvas_layout.addLayout(footer_layout)
        main_layout.addWidget(canvas_area, 1)

        # 4. 目次パネル（右側）
        self.index_panel = QWidget()
        self.index_panel.setFixedWidth(240)
        self.index_panel.setStyleSheet("background-color: #ffffff; border-left: 1px solid #e0e0e0;")
        index_layout = QVBoxLayout(self.index_panel)
        index_layout.setContentsMargins(12, 16, 12, 16)
        index_layout.setSpacing(10)

        index_header = QHBoxLayout()
        index_title = QLabel("目次")
        index_title.setStyleSheet("font-weight: bold; font-size: 15px; color: #333;")
        index_header.addWidget(index_title)
        index_header.addStretch()
        index_close_btn = TransparentToolButton(FluentIcon.CLOSE)
        index_close_btn.setToolTip("閉じる")
        index_close_btn.clicked.connect(self._toggle_index_panel)
        index_header.addWidget(index_close_btn)
        index_layout.addLayout(index_header)
        
        self.index_scroll = QScrollArea()
        self.index_scroll.setWidgetResizable(True)
        self.index_scroll.setStyleSheet(modern_scrollbar_style)
        self.index_content = QWidget()
        self.index_content_layout = QVBoxLayout(self.index_content)
        self.index_content_layout.setContentsMargins(0, 0, 0, 0)
        self.index_content_layout.setSpacing(2)
        self.index_content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.index_scroll.setWidget(self.index_content)
        index_layout.addWidget(self.index_scroll)
        
        main_layout.addWidget(self.index_panel)

    def _toggle_tool_panel(self):
        self.tool_panel.setVisible(not self.tool_panel.isVisible())

    def _toggle_index_panel(self):
        self.index_panel.setVisible(not self.index_panel.isVisible())
        
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
            "activate_window": "ウィンドウアクティブ化", "loop_start": "ループ開始", "loop_end": "ループ終了",
            "browser_action": "ブラウザ操作", "excel_action": "Excel操作"
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
        
    def _validate_loops(self) -> tuple[bool, str]:
        # Why: ループ構造不整合およびExcel未設定による実行時クラッシュを事前防止
        stack = []
        for i, cmd in enumerate(self.commands):
            method = cmd.get("method")
            if method == "loop_start":
                args = cmd.get("args", {})
                if args.get("data_source") == "excel" and not args.get("file_path"):
                    return False, "Excelデータ連携ループにExcelファイルが設定されていません。\n線の横にあるループ詳細からExcelファイルを選択してください。"
                stack.append(i)
            elif method == "loop_end":
                if not stack:
                    return False, "ループの開始と終了の順序が不正です。"
                start_idx = stack.pop()
                has_action = any(self.commands[j].get("method") not in ["loop_start", "loop_end"] for j in range(start_idx + 1, i))
                if not has_action:
                    return False, "ループ内にアクションが存在しません。"
        if len(stack) != 0:
            return False, "終了していないループが存在します。"
        return True, ""

    def _on_save_clicked(self):
        valid, err_msg = self._validate_loops()
        if not valid:
            QMessageBox.warning(self, "保存エラー", err_msg)
            return
            
        if self.is_temporary:
            self.run_requested.emit(self.commands)
        else:
            self.saved.emit(self.macro_name, self.commands)