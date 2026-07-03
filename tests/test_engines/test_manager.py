# @role: LocalServerManagerの「振る舞い」と、設定値に応じたバックグラウンドプロセスの起動・終了制御を検証するテストコード。

import sys
import os
import pytest
from unittest.mock import MagicMock

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from engines.manager import LocalServerManager
from models.data_types import AppConfig

def test_start_servers_local_mode(mocker):
    """正常系: ai_modeが'local'の場合、LLM, CV, vLLMの全ローカルサーバーが起動されることをテストする"""
    # 設定のモック化（すべてローカル起動の条件を満たす設定）
    mock_config = AppConfig(
        ai_mode='local',
        llm_host='127.0.0.1',
        llm_port='8844',
        cv_host='localhost',
        cv_port='8843',
        vllm_host='127.0.0.1',
        vllm_port='8000'
    )
    mocker.patch('utils.config_manager.ConfigManager.load_config', return_value=mock_config)
    
    # subprocess.Popen と atexit.register をモック化
    mock_popen = mocker.patch('subprocess.Popen', return_value=MagicMock())
    mock_atexit = mocker.patch('atexit.register')

    manager = LocalServerManager()
    manager.start_servers()

    # 3つのプロセス（LLM, CV, vLLM）が起動されたか
    assert mock_popen.call_count == 3
    assert len(manager._processes) == 3
    
    # 終了処理が登録されたか
    mock_atexit.assert_called_once_with(manager.stop_servers)

def test_start_servers_cloud_mode(mocker):
    """正常系: ai_modeが'cloud'の場合、LLMとvLLMは起動されず、CVサーバーのみが起動されることをテストする"""
    # 設定のモック化（AIはクラウドだが、CVはローカルのままのケースを想定）
    mock_config = AppConfig(
        ai_mode='cloud',
        llm_host='api.example.com',
        llm_port='443',
        cv_host='127.0.0.1',
        cv_port='8843',
        vllm_host='127.0.0.1',
        vllm_port='8000'
    )
    mocker.patch('utils.config_manager.ConfigManager.load_config', return_value=mock_config)
    
    mock_popen = mocker.patch('subprocess.Popen', return_value=MagicMock())
    mocker.patch('atexit.register')

    manager = LocalServerManager()
    manager.start_servers()

    # CVサーバーの1つだけが起動されたか
    assert mock_popen.call_count == 1
    assert len(manager._processes) == 1
    
    # 呼び出されたコマンドにCVサーバーのものが含まれているか確認
    args, _ = mock_popen.call_args
    assert 'engines.yolo.server:app' in args[0]

def test_stop_servers_terminates_processes(mocker):
    """正常系: stop_serversが呼ばれた際、保持しているプロセス群に対してterminateが呼ばれるかをテストする"""
    manager = LocalServerManager()
    
    # ダミーのモックプロセスを3つ作成して管理リストに追加
    mock_procs = [MagicMock(), MagicMock(), MagicMock()]
    for p in mock_procs:
        p.poll.return_value = None  # まだ実行中であることをシミュレート
    manager._processes.extend(mock_procs)
    
    manager.stop_servers()
    
    # すべてのプロセスに対してterminateとwaitが呼ばれたか
    for p in mock_procs:
        p.terminate.assert_called_once()
        p.wait.assert_called_once_with(timeout=5.0)
        
    # リストがクリアされたか
    assert len(manager._processes) == 0

def test_stop_servers_kills_on_timeout(mocker):
    """異常系: terminate後のwaitでタイムアウトが発生した場合、killで強制終了されるかをテストする"""
    import subprocess
    manager = LocalServerManager()
    
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    # waitが呼ばれた際に TimeoutExpired 例外を投げるように設定
    mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd='dummy', timeout=5.0)
    
    manager._processes.append(mock_proc)
    manager.stop_servers()
    
    # killがフォールバックとして呼ばれたか
    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()
    assert len(manager._processes) == 0