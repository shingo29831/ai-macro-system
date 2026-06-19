# @role: コンピュータビジョン（YOLOによるUI要素検出、EasyOCRによるテキスト認識）を同一プロセスでホスティングするFastAPIサーバー。

import os
import time
import cv2
import numpy as np
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import logging

try:
    import easyocr
    from ultralytics import YOLO
except ImportError:
    logging.error("Required libraries 'easyocr' or 'ultralytics' are not installed. Please run: pip install easyocr ultralytics")

logger = logging.getLogger(__name__)

# --- グローバル変数 (モデルのキャッシュ用) ---
yolo_model = None
ocr_reader = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global yolo_model, ocr_reader
    logger.info("Initializing Computer Vision engines...")
    
    try:
        ocr_reader = easyocr.Reader(['ja', 'en'])
        
        # モデルファイルが存在しない場合は汎用モデルへフォールバック
        model_path = 'ui-master-best.pt'
        if not os.path.exists(model_path):
            logger.warning(f"Custom model '{model_path}' not found. Falling back to 'yolov8n.pt'")
            model_path = 'yolov8n.pt'
            
        yolo_model = YOLO(model_path)
        logger.info("CV models loaded successfully. (Ready for inference)")
    except Exception as e:
        logger.error(f"Failed to load CV models: {e}")
        
    yield
    
    logger.info("Unloading CV models and freeing memory...")
    yolo_model = None
    ocr_reader = None

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
        img = cv2.imread(req.image_path)
        if img is None:
            raise HTTPException(status_code=400, detail="Failed to read image")

        results = yolo_model(img, conf=0.25)
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
        
    if ocr_reader is None:
        raise HTTPException(status_code=500, detail="OCR engine is not initialized")
        
    try:
        img = cv2.imread(req.image_path)
        if img is None:
            raise HTTPException(status_code=400, detail="Failed to read image")
            
        results = ocr_reader.readtext(img)
        mock_timestamp = int(time.time() * 1000)
        
        raw_boxes = []
        for (bbox, text, prob) in results:
            text = text.strip()
            if prob < 0.1 or not text:
                continue
            if len(text) == 1 and not text.isalnum():
                continue
                
            x_min = int(min([p[0] for p in bbox]))
            y_min = int(min([p[1] for p in bbox]))
            x_max = int(max([p[0] for p in bbox]))
            y_max = int(max([p[1] for p in bbox]))
            
            raw_boxes.append({
                'text': text,
                'x_min': x_min,
                'y_min': y_min,
                'x_max': x_max,
                'y_max': y_max,
                'prob': float(prob)
            })

        for b in raw_boxes:
            b['y_center'] = (b['y_min'] + b['y_max']) / 2
        raw_boxes.sort(key=lambda b: (b['y_center'] // 15, b['x_min']))

        merged_blocks = []
        for box in raw_boxes:
            text = box['text']
            left = box['x_min']
            top = box['y_min']
            right = box['x_max']
            bottom = box['y_max']
            box_h = bottom - top
            prob = box['prob']
            
            added = False
            for block in merged_blocks:
                y_overlap = max(0, min(block['y_max'], bottom) - max(block['y_min'], top))
                min_h = min(block['y_max'] - block['y_min'], box_h)
                
                if min_h > 0 and y_overlap > min_h * 0.3:
                    gap = left - block['x_max']
                    if -box_h * 2.0 <= gap <= box_h * 2.5:
                        block['text'] += " " + text 
                        block['x_min'] = min(block['x_min'], left)
                        block['y_min'] = min(block['y_min'], top)
                        block['x_max'] = max(block['x_max'], right)
                        block['y_max'] = max(block['y_max'], bottom)
                        block['prob'] = (block['prob'] + prob) / 2.0
                        added = True
                        break
            
            if not added:
                merged_blocks.append(box)

        text_analysis_list = []
        for block in merged_blocks:
            text = block['text'].strip()
            if len(text) < 2 and not text.isalnum():
                continue
                
            y = block['y_min']
            x = block['x_min']
            w = block['x_max'] - block['x_min']
            h = block['y_max'] - block['y_min']
            
            text_analysis_list.append({
                "timestamp": mock_timestamp,
                "boundingBox": {
                    "x": int(x),
                    "y": int(y),
                    "width": int(w),
                    "height": int(h)
                },
                "content": text,
                "confidence": round(block['prob'], 2)
            })

        return {"textAnalysis": text_analysis_list}

    except Exception as e:
        logger.error(f"OCR reading failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))