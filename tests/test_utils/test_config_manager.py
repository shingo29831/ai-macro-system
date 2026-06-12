# @role: ConfigManagerのファイルI/Oの振る舞いと、破損時の安全なフォールバックを検証するテスト。

import sys
import os
import json
import pytest

sys.path.insert(0, os.path.abspath('src'))

from utils.config_manager import ConfigManager
from models.data_types import AppConfig

# テスト用のクリーンアップ処理
@pytest.fixture(autouse=True)
def clean_config_file():
    yield
    if os.path.exists(ConfigManager.CONFIG_PATH):
        os.remove(ConfigManager.CONFIG_PATH)

def test_load_config_returns_default_when_not_exists():
    """正常系: config.jsonが存在しない場合、デフォルト値のAppConfigが返ること"""
    if os.path.exists(ConfigManager.CONFIG_PATH):
        os.remove(ConfigManager.CONFIG_PATH)
        
    config = ConfigManager.load_config()
    assert config.ai_mode == 'local'
    assert config.llm_port == '8844'

def test_save_and_load_config():
    """正常系: 保存した設定値が正しく読み込めること"""
    custom_config = AppConfig(ai_mode='cloud', llm_host='example.com', llm_port='1234', cv_host='1.1.1.1', cv_port='5678')
    ConfigManager.save_config(custom_config)
    
    loaded_config = ConfigManager.load_config()
    assert loaded_config.ai_mode == 'cloud'
    assert loaded_config.llm_host == 'example.com'

def test_load_config_fallback_on_corrupt_json():
    """異常系: ファイルがJSONとして破損している場合、クラッシュせずにデフォルト値を返すこと"""
    with open(ConfigManager.CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write("{ INVALID JSON DATA")
        
    config = ConfigManager.load_config()
    assert config.ai_mode == 'local' # デフォルト値にフォールバックしているか