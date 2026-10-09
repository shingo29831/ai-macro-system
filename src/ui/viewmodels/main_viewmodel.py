"""Module: @role: メインウィンドウのUI状態を管理し、非同期スレッドを用いてビューからのアクションをビジネスロジック(Core層)へ安全に中継・結合するViewModel層。"""

from typing import Any, Dict, List, Optional
import logging

from PySide6.QtCore import QObject, Qt, Signal, Slot

from core.executor import runner
from core.recorder import os_hook
from ui.viewmodels.macro_storage import (
    delete_macro_folders,
    list_macro_summaries,
    load_executable_commands,
    save_executable_commands,
)
from ui.viewmodels.macro_task_runner import MacroTaskRunner

logger = logging.getLogger(__name__)


class MainViewModel(QObject):
    macros_updated = Signal(list)
    can_run_changed = Signal(bool)
    execution_finished = Signal()
    generation_progress = Signal(int, str)
    generation_finished = Signal(bool, str, str)
    recording_stopped_by_shortcut = Signal()
    recording_error = Signal(str)
    execution_error = Signal(str)

    _internal_shortcut_signal = Signal()
    _internal_status_signal = Signal(str, bool)  # 自己修復UI通知用シグナル

    def __init__(self) -> None:
        super().__init__()
        self._selected_macro: Optional[str] = None
        self._macro_id_map: Dict[str, str] = {}
        self._cancel_requested: bool = False
        self._is_stopping: bool = False

        self._internal_shortcut_signal.connect(self._on_internal_shortcut, Qt.QueuedConnection)
        self._internal_status_signal.connect(self._on_internal_status, Qt.QueuedConnection)
        os_hook.set_shortcut_stop_callback(self._trigger_shortcut_signal)

    def _trigger_shortcut_signal(self) -> None:
        self._internal_shortcut_signal.emit()

    @Slot()
    def _on_internal_shortcut(self) -> None:
        self.recording_stopped_by_shortcut.emit()

    @Slot(str, bool)
    def _on_internal_status(self, text: str, is_healing: bool) -> None:
        try:
            from ui.views.running_dialog import RunningDialog
            RunningDialog.set_status(text, is_healing)
        except Exception as e:
            logger.error(f"Failed to update running dialog status: {e}")

    def load_macros(self) -> None:
        try:
            macros, self._macro_id_map = list_macro_summaries()
            self.macros_updated.emit(macros)
            logger.info(f"Successfully loaded {len(macros)} macros from storage.")
        except Exception as e:
            logger.error(f"Failed to load macros from directory structure: {e}")
            raise

    def load_macro_commands(self, macro_name: str) -> List[Dict[str, Any]]:
        workflow_id = self._macro_id_map.get(macro_name)
        if not workflow_id:
            raise ValueError(f"Macro not found: {macro_name}")
        return load_executable_commands(workflow_id)

    def save_macro_commands(self, macro_name: str, commands: List[Dict[str, Any]]) -> None:
        workflow_id = self._macro_id_map.get(macro_name)
        if not workflow_id:
            raise ValueError(f"Macro not found: {macro_name}")
        save_executable_commands(workflow_id, commands)

    @Slot()
    def start_recording(self) -> None:
        try:
            logger.info("Starting macro recording...")
            os_hook.start_recording()
        except Exception as e:
            err_msg = f"記録開始に失敗しました: {e}"
            logger.error(err_msg, exc_info=True)
            self.recording_error.emit(err_msg)
            raise

    @Slot()
    def cancel_generation(self) -> None:
        self._cancel_requested = True
        logger.info("Cancel generation requested by user.")

    @Slot()
    def stop_recording(self) -> None:
        if self._is_stopping:
            logger.warning("既に停止処理が進行中です。多重実行をブロックしました。")
            return

        try:
            self._is_stopping = True
            self._cancel_requested = False
            logger.info("Stopping macro recording (Running in background thread to prevent UI freeze)...")

            def on_finished(success: bool, err_msg: str, workflow_id: str) -> None:
                self._is_stopping = False
                self.load_macros()
                if success and workflow_id:
                    # Why: 生成直後のステップ精査・レビューUIを自動起動するためマクロ名を解決して通知
                    resolved_name = next(
                        (name for name, wid in self._macro_id_map.items() if wid == workflow_id),
                        f"マクロ {workflow_id}"
                    )
                    self._selected_macro = resolved_name
                    self.generation_finished.emit(True, "", resolved_name)
                else:
                    self.generation_finished.emit(False, err_msg, "")

            def on_error(detail_err: str) -> None:
                self._is_stopping = False
                self.recording_error.emit(detail_err)

            MacroTaskRunner.start_generation_task(
                is_cancel_requested=lambda: self._cancel_requested,
                on_progress=lambda prog, msg: self.generation_progress.emit(prog, msg),
                on_finished=on_finished,
                on_error=on_error,
            )

        except Exception as e:
            self._is_stopping = False
            logger.error(f"Failed to initiate stop recording task: {e}")
            raise

    @Slot()
    def trigger_emergency_stop(self) -> None:
        try:
            logger.warning("Emergency stop triggered by user.")
            runner.stop_workflow()
        except Exception as e:
            logger.error(f"Failed to trigger emergency stop: {e}")

    @Slot(str)
    def select_macro(self, macro_name: str) -> None:
        self._selected_macro = macro_name if macro_name else None
        self.can_run_changed.emit(self._selected_macro is not None)

    @Slot(list)
    def run_selected_macro(self, temp_commands: Optional[List[Dict[str, Any]]] = None) -> None:
        if not self._selected_macro:
            logger.warning("Run requested but no macro is selected.")
            return

        workflow_id = self._macro_id_map.get(self._selected_macro)
        if not workflow_id:
            logger.error(f"Failed to match workflow_id for the selected macro name: {self._selected_macro}")
            return

        try:
            logger.info(f"Executing macro: {self._selected_macro} (Resolved ID: {workflow_id})")

            def on_finished() -> None:
                self.load_macros()
                self.execution_finished.emit()

            MacroTaskRunner.start_execution_task(
                workflow_id=workflow_id,
                commands=temp_commands,
                status_callback=lambda text, is_healing: self._internal_status_signal.emit(text, is_healing),
                on_finished=on_finished,
                on_error=lambda err_msg: self.execution_error.emit(err_msg),
            )

        except Exception as e:
            logger.error(f"Execution handling failed for {self._selected_macro}: {e}")
            raise

    @Slot(str)
    def delete_macro(self, macro_name: str) -> None:
        if not macro_name:
            return
        self.delete_macros([macro_name])

    @Slot(list)
    def delete_macros(self, macro_names: List[str]) -> None:
        """複数のマクロを一括で完全に削除する。"""
        if not macro_names:
            return

        workflow_ids = [self._macro_id_map[name] for name in macro_names if name in self._macro_id_map]
        deleted_count = delete_macro_folders(workflow_ids)
        logger.info(f"Successfully deleted {deleted_count} macros from storage.")
        self.load_macros()