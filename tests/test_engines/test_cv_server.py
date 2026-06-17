# @role: YOLO/OCRをホストするCVサーバー(FastAPI)のエンドポイントと、ファイル存在チェックのロジックを検証するテストコード。

import sys
import os
import pytest
import tempfile

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from fastapi.testclient import TestClient

@pytest.fixture
def dummy_image():
    """テスト用のダミー画像（空ファイル）を作成し、パスを返すフィクスチャ"""
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    yield path
    os.remove(path)

def test_yolo_detect_success(dummy_image):
    """正常系: 存在する画像パスを指定した場合、YOLOのモック解析結果が返ることをテスト"""
    from engines.yolo.server import app
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/yolo/detect",
            json={"image_path": dummy_image}
        )
        assert response.status_code == 200
        data = response.json()
        assert "uiAnalysis" in data
        assert len(data["uiAnalysis"]) > 0
        assert data["uiAnalysis"][0]["type"] == "button"

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
        assert data["textAnalysis"][0]["content"] == "保存"

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