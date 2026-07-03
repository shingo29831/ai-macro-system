# @role: データモデルの「振る舞い」とバリデーションエラーを検証するテストコード。

import sys
import os
import pytest

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from pydantic import ValidationError
from models.data_types import AppConfig, InputLogData, ActionDetail, Size, Coordinates, QualityGateJudgment

# ==========================================
# AppConfig のテスト (Phase 3拡張を含む)
# ==========================================

def test_app_config_valid_data():
    """正常系: 正しいホスト名とポート番号、APIキーでインスタンスが生成されること"""
    config = AppConfig(
        ai_mode='cloud',
        llm_host='api.example.com',
        llm_port='443',
        cv_host='192.168.1.10',
        cv_port='8080',
        vllm_host='127.0.0.1',
        vllm_port='8000',
        generator_api_key='sk-test-gen',
        judge_api_key='sk-test-judge'
    )
    assert config.ai_mode == 'cloud'
    assert config.llm_host == 'api.example.com'
    assert config.vllm_host == '127.0.0.1'
    # SecretStrとして保持されているか確認
    assert config.generator_api_key.get_secret_value() == 'sk-test-gen'
    assert config.judge_api_key.get_secret_value() == 'sk-test-judge'

def test_app_config_invalid_ai_mode():
    """異常系: 許可されていないai_modeを指定した場合にエラーとなること"""
    with pytest.raises(ValidationError):
        AppConfig(ai_mode='invalid_mode')

def test_app_config_host_sanitization():
    """異常系: ホスト名にOSコマンドインジェクションの危険がある文字列が含まれる場合に弾くこと"""
    with pytest.raises(ValidationError):
        # 不正な文字（; や スペースなど）を含む場合
        AppConfig(vllm_host='127.0.0.1; rm -rf /')

def test_app_config_invalid_port():
    """異常系: ポート番号が範囲外または非数の場合にエラーとなること"""
    with pytest.raises(ValidationError):
        AppConfig(vllm_port='70000') # 65535を超える
    
    with pytest.raises(ValidationError):
        AppConfig(cv_port='abc') # 数値でない


# ==========================================
# データモデルの仕様追従テスト (Optional座標など)
# ==========================================

def test_input_log_data_with_cursor():
    """正常系: マウス操作時など、カーソル座標が含まれるInputLogDataが正しく生成されること"""
    log = InputLogData(
        timestamp=1717654800123,
        type="click_down",
        content="left_click",
        windowName="Test Window",
        windowSize=Size(width=800, height=600),
        windowCoordinates=Coordinates(x=0, y=0),
        cursorCoordinates=Coordinates(x=100, y=100)
    )
    assert log.cursorCoordinates is not None
    assert log.cursorCoordinates.x == 100

def test_input_log_data_without_cursor():
    """正常系: キー操作時など、カーソル座標が省略(None)されたInputLogDataが正しく生成されること"""
    log = InputLogData(
        timestamp=1717654800124,
        type="key_down",
        content="Enter",
        windowName="Test Window",
        windowSize=Size(width=800, height=600),
        windowCoordinates=Coordinates(x=0, y=0),
        cursorCoordinates=None
    )
    assert log.cursorCoordinates is None

def test_action_detail_optional_cursor():
    """正常系: ActionDetailでcursorRelativeCoordinatesを省略してもエラーにならないこと"""
    action = ActionDetail(
        inputType="key_down",
        inputValue="A",
        cursorRelativeCoordinates=None,
        diffRatio=0.01  # 低変化率
    )
    assert action.cursorRelativeCoordinates is None
    assert action.diffRatio == 0.01

# ==========================================
# Phase 3: 品質ゲートモデルのテスト
# ==========================================
def test_quality_gate_judgment():
    """正常系: QualityGateJudgmentが正しく生成され、制約を満たすこと"""
    judgment = QualityGateJudgment(
        status='PASS',
        confidence=0.95
    )
    assert judgment.status == 'PASS'
    assert judgment.confidence == 0.95

    with pytest.raises(ValidationError):
        # statusが定義外
        QualityGateJudgment(status='OK', confidence=0.95)
    
    with pytest.raises(ValidationError):
        # confidenceが1.0超過
        QualityGateJudgment(status='FAIL', confidence=1.5, reason='Bad layout')