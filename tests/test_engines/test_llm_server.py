# @role: ローカルLLMサーバー(FastAPI)のエンドポイントと、正常/異常系のロジックを検証するテストコード。

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from fastapi.testclient import TestClient

# モック用のダミーレスポンス
MOCK_LLM_RESPONSE = {
    "id": "chatcmpl-123",
    "choices": [{
        "message": {"role": "assistant", "content": "これはモックされたAIの応答です。"}
    }]
}

@pytest.fixture
def mock_llama():
    """重いモデルをロードしないよう、Llamaクラスとその推論メソッドをモック化するフィクスチャ"""
    with patch('engines.llm.server.Llama') as mock_class:
        instance = MagicMock()
        instance.create_chat_completion.return_value = MOCK_LLM_RESPONSE
        mock_class.return_value = instance
        yield instance

def test_generate_text_success(mock_llama):
    """正常系: モデルがロード済みの状態で、テキスト生成リクエストが成功するかをテスト"""
    from engines.llm.server import app
    
    # TestClientを with 構文で使うことで、サーバーの起動処理(lifespan)がエミュレートされる
    with TestClient(app) as client:
        response = client.post(
            "/generate",
            json={"prompt": "こんにちは"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["response"]["choices"][0]["message"]["content"] == "これはモックされたAIの応答です。"

def test_generate_text_with_image(mock_llama):
    """正常系: 画像（Base64）を含めたマルチモーダルリクエストが通るか、フォーマットをテスト"""
    from engines.llm.server import app
    
    with TestClient(app) as client:
        response = client.post(
            "/generate",
            json={
                "prompt": "画像について教えて",
                "image": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=" # 1x1ダミー画像
            }
        )
        
        assert response.status_code == 200
        assert response.json()["success"] is True

        # モックが受け取った messages パラメータの構造がマルチモーダル仕様になっているか検証
        called_messages = mock_llama.create_chat_completion.call_args[1]["messages"]
        assert len(called_messages[0]["content"]) == 2 # テキストと画像の2要素が含まれていること
        assert called_messages[0]["content"][1]["type"] == "image_url"

def test_generate_model_not_loaded():
    """異常系: モデルファイルが存在しない等でロードに失敗した場合のフェールセーフをテスト"""
    from engines.llm.server import app
    
    # 意図的に Llama クラスの初期化で例外を発生させる
    with patch('engines.llm.server.Llama', side_effect=Exception("Model File Not Found")):
        with TestClient(app) as client:
            response = client.post(
                "/generate",
                json={"prompt": "こんにちは"}
            )
            
            assert response.status_code == 200
            data = response.json()
            
            # APIとしては200を返しつつ、内部エラーとして安全にハンドリングされているか
            assert data["success"] is False
            assert "Model is not loaded" in data["error"]