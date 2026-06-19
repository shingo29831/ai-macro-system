# @role: 対象のスクリーンショット画像から、UI要素を物体認識(YOLO)する外部エンジンとのインターフェース。通信失敗時はリトライを行う。

import time
import requests
import logging
from typing import List
from models.data_types import UiAnalysisData
from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)

def detect_ui_elements(image_path: str, max_retries: int = 3) -> List[UiAnalysisData]:
    """画像からUI要素を検出し、UiAnalysisDataのリストを返す"""
    config = ConfigManager.load_config()
    # クラウドAIモードであっても、CVはローカル等で動かす柔軟な構成に対応
    url = f"http://{config.cv_host}:{config.cv_port}/api/v1/yolo/detect"
    payload = {"image_path": image_path}
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Requesting YOLO detection (Attempt {attempt + 1}/{max_retries}) for {image_path}")
            # AIの推論は時間がかかる場合があるため、タイムアウトを60秒に延長
            response = requests.post(url, json=payload, timeout=60.0)
            response.raise_for_status()
            
            data = response.json()
            results = []
            for item in data.get("uiAnalysis", []):
                results.append(UiAnalysisData(**item))
            return results
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"YOLO API request failed: {e}")
            if attempt == max_retries - 1:
                logger.error("Max retries reached for YOLO API. Returning empty list.")
                # エラー握り潰しは厳禁だが、ログ解析プロセス全体を落とさないために空配列を返す
                return []
            time.sleep(2 ** attempt)  # Exponential Backoff (1s, 2s, 4s...)
    
    return []