# src/engines/yolo/server_engine.py
# @role: サーバー側でYOLOモデルを用いたUI要素の検出を実行するエンジン

import os
import time
import logging
from pathlib import Path
from typing import List, Dict, Any

try:
    from ultralytics import YOLO
except ImportError:
    logging.error("Required library 'ultralytics' is not installed.")

logger = logging.getLogger(__name__)

class YoloServerEngine:
    def __init__(self, model_path: str = None, fallback_path: str = 'yolov8n.pt'):
        # src/ ディレクトリの絶対パスを取得
        base_dir = Path(__file__).resolve().parent.parent.parent
        
        # 明示的に指定されていない場合、デフォルトを ui-master-best.pt に設定
        if model_path is None:
            model_path = str(base_dir / "ui-master-best.pt")
            
        if fallback_path == 'yolov8n.pt':
            fallback_path = str(base_dir / "yolov8n.pt")

        if not os.path.exists(model_path):
            logger.warning(f"Custom model '{model_path}' not found. Falling back to '{fallback_path}'")
            model_path = fallback_path
            
        try:
            self.model = YOLO(model_path)
            logger.info(f"YOLO model '{Path(model_path).name}' loaded successfully on server.")
        except Exception as e:
            logger.error(f"Failed to load YOLO model from '{model_path}': {e}")
            raise

    def detect(self, image_path: str, confidence_threshold: float = 0.25) -> List[Dict[str, Any]]:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found at {image_path}")

        results = self.model(image_path, conf=confidence_threshold)
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
                    "type": label, # ui-master-best.pt によって正確な UIコンポーネント名 が入るようになります
                    "confidence": round(confidence, 2)
                })
                
        return ui_elements