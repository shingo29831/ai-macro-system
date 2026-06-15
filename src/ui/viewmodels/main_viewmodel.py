# @role: メインウィンドウのUI状態を管理し、ビューからのアクションをビジネスロジック(Core層)へ中継するViewModel層。

import logging
from PySide6.QtCore import QObject, Signal, Slot
from models.data_types import MacroSummary
from core.recorder import os_hook

logger = logging.getLogger(__name__)

class MainViewModel(QObject):
    macros_updated = Signal(list)
    status_changed = Signal(str, str)
    can_run_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self._states = ['idle', 'recording', 'running']
        self._state_labels = {'idle': '待機中', 'recording': '記録中', 'running': '実行中'}
        self._current_state_index = 0
        self._selected_macro: str | None = None

    def load_macros(self):
        try:
            # TODO: 将来的には Core 層からデータをロードし、MacroSummaryモデルにマッピングする
            macros = [
                MacroSummary(name='Meld Task 定期バックアップ', status='success', status_text='成功', heals='0回', heal_level='none', last_run='2026-06-10 09:00:00'),
                MacroSummary(name='ValorantParty データ同期', status='warning', status_text='修復完了', heals='2回', heal_level='mid', last_run='2026-06-09 23:30:00'),
                MacroSummary(name='D1 Grand Prix ログ収集', status='success', status_text='成功', heals='1回', heal_level='low', last_run='2026-06-08 14:15:00'),
                MacroSummary(name='就活ポータル 新着チェック', status='danger', status_text='失敗 (Stage 4)', heals='4回', heal_level='high', last_run='2026-06-07 18:00:00')
            ]
            self.macros_updated.emit(macros)
        except Exception as e:
            logger.error(f'Failed to load macros: {e}')
            raise

    @Slot()
    def toggle_status(self):
        self._current_state_index = (self._current_state_index + 1) % len(self._states)
        state = self._states[self._current_state_index]
        self.status_changed.emit(state, self._state_labels[state])

    @Slot()
    def start_recording(self):
        """Core層のos_hookを呼び出して記録を開始し、UIステータスを更新する"""
        try:
            logger.info("Starting macro recording...")
            os_hook.start_recording()
            
            # ステータスを「記録中」に更新
            self._current_state_index = self._states.index('recording')
            self.status_changed.emit('recording', self._state_labels['recording'])
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            raise

    @Slot()
    def stop_recording(self):
        """Core層のos_hookを停止し、マクロ生成フェーズへ移行する"""
        try:
            logger.info("Stopping macro recording...")
            os_hook.stop_recording()
            
            # TODO: ここで core.generator.log_integrator の生成処理(バックグラウンド)をキックする
            
            # ステータスを「待機中」に戻す
            self._current_state_index = self._states.index('idle')
            self.status_changed.emit('idle', self._state_labels['idle'])
        except Exception as e:
            logger.error(f"Failed to stop recording: {e}")
            raise

    @Slot()
    def trigger_emergency_stop(self):
        logger.info('Emergency stop triggered by user.')
        # TODO: 実行エンジンの強制停止（キルスイッチ）を呼び出す

    @Slot(str)
    def select_macro(self, macro_name: str):
        self._selected_macro = macro_name if macro_name else None
        self.can_run_changed.emit(self._selected_macro is not None)

    @Slot()
    def run_selected_macro(self):
        if not self._selected_macro:
            logger.warning('Run requested but no macro is selected.')
            return
        
        try:
            logger.info(f'Executing macro: {self._selected_macro}')
            # TODO: Executorへの実行要求を中継する
        except Exception as e:
            logger.error(f'Execution failed for {self._selected_macro}: {e}')
            raise

    @Slot(str)
    def delete_macro(self, macro_name: str):
        if not macro_name:
            return
            
        try:
            logger.info(f'Deleting macro: {macro_name}')
            # TODO: データ層へ削除要求を出す
        except Exception as e:
            logger.error(f'Failed to delete macro {macro_name}: {e}')
            raise