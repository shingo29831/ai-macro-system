# @role: MainViewModelの「振る舞い」とシグナル発行、およびエラー伝播の正確性を検証するテストコード。

import sys
import os
import pytest

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from ui.viewmodels.main_viewmodel import MainViewModel
from models.data_types import MacroSummary

def test_load_macros_emits_signal():
    """正常系：データロード時にmacros_updatedシグナルが正しいモデルリストと共に発行されるかをテストする"""
    vm = MainViewModel()
    emitted_macros = []

    def handle_macros_updated(macros):
        emitted_macros.extend(macros)

    vm.macros_updated.connect(handle_macros_updated)
    vm.load_macros()

    assert len(emitted_macros) == 4
    assert isinstance(emitted_macros[0], MacroSummary)
    assert emitted_macros[0].name == 'Meld Task 定期バックアップ'

def test_select_macro_updates_can_run_state():
    """正常系：マクロの選択状態に応じてcan_run_changedシグナルが正しく発行されるかをテストする"""
    vm = MainViewModel()
    states = []

    def handle_can_run_changed(can_run):
        states.append(can_run)

    vm.can_run_changed.connect(handle_can_run_changed)

    vm.select_macro('Meld Task 定期バックアップ')
    assert states[-1] is True

    vm.select_macro('')
    assert states[-1] is False

def test_toggle_status_rotates_states():
    """正常系：ステータスが待機中->記録中->実行中->待機中の順にローテーションするかをテストする"""
    vm = MainViewModel()
    emitted_states = []

    def handle_status_changed(state, label):
        emitted_states.append(state)

    vm.status_changed.connect(handle_status_changed)

    vm.toggle_status()
    assert emitted_states[-1] == 'recording'

    vm.toggle_status()
    assert emitted_states[-1] == 'running'

    vm.toggle_status()
    assert emitted_states[-1] == 'idle'

def test_run_selected_macro_without_selection_does_not_crash():
    """正常系：マクロが未選択の状態で実行要求が来た場合、エラーを出さずに安全に処理を中断する（早期リターンする）振る舞いをテストする"""
    vm = MainViewModel()
    
    # 意図的に未選択状態にする
    vm.select_macro(None)
    
    try:
        vm.run_selected_macro()
    except Exception as e:
        pytest.fail(f"未選択時の実行で予期せぬ例外が発生しました: {e}")

def test_delete_macro_empty_name_does_not_crash():
    """正常系：削除対象のマクロ名が空の場合、早期リターンしてクラッシュしないことをテストする"""
    vm = MainViewModel()
    try:
        vm.delete_macro('')
    except Exception as e:
        pytest.fail(f"空文字の削除要求で例外が発生しました: {e}")

def test_start_and_stop_recording_updates_status(mocker):
    """正常系：記録の開始および停止メソッドがos_hookを呼び出し、UIステータスを正しく更新するかをテストする"""
    vm = MainViewModel()
    
    # Core層のos_hookは環境依存するためモック化する
    mock_start = mocker.patch('core.recorder.os_hook.start_recording')
    mock_stop = mocker.patch('core.recorder.os_hook.stop_recording')
    
    emitted_states = []
    def handle_status_changed(state, label):
        emitted_states.append(state)

    vm.status_changed.connect(handle_status_changed)
    
    # 記録開始
    vm.start_recording()
    mock_start.assert_called_once()
    assert emitted_states[-1] == 'recording'
    
    # 記録停止
    vm.stop_recording()
    mock_stop.assert_called_once()
    assert emitted_states[-1] == 'idle'