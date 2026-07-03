# @role: YOLO/OCRをホストするCVサーバー(FastAPI)のエンドポイントと、ファイル存在チェックのロジックを検証するテストコード。

import sys
import os
import pytest
import tempfile
from PIL import Image

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from fastapi.testclient import TestClient

@pytest.fixture
def dummy_image():
    """テスト用のダミー画像を作成し、パスを返すフィクスチャ"""
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    
    # 0バイトのファイルではなく、PILを使って1x1のダミー画像を保存する
    img = Image.new('RGB', (10, 10), color='black')
    img.save(path)
    
    yield path
    os.remove(path)

def test_yolo_detect_success(dummy_image):
        """正常系: 存在する画像パスを指定した場合、YOLOの解析結果（または空配列）が返ることをテスト"""
        from engines.yolo.server import app
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/yolo/detect",
                json={"image_path": dummy_image}
            )
            assert response.status_code == 200
            data = response.json()
            assert "uiAnalysis" in data
            
            # 黒塗りのダミー画像のため、実際のYOLOが稼働している場合は検出0件（空リスト）になるのが正しい挙動。
            # リスト型で結果が返ってきていることだけを検証する。
            assert isinstance(data["uiAnalysis"], list)
            
            # 以下の2行はモック用の検証なので削除（またはコメントアウト）する
            # assert len(data["uiAnalysis"]) > 0
            # assert data["uiAnalysis"][0]["type"] == "button"

def test_ocr_read_success(dummy_image):
    """正常系: 存在する画像パスを指定した場合、OCRのモック解析結果が返ることをテスト"""
    from engines.yolo.server import app
    with TestClient(app) as client:
            response = client.post(
                "/api/v1/ocr/read",
                json={"image_path": dummy_image}
            )
            assert response.status_code == 200
            data = response.json()
            assert "textAnalysis" in data
            assert len(data["textAnalysis"]) > 0
            
            # ツールがない場合は"NDLOCR未設定"、ある場合はOCR結果が入ることを許容する
            content = data["textAnalysis"][0]["content"]
            assert content in ["保存", "NDLOCR未設定"]

def test_file_not_found():
    """異常系: 存在しない画像パスを指定した場合、404エラーが返ることをテスト"""
    from engines.yolo.server import app
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/yolo/detect",
            json={"image_path": "/path/to/nonexistent/image.png"}
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Image file not found"