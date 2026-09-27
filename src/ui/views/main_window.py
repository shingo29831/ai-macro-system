"""Module: @role: FluentWindowを継承し、ナビゲーションメニュー、各画面（ホーム、設定、エディタ）の切り替え、およびダイアログ制御を統括する最上位ウィンドウ。"""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMessageBox,
)
from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    Theme,
    setTheme,
)

from ui.viewmodels.main_viewmodel import MainViewModel
from ui.views.progress_dialog import ProgressDialog
from ui.views.record_dialog import RecordDialog
from ui.views.running_dialog import RunningDialog
from ui.views.settings_dialog import SettingsDialog
from ui.views.macro_editor.macro_editor_dialog import MacroEditorScreen
from ui.views.window_mapping_dialog import WindowMappingDialog, WindowThumbnailWidget
from ui.views.main_screen import MainScreen
from ui.views.setting_screen import SettingScreen

__all__ = [
    "MainWindow",
    "MainScreen",
    "SettingScreen",
    "WindowMappingDialog",
    "WindowThumbnailWidget",
]

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
        workflow_dir = get_macros_root() / workflow_id

        self.navigationInterface.hide()
        self.stackedWidget.setCurrentWidget(self.macro_editor_screen)

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
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from qfluentwidgets import FluentIcon, FluentWindow, Theme, setTheme
from ui.views.main_screen import MainScreen
        # UIイベントを処理して画面遷移とウィジェット描画を確定させる
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
