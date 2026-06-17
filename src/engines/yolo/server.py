# @role: コンピュータビジョン（YOLOv8によるUI要素検出、NDLOCR-Liteによるテキスト認識）を同一プロセスでホスティングするFastAPIサーバー。
#
# 【起動元】
#   - engines/manager.py (ポート8843で起動)

import os
import time
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

# --- YOLOv8 のインポート ---
try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None
    logger.warning("ultralytics is not installed. YOLOv8 detection will fail.")

# グローバル変数
yolo_model = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """サーバー起動時にYOLOv8モデルをメモリにロードする"""
    global yolo_model
    logger.info("Initializing Computer Vision engines...")
    
    try:
        if YOLO:
            # 独自のファインチューニングモデルを環境変数から取得（未設定時は best.pt をデフォルトとする）
            model_path = os.getenv('YOLO_MODEL_PATH', 'best.pt')
            
            logger.info(f"Loading YOLOv8 model from {model_path} ...")
            yolo_model = YOLO(model_path)
            logger.info("YOLOv8 model loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load YOLOv8 model: {e}")
        
    yield
    
    logger.info("Unloading CV models and freeing memory...")
    yolo_model = None

app = FastAPI(lifespan=lifespan)

class ImageRequest(BaseModel):
    image_path: str

@app.post("/api/v1/yolo/detect")
async def detect_ui(req: ImageRequest):
    """YOLOv8を用いたUI要素（ボタン等）の検出"""
    if not os.path.exists(req.image_path):
        raise HTTPException(status_code=404, detail="Image file not found")
    if not yolo_model:
        raise HTTPException(status_code=500, detail="YOLOv8 model is not loaded")
        
    try:
        timestamp = int(time.time() * 1000)
        # YOLO推論の実行
        results = yolo_model(req.image_path)
        
        ui_analysis = []
        for r in results:
            boxes = r.boxes
            for box in boxes:
                # 座標 (x1, y1, x2, y2)
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                class_name = r.names[cls_id]
                
                ui_analysis.append({
                    "timestamp": timestamp,
                    "boundingBox": {
                        "x": int(x1),
                        "y": int(y1),
                        "width": int(x2 - x1),
                        "height": int(y2 - y1)
                    },
                    "type": class_name,
                    "confidence": conf
                })
                
        return {"uiAnalysis": ui_analysis}
    except Exception as e:
        logger.error(f"YOLOv8 detection failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/ocr/read")
async def read_text(req: ImageRequest):
    """国立国会図書館 NDLOCR-Liteを用いたテキスト認識"""
    if not os.path.exists(req.image_path):
        raise HTTPException(status_code=404, detail="Image file not found")
        
    try:
        timestamp = int(time.time() * 1000)
        
        # NDLOCR-Lite がプロジェクト内の vendor/ndlocr-lite にあると想定
        ndlocr_cli_path = os.path.abspath("vendor/ndlocr-lite/src/cli.py")
        
        if not os.path.exists(ndlocr_cli_path):
            # NDLOCR-Liteが見つからない場合のフォールバック（テスト用）
            logger.warning(f"NDLOCR-Lite not found at {ndlocr_cli_path}. Using mock response.")
            return {
                "textAnalysis": [
                    {
                        "timestamp": timestamp,
                        "boundingBox": {"x": 0, "y": 0, "width": 50, "height": 20},
                        "content": "NDLOCR未設定",
                        "confidence": 0.5
                    }
                ]
            }

        # 実際のNDLOCR-Liteをサブプロセスで呼び出す処理
        result = subprocess.run(
            ["python", ndlocr_cli_path, "infer", "--input", req.image_path, "--output", "temp_ocr_out"],
            capture_output=True, text=True, check=True
        )
        
        # 仮のパース処理（NDLOCRの出力テキストファイルを読み込む）
        text_analysis = []
        # TODO: "temp_ocr_out" ディレクトリに生成されたテキストと座標(あれば)を解析して text_analysis に追加するロジックを記述します
        # 完了後は一時出力ファイルを削除します
        
        return {"textAnalysis": text_analysis}
        
    except subprocess.CalledProcessError as e:
        logger.error(f"NDLOCR-Lite execution failed: {e.stderr}")
        raise HTTPException(status_code=500, detail="OCR process failed")
    except Exception as e:
        logger.error(f"OCR reading failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))