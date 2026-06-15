# @role: メインウィンドウのUI状態を管理し、非同期スレッドを用いてビューからのアクションをビジネスロジック(Core層)へ安全に中継・結合するViewModel層。

import logging
import os
import json
import shutil
import threading
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Slot
from models.data_types import MacroSummary
from core.recorder import os_hook
from core.generator import log_integrator
from core.executor import runner

logger = logging.getLogger(__name__)

class MainViewModel(QObject):
    macros_updated = Signal(list)
    status_changed = Signal(str, str)
    can_run_changed = Signal(bool)
    execution_finished = Signal()

    def __init__(self):
        super().__init__()
        self._states = ['idle', 'recording', 'running']
        self._state_labels = {'idle': '待機中', 'recording': '記録中', 'running': '実行中'}
        self._current_state_index = 0
        self._selected_macro: str | None = None
        self._macro_id_map: dict[str, str] = {}

    def load_macros(self):
        try:
            from core.recorder.screen_capturer import get_macros_root
            macros_root = get_macros_root()
        except Exception as e:
            logger.warning(f"Failed to call get_macros_root, falling back to relative path: {e}")
            macros_root = Path(__file__).resolve().parent / "../../../macros"
        
        macros = []
        self._macro_id_map.clear()
        
        try:
            if macros_root.exists() and macros_root.is_dir():
                for wf_dir in sorted(macros_root.glob("wf_*")):
                    if not wf_dir.is_dir():
                        continue
                    
                    workflow_id = wf_dir.name
                    macro_name = f"マクロ {workflow_id}"
                    
                    status = 'success'
                    status_text = '待機中'
                    heals = '0回'
                    heal_level = 'none'
                    last_run = '-'
                    
                    integrated_json = wf_dir / "integrated.json"
                    if integrated_json.exists():
                        try:
                            with open(integrated_json, "r", encoding="utf-8") as f:
                                pass
                        except Exception as json_err:
                            logger.warning(f"Failed to parse integrated.json for metadata in {workflow_id}: {json_err}")
                    
                    self._macro_id_map[macro_name] = workflow_id
                    
                    macros.append(MacroSummary(
                        name=macro_name,
                        status=status,
                        status_text=status_text,
                        heals=heals,
                        heal_level=heal_level,
                        last_run=last_run
                    ))
            
            self.macros_updated.emit(macros)
            logger.info(f"Successfully loaded {len(macros)} macros from storage.")
        except Exception as e:
            logger.error(f'Failed to load macros from directory structure: {e}')
            raise

    @Slot()
    def toggle_status(self):
        self._current_state_index = (self._current_state_index + 1) % len(self._states)
        state = self._states[self._current_state_index]
        self.status_changed.emit(state, self._state_labels[state])

    @Slot()
    def start_recording(self):
        try:
            logger.info("Starting macro recording...")
            os_hook.start_recording()
            
            self._current_state_index = self._states.index('recording')
            self.status_changed.emit('recording', self._state_labels['recording'])
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            raise

    @Slot()
    def stop_recording(self):
        try:
            logger.info("Stopping macro recording...")
            os_hook.stop_recording()
            
            workflow_id = None
            if os_hook._recording_dirs and "macro_name" in os_hook._recording_dirs:
                workflow_id = os_hook._recording_dirs["macro_name"]
            
            self._current_state_index = self._states.index('idle')
            self.status_changed.emit('idle', self._state_labels['idle'])
            
            if workflow_id:
                def background_generation():
                    try:
                        logger.info(f"Kicking background macro generation workflow for ID: {workflow_id}")
                        log_integrator.generate_macro_workflow(workflow_id)
                        logger.info(f"Background macro generation successfully completed for ID: {workflow_id}")
                        self.load_macros()
                    except Exception as gen_err:
                        logger.error(f"Unhandled exception during background macro generation for {workflow_id}: {gen_err}")
                
                gen_thread = threading.Thread(target=background_generation, daemon=True)
                gen_thread.start()
            else:
                logger.warning("Recording stopped, but target workflow_id could not be resolved from os_hook.")
                self.load_macros()
                
        except Exception as e:
            logger.error(f"Failed to stop recording cleanly: {e}")
            raise

    @Slot()
    def trigger_emergency_stop(self):
        """Executor層に対して緊急停止（キルスイッチ）を発動する"""
        try:
            logger.warning('Emergency stop triggered by user.')
            runner.stop_workflow()
        except Exception as e:
            logger.error(f"Failed to trigger emergency stop: {e}")

    @Slot(str)
    def select_macro(self, macro_name: str):
        self._selected_macro = macro_name if macro_name else None
        self.can_run_changed.emit(self._selected_macro is not None)

    @Slot()
    def run_selected_macro(self):
        if not self._selected_macro:
            logger.warning('Run requested but no macro is selected.')
            return
        
        workflow_id = self._macro_id_map.get(self._selected_macro)
        if not workflow_id:
            logger.error(f"Failed to match workflow_id for the selected macro name: {self._selected_macro}")
            return
        
        try:
            logger.info(f'Executing macro: {self._selected_macro} (Resolved ID: {workflow_id})')
            
            self._current_state_index = self._states.index('running')
            self.status_changed.emit('running', self._state_labels['running'])
            
            def background_execution():
                try:
                    runner.run_workflow(workflow_id)
                    logger.info(f"Macro execution finished successfully for ID: {workflow_id}")
                except Exception as exec_err:
                    logger.error(f"Exception occurred during pipeline execution for {workflow_id}: {exec_err}")
                finally:
                    self._current_state_index = self._states.index('idle')
                    self.status_changed.emit('idle', self._state_labels['idle'])
                    self.load_macros()
                    self.execution_finished.emit()
            
            exec_thread = threading.Thread(target=background_execution, daemon=True)
            exec_thread.start()
            
        except Exception as e:
            logger.error(f'Execution handling failed for {self._selected_macro}: {e}')
            raise

    @Slot(str)
    def delete_macro(self, macro_name: str):
        if not macro_name:
            return
            
        workflow_id = self._macro_id_map.get(macro_name)
        if not workflow_id:
            logger.warning(f'Delete requested, but tracking map does not contain macro: {macro_name}')
            return
            
        try:
            logger.info(f'Deleting macro: {macro_name} (Target ID: {workflow_id})')
            
            from core.recorder.screen_capturer import get_macros_root
            macros_root = get_macros_root()
            target_dir = macros_root / workflow_id
            
            if target_dir.exists() and target_dir.is_dir():
                shutil.rmtree(target_dir)
                logger.info(f"Physically deleted macro directory tree: {target_dir}")
            
            self.load_macros()
        except Exception as e:
            logger.error(f'Failed to delete macro {macro_name} from workspace: {e}')
            raise