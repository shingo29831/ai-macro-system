"""Module: @role: メインウィンドウに紐づく各種フロートダイアログ（録画、実行、進捗、設定、エラー、確認）のライフサイクルと表示制御を担当する。"""

from typing import Any, Callable, Dict, List, Optional
from PySide6.QtWidgets import QMessageBox, QWidget

from ui.viewmodels.main_viewmodel import MainViewModel
from ui.views.error_dialog import ErrorDialog
from ui.views.progress_dialog import ProgressDialog
from ui.views.record_dialog import RecordDialog
from ui.views.running_dialog import RunningDialog
from ui.views.settings_dialog import SettingsDialog


class MainDialogManager:
    """フロートダイアログおよびモーダルダイアログの生成・表示・破棄を管理するコントローラー"""

    def __init__(self, main_window: QWidget, viewmodel: MainViewModel) -> None:
        self.main_window = main_window
        self.viewmodel = viewmodel
        self.record_dialog: Optional[RecordDialog] = None
        self.running_dialog: Optional[RunningDialog] = None
        self.progress_dialog: Optional[ProgressDialog] = None
        self.settings_dialog: Optional[SettingsDialog] = None

    def open_record_dialog(self, on_stop_callback: Callable[[], None]) -> None:
        try:
            self.viewmodel.start_recording()
            self.record_dialog = RecordDialog(self.main_window, on_stop_callback=on_stop_callback)
            self.record_dialog.show()
            self.main_window.hide()
        except Exception as error:
            QMessageBox.critical(self.main_window, "エラー", f"記録の開始に失敗しました:\n{error}")

    def close_record_dialog_if_open(self) -> None:
        if self.record_dialog:
            self.record_dialog.dialog.close()
            self.record_dialog = None

    def show_progress_dialog(self) -> None:
        try:
            self.viewmodel.stop_recording()
            self.progress_dialog = ProgressDialog(self.viewmodel, self.main_window)
            self.progress_dialog.show()
        except Exception as error:
            QMessageBox.critical(self.main_window, "エラー", f"記録の停止中にエラーが発生しました:\n{error}")
            self.restore_main_window()

    def close_progress_dialog_if_open(self) -> None:
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

    def open_running_dialog(
        self,
        temp_commands: Optional[List[Dict[str, Any]]],
        on_emergency_stop: Callable[[], None]
    ) -> None:
        try:
            self.viewmodel.run_selected_macro(temp_commands)
            self.running_dialog = RunningDialog(self.main_window, on_stop_callback=on_emergency_stop)
            self.running_dialog.show()
            self.main_window.hide()
        except Exception as error:
            QMessageBox.critical(self.main_window, "エラー", f"実行の開始に失敗しました:\n{error}")

    def close_running_dialog_if_open(self) -> None:
        if self.running_dialog:
            self.running_dialog.close_dialog()
            self.running_dialog = None

    def restore_main_window(self) -> None:
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def show_error_dialog(self, title: str, summary: str, detail: str) -> None:
        dialog = ErrorDialog(title=title, summary=summary, detail=detail, parent=self.main_window)
        dialog.exec()

    def open_settings_dialog(self) -> None:
        self.settings_dialog = SettingsDialog(self.main_window)
        self.settings_dialog.exec()

    def confirm_delete_single(self, macro_name: str) -> bool:
        result = QMessageBox.question(
            self.main_window,
            "削除の確認",
            f"「{macro_name}」を完全に削除してもよろしいですか？\nこの操作は元に戻せません。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return result == QMessageBox.Yes

    def confirm_delete_multiple(self, macro_names: List[str]) -> bool:
        count = len(macro_names)
        if count == 1:
            msg = f"「{macro_names[0]}」を完全に削除してもよろしいですか？\nこの操作は元に戻せません。"
        else:
            preview = "、".join(f"「{name}」" for name in macro_names[:3])
            if count > 3:
                preview += f" ほか計{count}件"
            msg = f"選択した {count} 件のマクロ（{preview}）を完全に削除してもよろしいですか？\nこの操作は元に戻せません。"

        result = QMessageBox.question(
            self.main_window,
            "一括削除の確認",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return result == QMessageBox.Yes
