# @role: 対象のスクリーンショット画像から、テキストを認識(OCR)する外部エンジンとのインターフェース。通信失敗時はリトライを行う。

import time
import requests
import logging
from typing import List
from models.data_types import TextAnalysisData
from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)

def read_text_from_image(image_path: str, max_retries: int = 3) -> List[TextAnalysisData]:
    """画像からテキストを読み取り、TextAnalysisDataのリストを返す"""
    config = ConfigManager.load_config()
    url = f"http://{config.cv_host}:{config.cv_port}/api/v1/ocr/read"
    payload = {"image_path": image_path}
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Requesting OCR processing (Attempt {attempt + 1}/{max_retries}) for {image_path}")
            # サーバー側の180秒タイムアウトをカバーするため、通信側のタイムアウトを190秒に延長
            response = requests.post(url, json=payload, timeout=190.0)
            response.raise_for_status()
            
            data = response.json()
            results = []
            for item in data.get("textAnalysis", []):
                results.append(TextAnalysisData(**item))
            return results
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"OCR API request failed: {e}")
            if attempt == max_retries - 1:
                logger.error("Max retries reached for OCR API. Returning empty list.")
                return []
            time.sleep(2 ** attempt)  # Exponential Backoff (1s, 2s, 4s...)
    
    return []