"""Module: @role: FluentWindowを継承し、ナビゲーションメニュー、各画面（ホーム、設定、エディタ）の切り替え、およびダイアログ制御を統括する最上位ウィンドウ。"""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox
from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    Theme,
    setTheme,
)

from ui.viewmodels.main_viewmodel import MainViewModel
from ui.views.main_dialog_manager import MainDialogManager
from ui.views.main_screen import MainScreen
from ui.views.macro_editor.macro_editor_dialog import MacroEditorScreen
from ui.views.setting_screen import SettingScreen
from ui.views.window_mapping_dialog import WindowMappingDialog, WindowThumbnailWidget
from ui.views.window_mapping_helper import resolve_window_mapping

__all__ = [
    "MainWindow",
    "MainScreen",
    "SettingScreen",
    "WindowMappingDialog",
    "WindowThumbnailWidget",
]

try:
    from src.core.recorder.macro_path_manager import get_macros_root
except ImportError:
    from core.recorder.macro_path_manager import get_macros_root


class MainWindow(FluentWindow):
    def __init__(self, viewmodel: MainViewModel) -> None:
        super().__init__()

        setTheme(Theme.LIGHT)

        self.viewmodel = viewmodel
        self.dialog_manager = MainDialogManager(self, self.viewmodel)

        self.setWindowTitle("Macro Manager")
        self.resize(1080, 720)
        self.setMinimumSize(900, 620)

        app_font = QFont("Yu Gothic UI", 10)
        app_font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(app_font)

        self.home_screen = MainScreen(self.viewmodel, self)
        self.settings_screen = SettingScreen(self)
        self.macro_editor_screen = MacroEditorScreen(self)

        self.home_screen.start_record_requested.connect(self.open_record_dialog)
        self.home_screen.run_macro_requested.connect(self.open_macro_editor_for_run)
        self.home_screen.edit_macro_requested.connect(self.open_macro_editor_for_edit)
        self.home_screen.delete_macro_requested.connect(self._on_delete_selected_clicked)
        self.home_screen.delete_macros_requested.connect(self._on_delete_macros_requested)
        self.settings_screen.open_settings_requested.connect(self.open_settings_dialog)

        self.addSubInterface(self.home_screen, FluentIcon.HOME, "ホーム")
        self.addSubInterface(self.settings_screen, FluentIcon.SETTING, "設定")

        self.stackedWidget.addWidget(self.macro_editor_screen)

        self.macro_editor_screen.saved.connect(self._on_macro_saved)
        self.macro_editor_screen.canceled.connect(self._on_macro_edit_canceled)
        self.macro_editor_screen.run_requested.connect(self._on_macro_run_requested)

        self.viewmodel.execution_finished.connect(self._on_execution_finished)
        self.viewmodel.generation_finished.connect(self._on_generation_finished)
        self.viewmodel.recording_stopped_by_shortcut.connect(self._on_recording_stopped_by_shortcut)
        self.viewmodel.execution_error.connect(self._on_execution_error)
        self.viewmodel.recording_error.connect(self._on_recording_error)

    @property
    def record_dialog(self) -> Any:
        return self.dialog_manager.record_dialog

    @property
    def running_dialog(self) -> Any:
        return self.dialog_manager.running_dialog

    @property
    def progress_dialog(self) -> Any:
        return self.dialog_manager.progress_dialog

    @property
    def settings_dialog(self) -> Any:
        return self.dialog_manager.settings_dialog

    @Slot()
    def _on_delete_selected_clicked(self) -> None:
        selected_macro_name = self.viewmodel._selected_macro
        if not selected_macro_name:
            return
        if self.dialog_manager.confirm_delete_single(selected_macro_name):
            self.viewmodel.delete_macro(selected_macro_name)

    @Slot(list)
    def _on_delete_macros_requested(self, macro_names: List[str]) -> None:
        if not macro_names:
            return
        if self.dialog_manager.confirm_delete_multiple(macro_names):
            self.viewmodel.delete_macros(macro_names)

    @Slot()
    def _on_recording_stopped_by_shortcut(self) -> None:
        self.dialog_manager.close_record_dialog_if_open()
        self._on_recording_stopped()

    def open_record_dialog(self) -> None:
        self.dialog_manager.open_record_dialog(on_stop_callback=self._on_recording_stopped)

    def _on_recording_stopped(self) -> None:
        self.dialog_manager.show_progress_dialog()

    def open_macro_editor_for_review(self, macro_name: str) -> None:
        self.viewmodel.select_macro(macro_name)
        commands = self.viewmodel.load_macro_commands(macro_name)
        workflow_id = self.viewmodel._macro_id_map.get(macro_name) or macro_name
        workflow_dir = get_macros_root() / workflow_id

        self.macro_editor_screen.load_macro(macro_name, commands, workflow_dir, is_temporary=False, is_review=True)
        self.navigationInterface.hide()
        self.stackedWidget.setCurrentWidget(self.macro_editor_screen)

    def open_macro_editor_for_edit(self) -> None:
        macro_name = self.viewmodel._selected_macro
        if not macro_name:
            return
        commands = self.viewmodel.load_macro_commands(macro_name)
        workflow_id = self.viewmodel._macro_id_map.get(macro_name) or macro_name
        workflow_dir = get_macros_root() / workflow_id

        self.macro_editor_screen.load_macro(macro_name, commands, workflow_dir, is_temporary=False)
        self.navigationInterface.hide()
        self.stackedWidget.setCurrentWidget(self.macro_editor_screen)

    def open_macro_editor_for_run(self) -> None:
        macro_name = self.viewmodel._selected_macro
        if not macro_name:
            return
        commands = self.viewmodel.load_macro_commands(macro_name)
        workflow_id = self.viewmodel._macro_id_map.get(macro_name) or macro_name
        workflow_dir = get_macros_root() / workflow_id

        self.navigationInterface.hide()
        self.stackedWidget.setCurrentWidget(self.macro_editor_screen)
        QApplication.processEvents()

        if not resolve_window_mapping(self, commands):
            self._on_macro_edit_canceled()
            return

        self.macro_editor_screen.load_macro(macro_name, commands, workflow_dir, is_temporary=True)

    @Slot(str, list)
    def _on_macro_saved(self, macro_name: str, commands: List[Dict[str, Any]]) -> None:
        self.viewmodel.save_macro_commands(macro_name, commands)
        QMessageBox.information(self, "保存完了", "マクロを保存しました。")
        self.navigationInterface.show()
        self.stackedWidget.setCurrentWidget(self.home_screen)

    @Slot()
    def _on_macro_edit_canceled(self) -> None:
        self.navigationInterface.show()
        self.stackedWidget.setCurrentWidget(self.home_screen)

    @Slot(list)
    def _on_macro_run_requested(self, temp_commands: List[Dict[str, Any]]) -> None:
        self.navigationInterface.show()
        self.stackedWidget.setCurrentWidget(self.home_screen)
        self.open_running_dialog(temp_commands)

    def open_running_dialog(self, temp_commands: Optional[List[Dict[str, Any]]] = None) -> None:
        self.dialog_manager.open_running_dialog(
            temp_commands=temp_commands,
            on_emergency_stop=self._on_emergency_stop_triggered,
        )

    def _on_emergency_stop_triggered(self) -> None:
        try:
            self.viewmodel.trigger_emergency_stop()
        except Exception as error:
            QMessageBox.critical(self, "エラー", f"強制停止中にエラーが発生しました:\n{error}")

    @Slot(str)
    def _on_execution_error(self, error_message: str) -> None:
        self.dialog_manager.close_running_dialog_if_open()
        self.dialog_manager.restore_main_window()
        self.dialog_manager.show_error_dialog(
            title="マクロ実行エラー",
            summary="マクロの実行中にエラー（またはフリーズ）が発生したため、処理を強制終了しました。",
            detail=error_message,
        )

    @Slot(str)
    def _on_recording_error(self, error_message: str) -> None:
        self.dialog_manager.close_record_dialog_if_open()
        self.dialog_manager.restore_main_window()
        self.dialog_manager.show_error_dialog(
            title="マクロ記録エラー",
            summary="マクロの記録中にエラーが発生したため、記録を強制終了しました。",
            detail=error_message,
        )

    @Slot()
    def _on_execution_finished(self) -> None:
        self.dialog_manager.close_running_dialog_if_open()
        self.dialog_manager.restore_main_window()

    @Slot(bool, str, str)
    def _on_generation_finished(self, success: bool, message: str, macro_name: str = "") -> None:
        self.dialog_manager.close_progress_dialog_if_open()
        self.dialog_manager.restore_main_window()

        if not success:
            if message != "キャンセルされました":
                QMessageBox.warning(self, "マクロ生成エラー", f"マクロの生成に失敗しました:\n{message}")
            return

        # Why: 記録完了後に対話的なステップ精査・取捨選択エディタへシームレスに誘導
        target_name = macro_name or self.viewmodel._selected_macro
        if target_name:
            self.open_macro_editor_for_review(target_name)

    def open_settings_dialog(self) -> None:
        """設定ダイアログを開く。"""
        self.dialog_manager.open_settings_dialog()
