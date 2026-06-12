# @role: データモデルの「振る舞い」とバリデーションエラーを検証するテストコード。

import sys
import os
import pytest

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from pydantic import ValidationError
from models.data_types import AppConfig

def test_app_config_valid_data():
    """正常系: 正しいホスト名とポート番号でインスタンスが生成されること"""
    config = AppConfig(
        ai_mode='cloud',
        llm_host='api.example.com',
        llm_port='443',
        cv_host='192.168.1.10',
        cv_port='8080'
    )
    assert config.ai_mode == 'cloud'
    assert config.llm_host == 'api.example.com'

def test_app_config_invalid_ai_mode():
    """異常系: 許可されていないai_modeを指定した場合にエラーとなること"""
    with pytest.raises(ValidationError):
        AppConfig(ai_mode='invalid_mode')

def test_app_config_host_sanitization():
    """異常系: ホスト名にOSコマンドインジェクションの危険がある文字列が含まれる場合に弾くこと"""
    with pytest.raises(ValidationError):
        # 不正な文字（; や スペースなど）を含む場合
        AppConfig(llm_host='127.0.0.1; rm -rf /')

def test_app_config_invalid_port():
    """異常系: ポート番号が範囲外または非数の場合にエラーとなること"""
    with pytest.raises(ValidationError):
        AppConfig(llm_port='70000') # 65535を超える
    
    with pytest.raises(ValidationError):
        AppConfig(cv_port='abc') # 数値でない