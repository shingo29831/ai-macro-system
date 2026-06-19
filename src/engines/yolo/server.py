# @role: コンピュータビジョン（YOLOによるUI要素検出、NDLOCR-Liteによるテキスト認識）を同一プロセスでホスティングするFastAPIサーバー。

import os
import sys
import time
import json
import tempfile
import subprocess
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

try:
    from ultralytics import YOLO
except ImportError:
    logging.error("Required library 'ultralytics' is not installed.")

logger = logging.getLogger(__name__)

# --- グローバル変数 (モデルのキャッシュ用) ---
yolo_model = None
ocr_cli_path = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global yolo_model, ocr_cli_path
    logger.info("Initializing Computer Vision engines...")
    
    try:
        # 1. YOLOモデルの読み込み (大成功した 'best.pt' を指定)
        model_path = 'best.pt'
        if not os.path.exists(model_path):
            logger.warning(f"Custom model '{model_path}' not found. Falling back to 'yolov8n.pt'")
            model_path = 'yolov8n.pt'
            
        yolo_model = YOLO(model_path)
        
        # 2. NDLOCR-Liteのパス設定 (このチャットで特定した ocr.py のパス)
        ocr_cli_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../vendor/ndlocr-lite/src/ocr.py"))
        
        if not os.path.exists(ocr_cli_path):
            logger.warning(f"NDLOCR-Lite not found at {ocr_cli_path}. OCR will return mock response.")
        else:
            logger.info(f"NDLOCR-Lite found at {ocr_cli_path}. (Ready for inference)")
            
        logger.info("CV models loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load CV models: {e}")
        
    yield
    
    logger.info("Unloading CV models and freeing memory...")
    yolo_model = None

app = FastAPI(lifespan=lifespan)

class ImageRequest(BaseModel):
    image_path: str

@app.post("/api/v1/yolo/detect")
async def detect_ui(req: ImageRequest):
    if not os.path.exists(req.image_path):
        raise HTTPException(status_code=404, detail="Image file not found")
        
    if yolo_model is None:
        raise HTTPException(status_code=500, detail="YOLO model is not initialized")
        
    try:
        # YOLOによる推論 (確信度0.25以上を抽出)
        results = yolo_model(req.image_path, conf=0.25)
        mock_timestamp = int(time.time() * 1000)
        
        ui_elements = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                class_id = int(box.cls[0].item())
                confidence = float(box.conf[0].item())
                label = result.names[class_id]
                
                ui_elements.append({
                    "timestamp": mock_timestamp,
                    "boundingBox": {
                        "x": int(x1),
                        "y": int(y1),
                        "width": int(x2 - x1),
                        "height": int(y2 - y1)
                    },
                    "type": label,
                    "confidence": round(confidence, 2)
                })
                
        return {"uiAnalysis": ui_elements}
        
    except Exception as e:
        logger.error(f"YOLO detection failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/ocr/read")
async def read_text(req: ImageRequest):
    if not os.path.exists(req.image_path):
        raise HTTPException(status_code=404, detail="Image file not found")
        
    mock_timestamp = int(time.time() * 1000)

    # NDLOCR-Lite が見つからない場合はフォールバック（モック）
    if not ocr_cli_path or not os.path.exists(ocr_cli_path):
        return {
            "textAnalysis": [
                {
                    "timestamp": mock_timestamp,
                    "boundingBox": {"x": 0, "y": 0, "width": 50, "height": 20},
                    "content": "NDLOCR未設定",
                    "confidence": 0.50
                }
            ]
        }
        
    # NDLOCR-Lite をサブプロセスで呼び出す
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # このチャットで特定した正しい引数フォーマット
            cmd = [
                sys.executable, 
                ocr_cli_path, 
                "--sourceimg", req.image_path, 
                "--output", temp_dir,
                "--json-only"
            ]
            
            # サブプロセスの実行
            process = subprocess.run(cmd, capture_output=True, text=True)
            
            if process.returncode != 0:
                logger.error(f"NDLOCR-Lite execution failed: {process.stderr}")
                raise Exception("OCR process failed")
            
            # 出力されたJSONファイルを読み込む
            output_files = [f for f in os.listdir(temp_dir) if f.endswith('.json')]
            text_analysis_list = []
            
            if output_files:
                json_path = os.path.join(temp_dir, output_files[0])
                with open(json_path, 'r', encoding='utf-8') as f:
                    ocr_data = json.load(f)
                
                # NDLOCRの出力をシステムのスキーマに変換
                items = ocr_data if isinstance(ocr_data, list) else [ocr_data]
                
                for item in items:
                    content = item.get('text', '')
                    if not content:
                        continue
                        
                    # バウンディングボックスのパース
                    bbox = item.get('bounds', [0,0,0,0])
                    if isinstance(bbox, list) and len(bbox) == 4:
                        x_min, y_min, x_max, y_max = bbox
                    else:
                        x_min, y_min, x_max, y_max = 0, 0, 10, 10
                        
                    text_analysis_list.append({
                        "timestamp": mock_timestamp,
                        "boundingBox": {
                            "x": int(x_min),
                            "y": int(y_min),
                            "width": int(max(0, x_max - x_min)),
                            "height": int(max(0, y_max - y_min))
                        },
                        "content": content,
                        "confidence": 0.90
                    })

            return {"textAnalysis": text_analysis_list}

    except Exception as e:
        logger.error(f"OCR reading failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))