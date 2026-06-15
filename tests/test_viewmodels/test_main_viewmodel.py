# @role: MainViewModelの「振る舞い」とシグナル発行、非同期処理の呼び出し、およびディレクトリ操作の正確性を検証するテストコード。

import sys
import os
import pytest
from pathlib import Path

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from ui.viewmodels.main_viewmodel import MainViewModel
from models.data_types import MacroSummary

@pytest.fixture
def mock_macros_dir(tmp_path, mocker):
    """テスト用の一時ディレクトリを生成し、get_macros_root がそれを返すようにモックする"""
    wf1 = tmp_path / "wf_001"
    wf2 = tmp_path / "wf_002"
    wf1.mkdir()
    wf2.mkdir()
    
    mocker.patch('core.recorder.screen_capturer.get_macros_root', return_value=tmp_path)
    return tmp_path


def test_load_macros_scans_directory(mock_macros_dir):
    """正常系：実ディレクトリを走査し、マクロ一覧と内部マップが正しくロードされるかをテストする"""
    vm = MainViewModel()
    emitted_macros = []

    def handle_macros_updated(macros):
        emitted_macros.extend(macros)

    vm.macros_updated.connect(handle_macros_updated)
    vm.load_macros()

    assert len(emitted_macros) == 2
    assert emitted_macros[0].name == 'マクロ wf_001'
    assert emitted_macros[1].name == 'マクロ wf_002'
    
    # 内部マップ(UI名 -> ID)が正しく構築されているか
    assert vm._macro_id_map['マクロ wf_001'] == 'wf_001'


def test_stop_recording_kicks_background_generation(mocker, mock_macros_dir):
    """正常系：記録停止時にバックグラウンドでマクロ生成プロセスがキックされるかをテストする"""
    vm = MainViewModel()
    
    # 記録停止のフックと、直前に作成されたディレクトリの情報をモック
    mocker.patch('core.recorder.os_hook.stop_recording')
    mocker.patch('core.recorder.os_hook._recording_dirs', {"macro_name": "wf_test_001"})
    
    # スレッド起動とCore層の生成処理をモック
    mock_thread = mocker.patch('threading.Thread')
    mock_generate = mocker.patch('core.generator.log_integrator.generate_macro_workflow')
    
    vm.stop_recording()
    
    # スレッドが作成・開始されたか
    mock_thread.assert_called_once()
    mock_thread.return_value.start.assert_called_once()
    
    # スレッドに渡された関数（background_generation）を手動で実行して、中身の挙動をテスト
    target_func = mock_thread.call_args[1]['target']
    target_func()
    
    # log_integrator が正しいIDで呼ばれたか
    mock_generate.assert_called_once_with("wf_test_001")


def test_run_selected_macro_kicks_execution_engine(mocker, mock_macros_dir):
    """正常系：選択したマクロの実行要求が、バックグラウンドスレッドで実行エンジンへ正しく中継されるかをテストする"""
    vm = MainViewModel()
    vm.load_macros() # マップを構築
    
    # UI上で 'マクロ wf_001' を選択したと仮定
    vm.select_macro('マクロ wf_001')
    
    mock_thread = mocker.patch('threading.Thread')
    mock_run = mocker.patch('core.executor.runner.run_workflow')
    
    vm.run_selected_macro()
    
    # スレッドがキックされたか
    mock_thread.assert_called_once()
    mock_thread.return_value.start.assert_called_once()
    
    # スレッド内関数を実行
    target_func = mock_thread.call_args[1]['target']
    target_func()
    
    # 実行エンジンが正しい workflow_id で呼び出されたか
    mock_run.assert_called_once_with("wf_001")


def test_delete_macro_removes_directory(mocker, mock_macros_dir):
    """正常系：削除要求によって物理ディレクトリが削除され、一覧が更新されるかをテストする"""
    vm = MainViewModel()
    vm.load_macros()
    
    # 削除対象のパスが存在することを確認
    target_dir = mock_macros_dir / "wf_001"
    assert target_dir.exists()
    
    # shutil.rmtree をモック化して、実際に消さずに呼び出しだけを検証
    mock_rmtree = mocker.patch('shutil.rmtree')
    
    vm.delete_macro('マクロ wf_001')
    
    # shutil.rmtree が正しいパスで呼ばれたか
    mock_rmtree.assert_called_once_with(target_dir)


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