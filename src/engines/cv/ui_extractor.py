# src/engines/cv/ui_extractor.py
"""
AI向け役割: OpenCVベースのUI抽出エンジン。画像内の輝度勾配(エッジ)を解析し、全探索を回避してUIコンポーネントのバウンディングボックス(UIs)を抽出・返却する。
"""

import cv2
import numpy as np
from typing import List, Dict, Any

class UIExtractor:
    def __init__(self, min_area: int = 1000, max_aspect_ratio: float = 10.0):
        self._min_area = min_area
        self._max_aspect_ratio = max_aspect_ratio

    def extract_uis(self, image: np.ndarray) -> List[Dict[str, Any]]:
        # グレースケール化と平滑化により、微細なテクスチャ等のノイズを低減
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # Cannyアルゴリズムにより輝度変化の微分値を計算し、全探索なしで高速に境界線を特定
        edges = cv2.Canny(blurred, 50, 150)
        
        # 階層構造は不要なため RETR_LIST を使用し、抽出処理を軽量化
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        
        uis = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < self._min_area:
                continue
                
            # 輪郭を直線近似し、多角形としての頂点数を減らす
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            
            # 頂点が4つ（矩形）とみなせるものをUIの枠として処理
            if len(approx) == 4:
                x, y, w, h = cv2.boundingRect(approx)
                aspect_ratio = max(w, h) / min(w, h)
                
                # 極端に細長い線状のノイズをUI枠として誤認しないように除外
                if aspect_ratio <= self._max_aspect_ratio:
                    # システムの後段(UIs)で扱いやすい形式にバウンディングボックス情報をマッピング
                    uis.append({
                        "type": "frame",
                        "box": {
                            "x": int(x),
                            "y": int(y),
                            "width": int(w),
                            "height": int(h)
                        },
                        "confidence": 1.0  # ルールベース抽出のため、検出されたものは確信度1.0とする
                    })
                    
        return uis