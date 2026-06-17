# @role: コンピュータビジョン（YOLOによるUI要素検出、OCRによるテキスト認識）を同一プロセスでホスティングするFastAPIサーバー。
#
# 【起動元】
#   - engines/manager.py (ポート8843で起動)

import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

# --- グローバル変数 (モデルのキャッシュ用) ---
yolo_model = None
ocr_engine = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """サーバー起動時にYOLOとOCRのモデルをVRAM/メモリにロードする"""
    global yolo_model, ocr_engine
    logger.info("Initializing Computer Vision engines...")
    
    try:
        # TODO: 実際のYOLOv8モデル (ultralytics等) と OCRエンジン (pytesseract等) をロードする処理
        # yolo_model = YOLO('yolov8n.pt')
        # ocr_engine = initialize_tesseract()
        logger.info("CV models loaded successfully. (Mock mode ready)")
    except Exception as e:
        logger.error(f"Failed to load CV models: {e}")
        
    yield
    
    logger.info("Unloading CV models and freeing memory...")
    yolo_model = None
    ocr_engine = None

app = FastAPI(lifespan=lifespan)

class ImageRequest(BaseModel):
    image_path: str

@app.post("/api/v1/yolo/detect")
async def detect_ui(req: ImageRequest):
    if not os.path.exists(req.image_path):
        raise HTTPException(status_code=404, detail="Image file not found")
        
    try:
        # TODO: 実際のYOLO推論ロジックに置き換える
        # モックとしてダミーの認識結果を返す（UiAnalysisDataスキーマに準拠）
        mock_timestamp = int(time.time() * 1000)
        return {
            "uiAnalysis": [
                {
                    "timestamp": mock_timestamp,
                    "boundingBox": {"x": 100, "y": 200, "width": 50, "height": 30},
                    "type": "button",
                    "confidence": 0.95
                }
            ]
        }
    except Exception as e:
        logger.error(f"YOLO detection failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/ocr/read")
async def read_text(req: ImageRequest):
    if not os.path.exists(req.image_path):
        raise HTTPException(status_code=404, detail="Image file not found")
        
    try:
        # TODO: 実際のOCR推論ロジックに置き換える
        # モックとしてダミーの認識結果を返す（TextAnalysisDataスキーマに準拠）
        mock_timestamp = int(time.time() * 1000)
        return {
            "textAnalysis": [
                {
                    "timestamp": mock_timestamp,
                    "boundingBox": {"x": 110, "y": 205, "width": 30, "height": 20},
                    "content": "保存",
                    "confidence": 0.88
                }
            ]
        }
    except Exception as e:
        logger.error(f"OCR reading failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))