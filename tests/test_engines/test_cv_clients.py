# @role: YOLOおよびOCRのAPIクライアントの「振る舞い」（通信成功時のパース処理と、失敗時のExponential Backoffリトライ機構）を検証するテストコード。

import sys
import os
import pytest
from unittest.mock import patch, MagicMock
import requests

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from engines.yolo.detector import detect_ui_elements
from engines.ocr.reader import read_text_from_image
from models.data_types import AppConfig

@pytest.fixture
def mock_config(mocker):
    config = AppConfig(cv_host='127.0.0.1', cv_port='8843')
    mocker.patch('utils.config_manager.ConfigManager.load_config', return_value=config)
    return config

def test_detector_success(mocker, mock_config):
    """正常系: YOLOクライアントが通信成功時にUiAnalysisDataのリストを返すことをテスト"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "uiAnalysis": [
            {
                "timestamp": 123456789,
                "boundingBox": {"x": 10, "y": 20, "width": 100, "height": 50},
                "type": "input",
                "confidence": 0.99
            }
        ]
    }
    mocker.patch('requests.post', return_value=mock_response)
    
    results = detect_ui_elements("dummy.png")
    
    assert len(results) == 1
    assert results[0].type == "input"
    assert results[0].confidence == 0.99

def test_detector_retry_and_fail(mocker, mock_config):
    """異常系: YOLOクライアントが通信エラー時にリトライを行い、最終的に空リストを返すことをテスト"""
    # 常に例外を投げるようにしつつ、モックオブジェクトを変数に保持する
    mock_post = mocker.patch('requests.post', side_effect=requests.exceptions.ConnectionError("Connection refused"))
    mocker.patch('time.sleep')  # テストを高速化するためにsleepもモック化
    
    max_retries = 3
    results = detect_ui_elements("dummy.png", max_retries=max_retries)
    
    # 規定回数リトライされたか（モックオブジェクトの呼び出し回数で検証）
    assert mock_post.call_count == max_retries
    # 最終的に空リストが返り、クラッシュを回避できているか
    assert results == []

def test_reader_success(mocker, mock_config):
    """正常系: OCRクライアントが通信成功時にTextAnalysisDataのリストを返すことをテスト"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "textAnalysis": [
            {
                "timestamp": 123456789,
                "boundingBox": {"x": 0, "y": 0, "width": 10, "height": 10},
                "content": "テスト",
                "confidence": 0.95
            }
        ]
    }
    mocker.patch('requests.post', return_value=mock_response)
    
    results = read_text_from_image("dummy.png")
    
    assert len(results) == 1
    assert results[0].content == "テスト"
    assert results[0].confidence