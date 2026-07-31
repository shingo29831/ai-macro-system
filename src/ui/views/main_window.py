# src/ui/views/main_window.py
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QButtonGroup,
    QGridLayout
)
from PySide6.QtWidgets import QHeaderView

from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TableWidget,
    Theme,
    TitleLabel,
    setTheme,
    BodyLabel,
    RadioButton,
    LineEdit
)

from ui.viewmodels.main_viewmodel import MainViewModel
from ui.views.progress_dialog import ProgressDialog
from ui.views.record_dialog import RecordDialog
from ui.views.running_dialog import RunningDialog
from ui.views.settings_dialog import SettingsDialog
from ui.views.macro_editor.macro_editor_dialog import MacroEditorScreen
from PySide6.QtWidgets import QDialog, QComboBox, QScrollArea
from PySide6.QtGui import QImage, QPixmap
from core.executor.window_manager import get_open_windows_info

from PySide6.QtWidgets import QFrame

class WindowThumbnailWidget(QFrame):
    clicked = Signal(int)

    def __init__(self, hwnd, title, pixmap, parent=None):
        super().__init__(parent)
        self.hwnd = hwnd
        self.title = title
        self.is_selected = False
        self.is_confirmed = False
        
        self.setFixedSize(220, 220)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setFixedHeight(40)
        
        self.thumb_label = QLabel()
        self.thumb_label.setAlignment(Qt.AlignCenter)
        if pixmap:
            self.thumb_label.setPixmap(pixmap.scaled(200, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.thumb_label.setText("No Image")
            self.thumb_label.setStyleSheet("background-color: #f0f0f0; color: #888;")
        self.thumb_label.setFixedSize(200, 150)
        
        layout.addWidget(self.title_label)
        layout.addWidget(self.thumb_label)
        
        self.update_style()
        
    def set_selected(self, selected):
        if self.is_confirmed:
            return
        self.is_selected = selected
        self.update_style()
        
    def set_confirmed(self, confirmed):
        self.is_confirmed = confirmed
        self.update_style()
        
    def update_style(self):
        if self.is_confirmed:
            self.setStyleSheet("""
                WindowThumbnailWidget {
                    background-color: #f0f0f0;
                    border: 2px solid #888888;
                    border-radius: 8px;
                }
            """)
            self.title_label.setStyleSheet("color: #888888;")
        elif self.is_selected:
            self.setStyleSheet("""
                WindowThumbnailWidget {
                    background-color: #e6f2ff;
                    border: 4px solid #0066cc;
                    border-radius: 8px;
                }
            """)
            self.title_label.setStyleSheet("color: black; font-weight: bold;")
        else:
            self.setStyleSheet("""
                WindowThumbnailWidget {
                    background-color: white;
                    border: 1px solid #cccccc;
                    border-radius: 8px;
                }
                WindowThumbnailWidget:hover {
                    background-color: #f5f5f5;
                    border: 2px solid #999999;
                }
            """)
            self.title_label.setStyleSheet("color: black;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.is_confirmed:
            self.clicked.emit(self.hwnd)

class WindowMappingDialog(QDialog):
    def __init__(self, target_titles, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ウィンドウの紐付け")
        self.resize(800, 600)
        self.setStyleSheet("""
            QDialog { background-color: white; }
            QLabel { color: black; }
        """)
        self.target_titles = target_titles
        self.current_title_index = 0
        self.mapping = {}
        self.confirmed_hwnds = set()
        self.current_selected_hwnd = None
        
        self.all_windows = [{"hwnd": -1, "title": "新規起動", "thumbnail": None}] + get_open_windows_info()
        
        layout = QVBoxLayout(self)
        
        self.progress_label = SubtitleLabel("")
        self.instruction_label = BodyLabel("")
        layout.addWidget(self.progress_label)
        layout.addWidget(self.instruction_label)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        self.scroll_widget = QWidget()
        self.scroll_widget.setStyleSheet("QWidget { background-color: transparent; }")
        
        self.grid_layout = QGridLayout(self.scroll_widget)
        self.grid_layout.setSpacing(15)
        
        self.scroll.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll)
        
        btn_layout = QHBoxLayout()
        self.cancel_btn = PushButton("キャンセル")
        self.cancel_btn.clicked.connect(self.reject)
        
        self.ok_btn = PrimaryPushButton("確定")
        self.ok_btn.clicked.connect(self.on_confirm_clicked)
        self.ok_btn.setEnabled(False)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)
        layout.addLayout(btn_layout)
        
        self.update_ui_state()

    def sort_windows(self, target_title):
        app_name = target_title.split("—")[-1].split("-")[-1].strip().lower()
        target_lower = target_title.lower()
        
        def score(win):
            if win["hwnd"] == -1:
                return -100
            
            s = 0
            if win["hwnd"] in self.confirmed_hwnds:
                s += 1000
                
            win_title_lower = win["title"].lower()
            if target_lower == win_title_lower:
                s -= 50
            elif target_lower in win_title_lower or win_title_lower in target_lower:
                s -= 40
            elif app_name and app_name in win_title_lower:
                s -= 30
                
            return s
            
        return sorted(self.all_windows, key=score)

    def update_ui_state(self):
        target_title = self.target_titles[self.current_title_index]
        self.progress_label.setText(f"ウィンドウの紐付け ({self.current_title_index + 1}/{len(self.target_titles)})")
        self.instruction_label.setText(f"「{target_title}」に紐付けるウィンドウを選択してください。")
        
        if self.current_title_index == len(self.target_titles) - 1:
            self.ok_btn.setText("完了")
        else:
            self.ok_btn.setText("確定")
            
        for i in reversed(range(self.grid_layout.count())): 
            widgetToRemove = self.grid_layout.itemAt(i).widget()
            self.grid_layout.removeWidget(widgetToRemove)
            widgetToRemove.setParent(None)
            
        self.thumbnail_widgets = []
        
        sorted_windows = self.sort_windows(target_title)
        
        cols = 3
        for i, win in enumerate(sorted_windows):
            pixmap = None
            if win.get("thumbnail"):
                img = win["thumbnail"]
                data = img.tobytes("raw", "RGB")
                qimg = QImage(data, img.width, img.height, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qimg)
                
            widget = WindowThumbnailWidget(win["hwnd"], win["title"], pixmap)
            if win["hwnd"] in self.confirmed_hwnds:
                widget.set_confirmed(True)
                
            self.thumbnail_widgets.append(widget)
            row = i // cols
            col = i % cols
            self.grid_layout.addWidget(widget, row, col)
            widget.clicked.connect(self.on_thumbnail_clicked)
            
        self.auto_select_best_match()

    def auto_select_best_match(self):
        target_title = self.target_titles[self.current_title_index]
        app_name = target_title.split("—")[-1].split("-")[-1].strip().lower()
        target_lower = target_title.lower()
        
        best_widget = None
        best_score = 999
        
        for widget in self.thumbnail_widgets:
            if widget.is_confirmed:
                continue
                
            if widget.hwnd == -1:
                score = 0
            else:
                win_title_lower = widget.title.lower()
                score = 10
                if target_lower == win_title_lower:
                    score = -50
                elif target_lower in win_title_lower or win_title_lower in target_lower:
                    score = -40
                elif app_name and app_name in win_title_lower:
                    score = -30
                    
            if score < best_score:
                best_score = score
                best_widget = widget
                
        if best_widget:
            self.on_thumbnail_clicked(best_widget.hwnd)

    def on_thumbnail_clicked(self, hwnd):
        self.current_selected_hwnd = hwnd
        for widget in self.thumbnail_widgets:
            if not widget.is_confirmed:
                widget.set_selected(widget.hwnd == hwnd)
        self.ok_btn.setEnabled(True)

    def on_confirm_clicked(self):
        if self.current_selected_hwnd is None:
            return
            
        current_title = self.target_titles[self.current_title_index]
        self.mapping[current_title] = self.current_selected_hwnd
        
        if self.current_selected_hwnd != -1:
            self.confirmed_hwnds.add(self.current_selected_hwnd)
            
        self.current_title_index += 1
        
        if self.current_title_index >= len(self.target_titles):
            self.accept()
        else:
            self.current_selected_hwnd = None
            self.ok_btn.setEnabled(False)
            self.update_ui_state()

    def get_mapping(self):
        return self.mapping


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


class SettingScreen(QWidget):
    """設定画面への入口。"""

    open_settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("SettingScreen")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(25)

        self.title_label = SubtitleLabel("設定画面", self)
        main_layout.addWidget(self.title_label)

        mode_layout = QVBoxLayout()
        mode_layout.setSpacing(10)

        mode_title = BodyLabel("AI 接続先設定", self)
        font_mode = mode_title.font()
        font_mode.setBold(True)
        mode_title.setFont(font_mode)
        mode_layout.addWidget(mode_title)

        self.local_ai_radio = RadioButton("ローカルAI", self)
        self.cloud_ai_radio = RadioButton("クラウドAI", self)
        
        self.local_ai_radio.setChecked(True)

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.local_ai_radio)
        self.mode_group.addButton(self.cloud_ai_radio)
        
        mode_layout.addWidget(self.local_ai_radio)
        mode_layout.addWidget(self.cloud_ai_radio)
        
        main_layout.addLayout(mode_layout)
        
        server_layout = QGridLayout()
        server_layout.setVerticalSpacing(15)
        server_layout.setHorizontalSpacing(15)
        
        llm_title = BodyLabel("LLMサーバ設定", self)
        font_llm = llm_title.font()
        font_llm.setBold(True)
        llm_title.setFont(font_llm)
        
        self.llm_host_input = LineEdit(self)
        self.llm_host_input.setPlaceholderText("localhost")
        
        server_layout.addWidget(llm_title, 0, 0)
        server_layout.addWidget(self.llm_host_input, 0, 1)
        
        vision_title = BodyLabel("画面解析 サーバ設定", self)
        font_vision = vision_title.font()
        font_vision.setBold(True)
        vision_title.setFont(font_vision)
        
        self.vision_host_input = LineEdit(self)
        self.vision_host_input.setPlaceholderText("localhost")
        
        server_layout.addWidget(vision_title, 1, 0)
        server_layout.addWidget(self.vision_host_input, 1, 1)
        server_layout.setColumnStretch(1, 1)
        
        main_layout.addLayout(server_layout)
        main_layout.addStretch(1)
        
        bottom_layout = QHBoxLayout()
        
        self.test_button = PushButton(FluentIcon.SYNC, "接続テスト", self)
        self.cancel_button = PushButton("キャンセル", self)
        self.save_button = PrimaryPushButton("保存", self)
        
        bottom_layout.addWidget(self.test_button)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.cancel_button)
        bottom_layout.addWidget(self.save_button)
        
        main_layout.addLayout(bottom_layout)
        
        self.test_button.clicked.connect(self.connect_test)
        
    def connect_test(self):
        QMessageBox.information(self, "接続テスト", "接続テスト要求を受け付けました（バックエンド未結合）")

class MainWindow(FluentWindow):
    """QFluentWidgets ベースのメインウィンドウ。"""

    def __init__(self, viewmodel: MainViewModel):
        super().__init__()

        setTheme(Theme.LIGHT)

        self.viewmodel = viewmodel

        self.record_dialog = None
        self.running_dialog = None
        self.progress_dialog = None
        self.settings_dialog = None

        self.setWindowTitle("Macro Manager")
        self.resize(1080, 720)
        self.setMinimumSize(900, 620)

        app_font = QFont("Yu Gothic UI", 10)
        app_font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(app_font)

        self.home_screen = MainScreen(self.viewmodel, self)
        self.settings_screen = SettingScreen(self)
        self.macro_editor_screen = MacroEditorScreen(self)

        self.home_screen.start_record_requested.connect(
            self.open_record_dialog
        )
        self.home_screen.run_macro_requested.connect(
            self.open_macro_editor_for_run
        )
        self.home_screen.edit_macro_requested.connect(
            self.open_macro_editor_for_edit
        )
        self.home_screen.delete_macro_requested.connect(
            self._on_delete_selected_clicked
        )
        self.settings_screen.open_settings_requested.connect(
            self.open_settings_dialog
        )

        self.addSubInterface(
            self.home_screen,
            FluentIcon.HOME,
            "ホーム",
        )
        self.addSubInterface(
            self.settings_screen,
            FluentIcon.SETTING,
            "設定",
        )
        
        self.stackedWidget.addWidget(self.macro_editor_screen)

        self.macro_editor_screen.saved.connect(self._on_macro_saved)
        self.macro_editor_screen.canceled.connect(self._on_macro_edit_canceled)
        self.macro_editor_screen.run_requested.connect(self._on_macro_run_requested)

        self.viewmodel.execution_finished.connect(
            self._on_execution_finished
        )
        self.viewmodel.generation_finished.connect(
            self._on_generation_finished
        )
        self.viewmodel.recording_stopped_by_shortcut.connect(
            self._on_recording_stopped_by_shortcut
        )

    @Slot()
    def _on_delete_selected_clicked(self):
        selected_macro_name = self.viewmodel._selected_macro

        if not selected_macro_name:
            return

        result = QMessageBox.question(
            self,
            "削除の確認",
            f"「{selected_macro_name}」を完全に削除してもよろしいですか？\n"
            "この操作は元に戻せません。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if result == QMessageBox.Yes:
            self.viewmodel.delete_macro(selected_macro_name)

    @Slot()
    def _on_recording_stopped_by_shortcut(self):
        if self.record_dialog:
            self.record_dialog.dialog.close()

        self._on_recording_stopped()

    def open_record_dialog(self):
        try:
            self.viewmodel.start_recording()

            self.record_dialog = RecordDialog(
                self,
                on_stop_callback=self._on_recording_stopped,
            )
            self.record_dialog.show()

            self.hide()

        except Exception as error:
            QMessageBox.critical(
                self,
                "エラー",
                f"記録の開始に失敗しました:\n{error}",
            )

    def _on_recording_stopped(self):
        try:
            self.viewmodel.stop_recording()

            self.progress_dialog = ProgressDialog(
                self.viewmodel,
                self,
            )
            self.progress_dialog.show()

        except Exception as error:
            QMessageBox.critical(
                self,
                "エラー",
                f"記録の停止中にエラーが発生しました:\n{error}",
            )

            self.show()
            self.raise_()
            self.activateWindow()

    def open_macro_editor_for_edit(self):
        macro_name = self.viewmodel._selected_macro
        if not macro_name:
            return
        commands = self.viewmodel.load_macro_commands(macro_name)
        workflow_id = self.viewmodel._macro_id_map.get(macro_name)
        from core.recorder.screen_capturer import get_macros_root
        workflow_dir = get_macros_root() / workflow_id
        
        self.macro_editor_screen.load_macro(macro_name, commands, workflow_dir, is_temporary=False)
        self.navigationInterface.hide()
        self.stackedWidget.setCurrentWidget(self.macro_editor_screen)

    def open_macro_editor_for_run(self):
        macro_name = self.viewmodel._selected_macro
        if not macro_name:
            return
        commands = self.viewmodel.load_macro_commands(macro_name)
        
        workflow_id = self.viewmodel._macro_id_map.get(macro_name)
        from core.recorder.screen_capturer import get_macros_root
        workflow_dir = get_macros_root() / workflow_id
        
        self.navigationInterface.hide()
        self.stackedWidget.setCurrentWidget(self.macro_editor_screen)
        
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        
        title_to_aliases = {}
        for cmd in commands:
            if cmd.get("method") == "activate_window":
                alias = cmd.get("args", {}).get("window_alias")
                title = cmd.get("args", {}).get("window_title")
                if alias and title:
                    if title not in title_to_aliases:
                        title_to_aliases[title] = []
                    title_to_aliases[title].append(alias)
                    
        unique_titles = list(title_to_aliases.keys())
                    
        if unique_titles:
            dialog = WindowMappingDialog(unique_titles, self)
            if dialog.exec() == QDialog.Accepted:
                mapping = dialog.get_mapping()
                for cmd in commands:
                    if cmd.get("method") == "activate_window":
                        title = cmd.get("args", {}).get("window_title")
                        if title and title in mapping:
                            cmd["args"]["mapped_hwnd"] = mapping[title]
            else:
                self._on_macro_edit_canceled()
                return
                
        self.macro_editor_screen.load_macro(macro_name, commands, workflow_dir, is_temporary=True)

    @Slot(str, list)
    def _on_macro_saved(self, macro_name: str, commands: list):
        self.viewmodel.save_macro_commands(macro_name, commands)
        QMessageBox.information(self, "保存完了", "マクロを保存しました。")
        self.navigationInterface.show()
        self.stackedWidget.setCurrentWidget(self.home_screen)

    @Slot()
    def _on_macro_edit_canceled(self):
        self.navigationInterface.show()
        self.stackedWidget.setCurrentWidget(self.home_screen)

    @Slot(list)
    def _on_macro_run_requested(self, temp_commands: list):
        self.navigationInterface.show()
        self.stackedWidget.setCurrentWidget(self.home_screen)
        self.open_running_dialog(temp_commands)

    def open_running_dialog(self, temp_commands=None):
        try:
            self.viewmodel.run_selected_macro(temp_commands)

            self.running_dialog = RunningDialog(
                self,
                on_stop_callback=self._on_emergency_stop_triggered,
            )
            self.running_dialog.show()

            self.hide()

        except Exception as error:
            QMessageBox.critical(
                self,
                "エラー",
                f"実行の開始に失敗しました:\n{error}",
            )

    def _on_emergency_stop_triggered(self):
        try:
            self.viewmodel.trigger_emergency_stop()

        except Exception as error:
            QMessageBox.critical(
                self,
                "エラー",
                f"強制停止中にエラーが発生しました:\n{error}",
            )

    @Slot()
    def _on_execution_finished(self):
        if self.running_dialog:
            self.running_dialog.close_dialog()
            self.running_dialog = None

        self.show()
        self.raise_()
        self.activateWindow()

    @Slot(bool, str)
    def _on_generation_finished(self, success: bool, message: str):
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        if not success and message != "キャンセルされました":
            QMessageBox.warning(
                self,
                "マクロ生成エラー",
                f"マクロの生成に失敗しました:\n{message}",
            )

        self.show()
        self.raise_()
        self.activateWindow()

    def open_settings_dialog(self):
        self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.exec()