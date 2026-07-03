# @role: LLMサーバーへのリクエストをカプセル化したクライアント。通信のタイムアウトやリトライ処理を担う。

import time
import logging
from typing import Dict, Any, Optional
import requests

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 8000, timeout: int = 60, max_retries: int = 3):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.max_retries = max_retries
        self.endpoint = f"{self.base_url}/generate"

    def generate(self, prompt: str, image_base64: Optional[str] = None) -> Dict[str, Any]:
        payload = {"prompt": prompt}
        if image_base64:
            payload["image"] = image_base64

        attempt = 0
        while attempt < self.max_retries:
            try:
                response = requests.post(
                    self.endpoint,
                    json=payload,
                    timeout=self.timeout
                )
                response.raise_for_status()
                return response.json()
            
            except requests.exceptions.Timeout as e:
                logger.warning(f"Timeout on attempt {attempt + 1}: {e}")
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Connection error on attempt {attempt + 1}: {e}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed on attempt {attempt + 1}: {e}")
                
            attempt += 1
            if attempt < self.max_retries:
                # To prevent overwhelming the recovering server
                time.sleep(2 ** attempt)

        return {"success": False, "error": "Max retries exceeded"}