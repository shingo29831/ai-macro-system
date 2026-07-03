# @role: SettingsViewModelの「振る舞い」とシグナル発行、およびバリデーションエラー伝播の正確性を検証するテストコード。

import sys
import os
import pytest
from pydantic import ValidationError, SecretStr

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from ui.viewmodels.settings_viewmodel import SettingsViewModel
from models.data_types import AppConfig

def test_load_current_settings_emits_signal(mocker):
    """正常系：設定ロード時にconfig_loadedシグナルが辞書データと共に発行され、SecretStrが復号されるかをテストする"""
    vm = SettingsViewModel()
    
    # ConfigManagerのload_configをモック化して固定のAppConfigを返すように設定
    mock_config = AppConfig(
        ai_mode='cloud', 
        llm_host='test.local', 
        llm_port='1111',
        vllm_host='127.0.0.1',
        vllm_port='8000',
        generator_api_key='dummy_key'
    )
    mocker.patch('utils.config_manager.ConfigManager.load_config', return_value=mock_config)

    emitted_configs = []
    def handle_config_loaded(config_dict):
        emitted_configs.append(config_dict)

    vm.config_loaded.connect(handle_config_loaded)
    vm.load_current_settings()

    assert len(emitted_configs) == 1
    assert emitted_configs[0]['ai_mode'] == 'cloud'
    assert emitted_configs[0]['llm_host'] == 'test.local'
    # SecretStrがUI向けに平文の文字列として辞書にセットされているか
    assert emitted_configs[0]['generator_api_key'] == 'dummy_key'

def test_save_settings_success_emits_signal(mocker):
    """正常系：正しい入力値（Phase 3拡張含む）が渡された場合、save_successfulシグナルが発行されるかをテストする"""
    vm = SettingsViewModel()
    
    # ConfigManagerのsave_configをモック化（何もしない）
    mock_save = mocker.patch('utils.config_manager.ConfigManager.save_config')

    success_emitted = []
    def handle_save_successful():
        success_emitted.append(True)

    vm.save_successful.connect(handle_save_successful)
    
    # 正常な値を渡す
    vm.save_settings(
        ai_mode='local',
        llm_host='192.168.1.5',
        llm_port='8844',
        cv_host='127.0.0.1',
        cv_port='8843',
        vllm_host='127.0.0.1',
        vllm_port='8000',
        generator_api_key='sk-test-gen',
        judge_api_key='sk-test-judge'
    )

    # 保存処理が呼ばれ、成功シグナルが発行されたか
    mock_save.assert_called_once()
    assert len(success_emitted) == 1

def test_save_settings_validation_error_emits_failed_signal(mocker):
    """異常系：バリデーションエラーになる不正な入力値を渡した場合、save_failedシグナルがエラー内容と共に発行されるかをテストする"""
    vm = SettingsViewModel()
    
    failed_emitted = []
    def handle_save_failed(msg):
        failed_emitted.append(msg)

    vm.save_failed.connect(handle_save_failed)
    
    # 意図的に不正なポート番号とホスト名を渡す
    vm.save_settings(
        ai_mode='local',
        llm_host='127.0.0.1; ls', # インジェクション
        llm_port='999999',        # 範囲外
        cv_host='127.0.0.1',
        cv_port='8843',
        vllm_host='127.0.0.1',
        vllm_port='8000',
        generator_api_key='',
        judge_api_key=''
    )

    assert len(failed_emitted) == 1
    # エラーメッセージにバリデーション失敗の旨が含まれているか
    assert '入力値に誤りがあります' in failed_emitted[0]

def test_save_settings_unexpected_error_emits_failed_signal(mocker):
    """異常系：ファイル書き込み時などに予期せぬ例外が発生した場合、エラーを握り潰さずUIへ伝播させるかをテストする"""
    vm = SettingsViewModel()
    
    # save_configが意図的に例外を投げるようにモック化
    mocker.patch('utils.config_manager.ConfigManager.save_config', side_effect=PermissionError('アクセス拒否'))

    failed_emitted = []
    def handle_save_failed(msg):
        failed_emitted.append(msg)

    vm.save_failed.connect(handle_save_failed)
    
    # 正常な値を渡すが、内部で保存エラーが発生する
    vm.save_settings(
        ai_mode='local',
        llm_host='127.0.0.1',
        llm_port='8844',
        cv_host='127.0.0.1',
        cv_port='8843',
        vllm_host='127.0.0.1',
        vllm_port='8000',
        generator_api_key='',
        judge_api_key=''
    )

    assert len(failed_emitted) == 1
    assert 'システムエラー' in failed_emitted[0]