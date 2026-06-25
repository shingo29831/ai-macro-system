# @role: 画像からテキストを読み取るOCRエンジンの実行と結果のパース

import os
import sys
import json
import time
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List

from models.data_types import TextAnalysisData, BoundingBox

logger = logging.getLogger(__name__)

# NDLOCR-Lite の ocr.py のパス (サブモジュール内)
vendor_path = Path(__file__).resolve().parent.parent.parent / "vendor" / "ndlocr-lite"
ocr_script_path = vendor_path / "src" / "ocr.py"

# スクリプトが存在するかチェック
NDLOCR_AVAILABLE = ocr_script_path.exists()

def read_text_from_image(image_path: str) -> List[TextAnalysisData]:
    if not NDLOCR_AVAILABLE:
        logger.error(f"NDLOCR-Lite script not found at {ocr_script_path}. Did you run 'git submodule update --init'?")
        return []

    if not os.path.exists(image_path):
        logger.warning(f"Image path does not exist: {image_path}")
        return []

    logger.info(f"Running NDLOCR-Lite on {image_path}")
    results = []
    
    # READMEの仕様に従い、一時ディレクトリを出力先に指定してコマンドを実行
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # サブプロセスとして NDLOCR-Lite を実行
            cmd = [
                sys.executable,  # 現在の仮想環境のPythonを使用
                str(ocr_script_path),
                "--sourceimg", str(image_path),
                "--output", str(tmpdir),
                "--json-only"
            ]
            
            # 実行 (コンソールウィンドウなどを出さずにバックグラウンド処理)
            process = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            # tmpdirに出力されたJSONファイルを検索
            output_files = list(Path(tmpdir).glob("*.json"))
            if not output_files:
                logger.warning("NDLOCR-Lite executed but no JSON output was found.")
                logger.debug(f"NDLOCR-Lite stdout: {process.stdout}")
                return []
                
            # JSONを読み込んでパース
            with open(output_files[0], 'r', encoding='utf-8') as f:
                ocr_data = json.load(f)
                
            # ※ 以下のパース処理はNDLOCR-Liteの実際のJSONフォーマットに合わせています
            # JSONの構造に "contents" や "text" のリストが含まれていることを想定
            if isinstance(ocr_data, list):
                items = ocr_data
            elif isinstance(ocr_data, dict) and "contents" in ocr_data:
                items = ocr_data["contents"]
            else:
                items = []

            for item in items:
                # 座標データ (x, y, width, height等のキー名に依存するため安全に取得)
                bounds = item.get("bounds", [0, 0, 0, 0])
                if isinstance(bounds, dict):
                    x, y, w, h = bounds.get("x", 0), bounds.get("y", 0), bounds.get("w", 0), bounds.get("h", 0)
                elif isinstance(bounds, list) and len(bounds) >= 4:
                    x, y, w, h = bounds[0], bounds[1], bounds[2], bounds[3]
                else:
                    x, y, w, h = 0, 0, 0, 0
                    
                text_content = item.get("text", "")
                confidence = item.get("confidence", item.get("score", 1.0)) # スコアが取れない場合は1.0

                if text_content:
                    results.append(TextAnalysisData(
                        timestamp=int(time.time() * 1000),
                        boundingBox=BoundingBox(x=int(x), y=int(y), width=int(w), height=int(h)),
                        content=str(text_content),
                        confidence=float(confidence)
                    ))
                    
        except subprocess.CalledProcessError as e:
            logger.error(f"NDLOCR-Lite process failed with code {e.returncode}.\nStderr: {e.stderr}")
        except Exception as e:
            logger.error(f"Failed to execute or parse NDLOCR-Lite: {e}")

    return results