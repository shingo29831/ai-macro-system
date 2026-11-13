"""Module: @role: CVを用いたテキストフィールドのBBox抽出、精緻化、およびOCR追跡オーケストレーションを担当する。"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from core.generator.cv_field_scorer import (
    collect_ocr_results,
    expand_final_bbox_by_diff_contours,
    prune_candidates_and_areas,
    score_and_update_candidates,
)

logger = logging.getLogger(__name__)

def get_text_field_bboxes_cv(images_paths: List[Path], max_w: int, max_h: int) -> List[Tuple[int, int, int, int]]:
    """
    ベース画像(入力前)と現在の画像のピクセル差分から、純粋な入力領域のBBoxを抽出する。
    """
    if len(images_paths) < 2:
        return []
        
    try:
        img_base = cv2.imread(str(images_paths[0]), cv2.IMREAD_GRAYSCALE)
        if img_base is None: return []
        
        accum_mask = np.zeros_like(img_base)
        
        for i in range(1, len(images_paths)):
            img_curr = cv2.imread(str(images_paths[i]), cv2.IMREAD_GRAYSCALE)
            if img_curr is None or img_curr.shape != img_base.shape:
                continue
                
            diff = cv2.absdiff(img_base, img_curr)
            _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
            accum_mask = cv2.bitwise_or(accum_mask, thresh)
            
        kernel = np.ones((5, 15), np.uint8)
        dilated = cv2.dilate(accum_mask, kernel, iterations=2)
        
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        valid_bboxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w > 5 and h > 5 and w < max_w * 0.8 and h < max_h * 0.5:
                valid_bboxes.append((x, y, x+w, y+h))
                
        merged_bboxes = []
        for bbox in valid_bboxes:
            x1, y1, x2, y2 = bbox
            has_merged = True
            while has_merged:
                has_merged = False
                for i, (mx1, my1, mx2, my2) in enumerate(merged_bboxes):
                    if not (x2 < mx1 or x1 > mx2 or y2 < my1 or y1 > my2):
                        new_bbox = (min(x1, mx1), min(y1, my1), max(x2, mx2), max(y2, my2))
                        merged_bboxes.pop(i)
                        x1, y1, x2, y2 = new_bbox
                        has_merged = True
                        break
            merged_bboxes.append((x1, y1, x2, y2))
                
        return merged_bboxes

    except Exception as e:
        logger.warning(f"CV cumulative diff failed: {e}")
        return []

def refine_textfield_bbox_cv(image_path: str, diff_bbox: Tuple[int, int, int, int]) -> Tuple[Tuple[int, int, int, int], str]:
    try:
        img = cv2.imread(image_path)
        if img is None:
            return diff_bbox, "unknown"
            
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.bilateralFilter(gray, 9, 75, 75)
        edges = cv2.Canny(blurred, 15, 50)
        kernel = np.ones((3, 3), np.uint8)
        edges = cv2.dilate(edges, kernel, iterations=1)
        
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        
        dl, dt, dr, db = diff_bbox
        char_h = db - dt
        if char_h <= 0: char_h = 20
        
        min_field_h = max(15, int(char_h * 1.0))
        best_bbox = diff_bbox
        best_shape = "text_area (no specific boundary found)"
        min_margin_area = float('inf')
        
        img_h, img_w = img.shape[:2]
        max_allowed_area = img_w * img_h * 0.9
        
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            
            if h < min_field_h:
                continue
            
            margin = 20
            if x <= dl + margin and y <= dt + margin and (x+w) >= dr - margin and (y+h) >= db - margin:
                area = w * h
                
                if area < max_allowed_area:
                    if area < min_margin_area:
                        min_margin_area = area
                        best_bbox = (x, y, x+w, y+h)
                        
                        peri = cv2.arcLength(cnt, True)
                        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
                        contour_area = cv2.contourArea(cnt)
                        extent = contour_area / float(area) if area > 0 else 0
                        
                        if len(approx) >= 4 and 0.85 < extent < 0.98:
                            best_shape = "rounded_rectangle"
                        elif len(approx) == 4 and extent >= 0.98:
                            best_shape = "rectangle"
                        else:
                            best_shape = "complex_shape"
                            
        return best_bbox, best_shape
        
    except Exception as e:
        logger.warning(f"Failed to refine bbox with CV: {e}")
        return diff_bbox, "unknown"

def track_text_field_by_scoring(
    group_events: List[Dict[str, Any]],
    candidate_img_paths_rel: List[str],
    macros_root: Path,
    temp_dir: Path,
    target_event_id: str,
    search_areas: Optional[List[Tuple[int, int, int, int]]] = None,
    last_click_pos: Optional[Tuple[int, int]] = None,
) -> Tuple[Optional[Tuple[int, int, int, int]], str]:
    """
    一連のタイピング中の複数フレームを分析し、入力バッファと文字列が継続的に一致する
    座標(BoundingBox)を追跡・スコアリングして、最も確からしいテキストフィールドを特定する。
    """
    try:
        from core.recorder.romaji_converter import to_hiragana
    except ImportError:
        to_hiragana = lambda x: x

    field_candidates: List[Dict[str, Any]] = []
    current_buffer = ""
    ocr_call_count = 0
    frame_count = 0
    active_search_areas = [(i, sa) for i, sa in enumerate(search_areas)] if search_areas else []

    for event in group_events:
        img_path_rel = event.get("pre_img_path")
        is_ime = event.get("ime_active", False)

        if img_path_rel and current_buffer.strip():
            frame_count += 1
            img_path = macros_root / img_path_rel
            if img_path.exists():
                try:
                    ocr_results = collect_ocr_results(img_path, active_search_areas, temp_dir)
                    if ocr_results:
                        ocr_call_count = score_and_update_candidates(
                            ocr_results,
                            field_candidates,
                            current_buffer,
                            is_ime,
                            to_hiragana,
                            last_click_pos,
                            img_path,
                            temp_dir,
                            target_event_id,
                            ocr_call_count,
                        )
                        field_candidates, active_search_areas = prune_candidates_and_areas(
                            field_candidates,
                            active_search_areas,
                            frame_count,
                            target_event_id,
                        )
                except Exception as e:
                    logger.warning(f"Error during OCR tracking evaluation: {e}")

        role = str(event.get("semantic_role", "")).lower()
        if role == "backspace":
            current_buffer = current_buffer[:-1]
        elif role == "space":
            current_buffer += " "
        elif len(role) == 1:
            current_buffer += role

    if not field_candidates:
        return None, ""

    best_candidate = max(field_candidates, key=lambda x: x["score"])
    final_img_path = macros_root / candidate_img_paths_rel[-1]
    base_img_path_rel = group_events[0].get("pre_img_path") if group_events else candidate_img_paths_rel[0]
    base_img_path = macros_root / base_img_path_rel

    fl, ft, fr, fb = expand_final_bbox_by_diff_contours(
        base_img_path,
        final_img_path,
        best_candidate["bbox"],
        target_event_id,
    )
    pad = 5
    return (max(0, fl - pad), max(0, ft - pad), fr + pad, fb + pad), best_candidate["last_text"]
