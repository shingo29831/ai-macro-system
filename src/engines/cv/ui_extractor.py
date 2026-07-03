"""
AI向け役割: OpenCVベースのUI抽出エンジン。画像内の輝度勾配(エッジ)と境界線の色均一性を解析し、テキスト等のノイズを除外してUIコンポーネント(検索ボックス等)の枠を正確に抽出する。
"""

import cv2
import numpy as np
from typing import List, Dict, Any

class UIExtractor:
    def __init__(self, min_area: int = 1000, max_aspect_ratio: float = 10.0, color_tolerance: float = 15.0):
        self._min_area = min_area
        self._max_aspect_ratio = max_aspect_ratio
        self._color_tolerance = color_tolerance

    def extract_uis(self, image: np.ndarray) -> List[Dict[str, Any]]:
        # 背景: グレースケール化と平滑化により、微細なテクスチャ等のノイズを低減
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # 背景: Cannyアルゴリズムにより輝度変化の微分値を計算し、全探索なしで高速に境界線を特定
        edges = cv2.Canny(blurred, 50, 150)
        
        # 背景: 階層構造は不要なため RETR_LIST を使用し、抽出処理を軽量化
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        
        uis = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < self._min_area:
                continue
                
            # 背景: 輪郭を直線近似し、多角形としての頂点数を減らす
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            
            # 背景: 頂点が4つ（矩形）とみなせるものをUIの枠として処理
            if len(approx) == 4:
                x, y, w, h = cv2.boundingRect(approx)
                aspect_ratio = max(w, h) / min(w, h)
                
                # 背景: 極端に細長い線状のノイズをUI枠として誤認しないように除外
                if aspect_ratio <= self._max_aspect_ratio:
                    # 背景: 輪郭の縁のピクセル色の標準偏差を計算し、文字の塊ではなく均一な色の枠（テキストボックス等）であるかを判定
                    mask = np.zeros(image.shape[:2], dtype=np.uint8)
                    cv2.drawContours(mask, [approx], -1, 255, 1)
                    edge_pixels = image[mask == 255]
                    
                    if len(edge_pixels) == 0:
                        continue
                        
                    std_dev = np.std(edge_pixels, axis=0)
                    
                    # 背景: 色のばらつきが許容範囲内（単一色の線で囲まれている）の場合のみUI枠として採用
                    if np.all(std_dev < self._color_tolerance):
                        # 背景: システムの後段(UIs)で扱いやすい形式にバウンディングボックス情報をマッピング
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