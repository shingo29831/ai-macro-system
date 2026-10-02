# @role: サーバー側でホストされたOCRエンジン(NDLOCR-Lite)のAPIを呼び出し、画像からテキストを読み取るクライアントインターフェース。通信失敗時はリトライを行う。

import time
import requests
import logging
from typing import List

from models.data_types import TextAnalysisData
from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)

def read_text_from_image(image_path: str, max_retries: int = 3) -> List[TextAnalysisData]:
    config = ConfigManager.load_config()
    url = f"http://{config.cv_host}:{config.cv_port}/api/v1/ocr/read"
    payload = {"image_path": image_path}
    
    # Why: 過剰なタイムアウト(180秒)による生成パイプラインの停止を防止し5秒で安全解決
    for attempt in range(2):
        try:
            logger.info(f"Requesting OCR reading (Attempt {attempt + 1}/2) for {image_path}")
            response = requests.post(url, json=payload, timeout=5.0)
            response.raise_for_status()
            
            data = response.json()
            results = []
            for item in data.get("textAnalysis", []):
                results.append(TextAnalysisData(**item))
            return results
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"OCR API request failed: {e}")
            if isinstance(e, requests.exceptions.ConnectionError):
                break
            time.sleep(0.5)
    
    return []