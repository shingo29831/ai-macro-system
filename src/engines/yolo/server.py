# @role: コンピュータビジョン（YOLOによるUI要素検出、NDLOCR-Liteによるテキスト認識）を同一プロセスでホスティングするFastAPIサーバーのルーティングおよびライフサイクル管理

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# 同一および隣接するエンジンパッケージからロジッククラスをインポート
from engines.yolo.server_engine import YoloServerEngine
from engines.ocr.server_engine import OcrServerEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

engines = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Computer Vision engines...")
    try:
        engines["yolo"] = YoloServerEngine()
        engines["ocr"] = OcrServerEngine()
        logger.info("CV models loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load CV models: {e}")
        # アプリケーションの起動自体はブロックせず、リクエスト時に500エラーを返す設計
        engines["yolo"] = None
        engines["ocr"] = None
        
    yield
    
    logger.info("Unloading CV models and freeing memory...")
    engines.clear()

app = FastAPI(lifespan=lifespan)

class ImageRequest(BaseModel):
    image_path: str = Field(..., min_length=1, description="Path to the target image file")

@app.post("/api/v1/yolo/detect")
async def detect_ui(req: ImageRequest):
    if not os.path.exists(req.image_path):
        raise HTTPException(status_code=404, detail="Image file not found")
        
    yolo_engine = engines.get("yolo")
    if yolo_engine is None:
        raise HTTPException(status_code=500, detail="YOLO engine is not initialized")
        
    try:
        ui_elements = yolo_engine.detect(req.image_path)
        return {"uiAnalysis": ui_elements}
    except Exception as e:
        logger.error(f"YOLO detection failed: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error during YOLO detection")

@app.post("/api/v1/ocr/read")
async def read_text(req: ImageRequest):
    if not os.path.exists(req.image_path):
        raise HTTPException(status_code=404, detail="Image file not found")
        
    ocr_engine = engines.get("ocr")
    if ocr_engine is None:
        raise HTTPException(status_code=500, detail="OCR engine is not initialized")
        
    try:
        text_analysis = await ocr_engine.read_text(req.image_path)
        return {"textAnalysis": text_analysis}
    except TimeoutError as te:
        logger.error(f"OCR reading timed out: {te}")
        raise HTTPException(status_code=504, detail="OCR processing timed out")
    except Exception as e:
        logger.error(f"OCR reading failed: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error during OCR processing")