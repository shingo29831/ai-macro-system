# src/ui/viewmodels/main_viewmodel.py
# @role: メインウィンドウのUI状態を管理し、非同期スレッドを用いてビューからのアクションをビジネスロジック(Core層)へ安全に中継・結合するViewModel層。

import logging
import os
import json
import shutil
import threading
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Slot, Qt
from models.data_types import MacroSummary, AppConfig
from core.recorder import os_hook
from core.generator import log_integrator
from core.executor import runner
from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)

class MainViewModel(QObject):
    macros_updated = Signal(list)
    can_run_changed = Signal(bool)
    execution_finished = Signal()
    generation_progress = Signal(int, str)
    generation_finished = Signal(bool, str)
    recording_stopped_by_shortcut = Signal()
    recording_error = Signal(str)
    execution_error = Signal(str)
    
    _internal_shortcut_signal = Signal()
    _internal_status_signal = Signal(str, bool) # 自己修復UI通知用シグナル

    def __init__(self):
        super().__init__()
        self._selected_macro: str | None = None
        self._macro_id_map: dict[str, str] = {}
        self._cancel_requested = False
        self._is_stopping = False
        
        self._internal_shortcut_signal.connect(self._on_internal_shortcut, Qt.QueuedConnection)
        self._internal_status_signal.connect(self._on_internal_status, Qt.QueuedConnection)
        os_hook.set_shortcut_stop_callback(self._trigger_shortcut_signal)

    def _trigger_shortcut_signal(self):
        self._internal_shortcut_signal.emit()

    @Slot()
    def _on_internal_shortcut(self):
        self.recording_stopped_by_shortcut.emit()

    @Slot(str, bool)
    def _on_internal_status(self, text: str, is_healing: bool):
        try:
            from ui.views.running_dialog import RunningDialog
            RunningDialog.set_status(text, is_healing)
        except Exception as e:
            logger.error(f"Failed to update running dialog status: {e}")

    def load_macros(self):
        import re
        from datetime import datetime

        try:
            from core.recorder.screen_capturer import get_macros_root
            macros_root = get_macros_root()
        except Exception as e:
            logger.warning(f"Failed to call get_macros_root, falling back to relative path: {e}")
            macros_root = Path(__file__).resolve().parent / "../../../macros"
        
        macros = []
        self._macro_id_map.clear()

        # 数値混在文字列を人間が自然に感じる順序(1, 2, 10)で整列
        def natural_sort_key(s: str):
            return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', str(s))]
        
        try:
            if macros_root.exists() and macros_root.is_dir():
                macro_dirs = [d for d in macros_root.glob("wf_*") if d.is_dir()]
                macro_dirs.sort(key=lambda d: natural_sort_key(d.name))

                for wf_dir in macro_dirs:
                    workflow_id = wf_dir.name
                    # 非ITユーザーが直感的に識別できるよう「マクロ 1」形式で表記
                    if workflow_id.startswith("wf_") and workflow_id[3:].isdigit():
                        macro_name = f"マクロ {workflow_id[3:]}"
                    else:
                        macro_name = f"マクロ {workflow_id}"
                    
                    if macro_name in self._macro_id_map:
                        macro_name = f"マクロ {workflow_id}"

                    stat = wf_dir.stat()
                    created_ts = getattr(stat, 'st_birthtime', stat.st_ctime)
                    updated_ts = stat.st_mtime

                    # フォルダ内重要ファイルの最新更新日時を反映
                    for f_name in ("executable_macro.json", "workflow.json", "integrated.json"):
                        target_file = wf_dir / f_name
                        if target_file.exists():
                            try:
                                f_mtime = target_file.stat().st_mtime
                                if f_mtime > updated_ts:
                                    updated_ts = f_mtime
                            except OSError:
                                pass

                    created_at_str = datetime.fromtimestamp(created_ts).strftime("%Y/%m/%d %H:%M")
                    updated_at_str = datetime.fromtimestamp(updated_ts).strftime("%Y/%m/%d %H:%M")

                    status = 'success'
                    status_text = '待機中'
                    heals = '0回'
                    heal_level = 'none'
                    last_run = '-'
                    
                    self._macro_id_map[macro_name] = workflow_id
                    
                    macros.append(MacroSummary(
                        name=macro_name,
                        status=status,
                        status_text=status_text,
                        heals=heals,
                        heal_level=heal_level,
                        last_run=last_run,
                        created_at=created_at_str,
                        updated_at=updated_at_str,
                        created_timestamp=created_ts,
                        updated_timestamp=updated_ts
                    ))
            
            # 初期一覧は直近で作業したものが上位に来る更新順降順
            macros.sort(key=lambda m: m.updated_timestamp, reverse=True)
            self.macros_updated.emit(macros)
            logger.info(f"Successfully loaded {len(macros)} macros from storage.")
        except Exception as e:
            logger.error(f'Failed to load macros from directory structure: {e}')
            raise

    def load_macro_commands(self, macro_name: str) -> list[dict]:
        workflow_id = self._macro_id_map.get(macro_name)
        if not workflow_id:
            raise ValueError(f"Macro not found: {macro_name}")
            
        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        macro_file = macros_root / workflow_id / "executable_macro.json"
        
        if not macro_file.exists():
            return []
            
        with open(macro_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("commands", [])

    def save_macro_commands(self, macro_name: str, commands: list[dict]):
        workflow_id = self._macro_id_map.get(macro_name)
        if not workflow_id:
            raise ValueError(f"Macro not found: {macro_name}")
            
        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        macro_file = macros_root / workflow_id / "executable_macro.json"
        
        if macro_file.exists():
            with open(macro_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"macro_id": workflow_id, "target_application": "auto_generated"}
            
        data["commands"] = commands
        
        with open(macro_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @Slot()
    def start_recording(self):
        try:
            logger.info("Starting macro recording...")
            os_hook.start_recording()
        except Exception as e:
            err_msg = f"記録開始に失敗しました: {e}"
            logger.error(err_msg, exc_info=True)
            self.recording_error.emit(err_msg)
            raise

    @Slot()
    def cancel_generation(self):
        self._cancel_requested = True
        logger.info("Cancel generation requested by user.")

    @Slot()
    def stop_recording(self):
        if getattr(self, '_is_stopping', False):
            logger.warning("既に停止処理が進行中です。多重実行をブロックしました。")
            return
            
        try:
            self._is_stopping = True
            self._cancel_requested = False
            logger.info("Stopping macro recording (Running in background thread to prevent UI freeze)...")
            
            def background_stop_task():
                try:
                    os_hook.stop_recording()
                    
                    workflow_id = os_hook.get_current_workflow_id()
                    
                    if not workflow_id:
                        try:
                            from core.recorder.screen_capturer import get_macros_root
                            macros_root = get_macros_root()
                            wf_dirs = sorted([d for d in macros_root.glob("wf_*") if d.is_dir()], key=lambda x: x.stat().st_mtime)
                            if wf_dirs:
                                workflow_id = wf_dirs[-1].name
                        except Exception as e:
                            logger.error(f"Failed to fallback workflow_id: {e}")

                    if workflow_id:
                        app_config = ConfigManager.load_config()
                        
                        logger.info(f"Kicking background macro generation workflow for ID: {workflow_id}")
                        
                        def progress_cb(prog: int, msg: str):
                            self.generation_progress.emit(prog, msg)

                        def check_cancel() -> bool:
                            return self._cancel_requested
                        
                        log_integrator.generate_macro_workflow(
                            workflow_id, 
                            app_config, 
                            progress_callback=progress_cb, 
                            check_cancel_callback=check_cancel
                        )
                        
                        logger.info(f"Background macro generation successfully completed for ID: {workflow_id}")
                        self.load_macros()
                        self.generation_finished.emit(True, "")
                            
                    else:
                        msg = "Recording stopped, but target workflow_id could not be resolved from os_hook."
                        logger.warning(msg)
                        self.load_macros()
                        self.generation_finished.emit(False, msg)
                        
                except InterruptedError:
                    logger.warning(f"Background macro generation cancelled.")
                    self.generation_finished.emit(False, "キャンセルされました")
                except Exception as gen_err:
                    err_msg = str(gen_err)
                    logger.error(f"Unhandled exception during background macro generation: {err_msg}")
                    self.generation_finished.emit(False, err_msg)
                finally:
                    self._is_stopping = False
                    
            threading.Thread(target=background_stop_task, daemon=True).start()
            
        except Exception as e:
            self._is_stopping = False
            logger.error(f"Failed to initiate stop recording task: {e}")
            raise

    @Slot()
    def trigger_emergency_stop(self):
        try:
            logger.warning('Emergency stop triggered by user.')
            runner.stop_workflow()
        except Exception as e:
            logger.error(f"Failed to trigger emergency stop: {e}")

    @Slot(str)
    def select_macro(self, macro_name: str):
        self._selected_macro = macro_name if macro_name else None
        self.can_run_changed.emit(self._selected_macro is not None)

    @Slot(list)
    def run_selected_macro(self, temp_commands: list[dict] = None):
        if not self._selected_macro:
            logger.warning('Run requested but no macro is selected.')
            return
        
        workflow_id = self._macro_id_map.get(self._selected_macro)
        if not workflow_id:
            logger.error(f"Failed to match workflow_id for the selected macro name: {self._selected_macro}")
            return
        
        try:
            logger.info(f'Executing macro: {self._selected_macro} (Resolved ID: {workflow_id})')
            
            app_config = ConfigManager.load_config()
            
            def status_cb(text: str, is_healing: bool):
                self._internal_status_signal.emit(text, is_healing)
            
            def background_execution(cfg: AppConfig, cmds: list[dict] = None):
                try:
                    runner.run_workflow(workflow_id, cfg, status_callback=status_cb, temp_commands=cmds)
                    logger.info(f"Macro execution finished successfully for ID: {workflow_id}")
                except runner.WorkflowStoppedException as stop_err:
                    logger.warning(f"Macro execution stopped by user: {stop_err}")
                except Exception as exec_err:
                    import traceback
                    tb_str = traceback.format_exc()
                    err_msg = f"{type(exec_err).__name__}: {exec_err}"
                    logger.error(f"Exception occurred during pipeline execution for {workflow_id}:\n{tb_str}")
                    status_cb(f"エラー: {err_msg}", True)
                    self.execution_error.emit(err_msg)
                finally:
                    self.load_macros()
                    self.execution_finished.emit()
            
            exec_thread = threading.Thread(target=background_execution, args=(app_config, temp_commands), daemon=True)
            exec_thread.start()
            
        except Exception as e:
            logger.error(f'Execution handling failed for {self._selected_macro}: {e}')
            raise

    @Slot(str)
    def delete_macro(self, macro_name: str):
        if not macro_name:
            return
        self.delete_macros([macro_name])

    @Slot(list)
    def delete_macros(self, macro_names: list[str]):
        """複数のマクロを一括で完全に削除する。"""
        if not macro_names:
            return

        try:
            from core.recorder.screen_capturer import get_macros_root
            macros_root = get_macros_root()
        except Exception as e:
            logger.warning(f"Failed to call get_macros_root: {e}")
            macros_root = Path(__file__).resolve().parent / "../../../macros"

        deleted_count = 0
        for macro_name in macro_names:
            workflow_id = self._macro_id_map.get(macro_name)
            if not workflow_id:
                logger.warning(f'Delete requested, but tracking map does not contain macro: {macro_name}')
                continue

            try:
                target_dir = macros_root / workflow_id
                if target_dir.exists() and target_dir.is_dir():
                    shutil.rmtree(target_dir)
                    deleted_count += 1
                    logger.info(f"Physically deleted macro directory tree: {target_dir}")
            except Exception as e:
                logger.error(f'Failed to delete macro {macro_name} from workspace: {e}')

        logger.info(f"Successfully deleted {deleted_count} macros from storage.")
        self.load_macros()