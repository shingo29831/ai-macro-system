# @role: サーバー側でNDLOCR-Liteを用いたテキスト認識(OCR)を実行するエンジン
import os
import sys
import json
import time
import tempfile
import asyncio
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class OcrServerEngine:
    def __init__(self, ocr_cli_path: str = None):
        if ocr_cli_path is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.ocr_cli_path = os.path.abspath(os.path.join(current_dir, "../../vendor/ndlocr-lite/src/ocr.py"))
        else:
            self.ocr_cli_path = ocr_cli_path

        if not os.path.exists(self.ocr_cli_path):
            logger.warning(f"NDLOCR-Lite not found at {self.ocr_cli_path}. Will run in mock mode.")
            self.is_mock = True
        else:
            logger.info(f"NDLOCR-Lite found at {self.ocr_cli_path}.")
            self.is_mock = False

    async def read_text(self, image_path: str) -> List[Dict[str, Any]]:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found at {image_path}")

        mock_timestamp = int(time.time() * 1000)

        if self.is_mock:
            return [{
                "timestamp": mock_timestamp,
                "boundingBox": {"x": 0, "y": 0, "width": 50, "height": 20},
                "content": "NDLOCR未設定",
                "confidence": 0.50
            }]

        with tempfile.TemporaryDirectory() as temp_dir:
            cmd = [
                sys.executable, 
                self.ocr_cli_path, 
                "--sourceimg", image_path, 
                "--output", temp_dir,
                "--json-only"
            ]
            
            logger.info("Starting NDLOCR-Lite subprocess...")
            
            process = await asyncio.create_subprocess_exec(*cmd)
            
            try:
                await asyncio.wait_for(process.communicate(), timeout=180.0)
            except asyncio.TimeoutError:
                process.kill()
                raise TimeoutError("OCR process timed out after 180 seconds")

            if process.returncode != 0:
                logger.error(f"NDLOCR-Lite execution failed with return code {process.returncode}")
                raise RuntimeError(f"OCR process failed with return code {process.returncode}")
            
            return self._parse_results(temp_dir, mock_timestamp)

    def _flatten_items(self, nested_list: List[Any]) -> List[Dict[str, Any]]:
        """二重リストなどのネストされた構造をフラットな辞書のリストに変換する再帰関数"""
        flat_list = []
        for element in nested_list:
            if isinstance(element, list):
                flat_list.extend(self._flatten_items(element))
            elif isinstance(element, dict):
                flat_list.append(element)
        return flat_list

    def _parse_results(self, temp_dir: str, timestamp: int) -> List[Dict[str, Any]]:
        json_paths = []
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                if file.endswith('.json'):
                    json_paths.append(os.path.join(root, file))
        
        if not json_paths:
            logger.warning(f"No JSON files found in {temp_dir}.")
            return []

        json_path = json_paths[0]
        with open(json_path, 'r', encoding='utf-8') as f:
            ocr_data = json.load(f)
            
        text_analysis_list = []
        raw_items = []
        
        if isinstance(ocr_data, list):
            raw_items = ocr_data
        elif isinstance(ocr_data, dict):
            for key in ['contents', 'lines', 'texts', 'blocks', 'data', 'result']:
                if key in ocr_data and isinstance(ocr_data[key], list):
                    raw_items = ocr_data[key]
                    break
            if not raw_items:
                raw_items = [ocr_data]

        # 二重リスト構造を完全にフラット化
        items = self._flatten_items(raw_items)

        for item in items:
            content = item.get('text') or item.get('chars') or item.get('content') or ''
            if not content:
                continue
                
            bbox = item.get('boundingBox') or item.get('bounds') or item.get('bbox') or item.get('box') or [0,0,0,0]
            
            x_min, y_min, x_max, y_max = 0, 0, 10, 10
            
            if isinstance(bbox, list) and len(bbox) > 0:
                # パターン1: [[x1, y1], [x2, y2], [x3, y3], [x4, y4]] のような二次元配列
                if isinstance(bbox[0], list):
                    xs = [pt[0] for pt in bbox if len(pt) >= 1]
                    ys = [pt[1] for pt in bbox if len(pt) >= 2]
                    if xs and ys:
                        x_min, x_max = min(xs), max(xs)
                        y_min, y_max = min(ys), max(ys)
                
                # パターン2: [x1, y1, x2, y2, x3, y3, x4, y4] のようなフラットな8点
                elif len(bbox) >= 8:
                    xs = [bbox[0], bbox[2], bbox[4], bbox[6]]
                    ys = [bbox[1], bbox[3], bbox[5], bbox[7]]
                    x_min, x_max = min(xs), max(xs)
                    y_min, y_max = min(ys), max(ys)
                
                # パターン3: [x_min, y_min, x_max, y_max] のようなフラットな4点
                elif len(bbox) >= 4:
                    x_min, y_min, x_max, y_max = bbox[:4]

            text_analysis_list.append({
                "timestamp": timestamp,
                "boundingBox": {
                    "x": int(x_min),
                    "y": int(y_min),
                    "width": int(max(0, x_max - x_min)),
                    "height": int(max(0, y_max - y_min))
                },
                "content": str(content),
                "confidence": float(item.get('confidence', item.get('score', 0.90)))
            })

        logger.info(f"Successfully mapped {len(text_analysis_list)} text elements to system schema.")
        return text_analysis_list