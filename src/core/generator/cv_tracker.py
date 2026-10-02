# Role: CVを用いたテキストフィールドのBBox抽出、精緻化、およびOCRを用いた追跡・スコアリングを担当するモジュール

import cv2
import numpy as np
import math
import logging
import Levenshtein
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from PIL import Image

from engines.ocr.reader import read_text_from_image

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
    last_click_pos: Optional[Tuple[int, int]] = None
) -> Tuple[Optional[Tuple[int, int, int, int]], str]:
    """
    一連のタイピング中の複数フレームを分析し、入力バッファと文字列が継続的に一致する
    座標(BoundingBox)を追跡・スコアリングして、最も確からしいテキストフィールドを特定する。
    """
    try:
        from core.recorder.romaji_converter import to_hiragana
    except ImportError:
        to_hiragana = lambda x: x
        
    field_candidates = []
    current_buffer = ""
    ocr_call_count = 0
    frame_count = 0
    
    active_search_areas = [(i, sa) for i, sa in enumerate(search_areas)] if search_areas else []
    
    def _evaluate_frame(img_path_rel: str, buffer_to_check: str, is_ime: bool):
        nonlocal ocr_call_count, active_search_areas, frame_count, field_candidates
        if not buffer_to_check.strip():
            return
            
        frame_count += 1
        img_path = macros_root / img_path_rel
        if not img_path.exists():
            return
            
        try:
            ocr_results = []
            
            if active_search_areas:
                with Image.open(img_path) as img:
                    max_w, max_h = img.width, img.height
                    for idx, s_area in active_search_areas:
                        l = max(0, s_area[0])
                        t = max(0, s_area[1])
                        r = min(max_w, s_area[2])
                        b = min(max_h, s_area[3])
                        
                        if r > l and b > t:
                            crop_img = img.crop((l, t, r, b))
                            eval_img_path = temp_dir / f"temp_eval_tracking_{img_path.stem}_area{idx}.png"
                            crop_img.save(eval_img_path)
                            
                            area_results = read_text_from_image(str(eval_img_path))
                            if area_results:
                                for res in area_results:
                                    res.boundingBox.x += l
                                    res.boundingBox.y += t
                                    ocr_results.append((res, idx))
            else:
                raw_res = read_text_from_image(str(img_path))
                if raw_res:
                    ocr_results = [(res, None) for res in raw_res]

            if not ocr_results:
                return

            unique_results = []
            seen_centers = []
            for res, idx in ocr_results:
                if not res.content: continue
                cx = res.boundingBox.x + res.boundingBox.width // 2
                cy = res.boundingBox.y + res.boundingBox.height // 2
                
                is_duplicate = False
                for sx, sy in seen_centers:
                    if abs(cx - sx) < 20 and abs(cy - sy) < 20:
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    seen_centers.append((cx, cy))
                    unique_results.append((res, idx))
                    
            ocr_results = unique_results

            target_text = to_hiragana(buffer_to_check) if is_ime else buffer_to_check
            target_lower = target_text.lower()
            buffer_lower = buffer_to_check.lower()
            
            for res, area_idx in ocr_results:
                c_lower = res.content.lower()
                
                if is_ime:
                    similarity = Levenshtein.ratio(target_lower, c_lower)
                    is_substring = target_lower in c_lower
                else:
                    similarity = Levenshtein.ratio(buffer_lower, c_lower)
                    is_substring = buffer_lower in c_lower
                
                l, t, w, h = res.boundingBox.x, res.boundingBox.y, res.boundingBox.width, res.boundingBox.height
                r, b = l + w, t + h
                
                dist_penalty = 0.0
                if last_click_pos is not None:
                    cx, cy = l + w / 2, t + h / 2
                    dist = math.hypot(cx - last_click_pos[0], cy - last_click_pos[1])
                    dist_penalty = min(0.5, (dist / 1000.0) * 0.5)

                matched_cand = None
                for cand in field_candidates:
                    cl, ct, cr, cb = cand["bbox"]
                    if abs(l - cl) < 30 and abs(t - ct) < 15: 
                        matched_cand = cand
                        break

                target_len = len(target_lower) if is_ime else len(buffer_lower)
                ocr_len = len(c_lower)
                len_diff = abs(target_len - ocr_len)
                len_penalty = (len_diff / max(1, target_len)) * 0.3
                len_penalty = min(0.8, len_penalty)

                change_penalty = 0.0
                if matched_cand:
                    prev_text = matched_cand["last_text"].lower()
                    dist_change = Levenshtein.distance(prev_text, c_lower)
                    max_len = max(len(prev_text), len(c_lower), 1)
                    
                    if dist_change > 1:
                        change_ratio = dist_change / max_len
                        if change_ratio > 0.5:
                            change_penalty = 0.5
                        elif change_ratio > 0.2:
                            change_penalty = change_ratio * 0.5
                else:
                    if len(c_lower) > max(3, target_len * 2):
                        change_penalty = 0.3

                frame_score = similarity - dist_penalty - change_penalty - len_penalty
                
                logger.info(f"[{target_event_id}] Scoring OCR - Found: '{res.content}', Score: {frame_score:.2f} (Sim: {similarity:.2f}, Pen: {dist_penalty:.2f}, Chg: {change_penalty:.2f}, Len: {len_penalty:.2f}), Target: '{target_lower}' (IME: {is_ime})")
                
                if similarity > 0.3 or is_substring:
                    ocr_call_count += 1
                    try:
                        with Image.open(img_path) as tracking_img:
                            t_crop = tracking_img.crop((l, t, r, b))
                            t_crop_path = temp_dir / f"temp_ocr_crop_tracking_{target_event_id}_{img_path.stem}_{ocr_call_count}.png"
                            t_crop.save(t_crop_path)
                    except Exception:
                        pass

                    if matched_cand:
                        matched_cand["score"] += frame_score * 2.0 
                        matched_cand["bbox"] = (min(l, cl), min(t, ct), max(r, cr), max(b, cb))
                        matched_cand["last_text"] = res.content
                    else:
                        field_candidates.append({
                            "bbox": (l, t, r, b),
                            "score": frame_score,
                            "last_text": res.content
                        })

            if field_candidates:
                max_score = max(cand["score"] for cand in field_candidates)
                surviving_candidates = []
                for cand in field_candidates:
                    if max_score - cand["score"] <= 3.0:
                        surviving_candidates.append(cand)
                    else:
                        logger.info(f"[{target_event_id}] Pruning poor candidate at {cand['bbox']} (Score: {cand['score']:.2f}, Max: {max_score:.2f})")
                field_candidates = surviving_candidates

            if active_search_areas and field_candidates:
                global_max_score = max(cand["score"] for cand in field_candidates)
                
                surviving_areas = []
                for idx, s_area in active_search_areas:
                    sl, st, sr, sb = s_area
                    area_has_candidate = False
                    area_max = -float('inf')
                    for cand in field_candidates:
                        cl, ct, cr, cb = cand["bbox"]
                        cx, cy = (cl + cr) / 2, (ct + cb) / 2
                        if sl - 50 <= cx <= sr + 50 and st - 50 <= cy <= sb + 50:
                            area_has_candidate = True
                            area_max = max(area_max, cand["score"])
                    
                    if frame_count >= 3 and not area_has_candidate:
                        logger.info(f"[{target_event_id}] Pruning search area {idx} (No valid candidates found in early frames)")
                        continue
                        
                    if area_has_candidate and (global_max_score - area_max > 3.0):
                        logger.info(f"[{target_event_id}] Pruning search area {idx} (Area Max: {area_max:.2f}, Global Max: {global_max_score:.2f})")
                        continue
                        
                    surviving_areas.append((idx, s_area))
                
                if surviving_areas:
                    active_search_areas = surviving_areas

        except Exception as e:
            logger.warning(f"Error during OCR tracking evaluation: {e}")

    for event in group_events:
        img_path_rel = event.get("pre_img_path")
        is_ime = event.get("ime_active", False)
        
        if img_path_rel:
            _evaluate_frame(img_path_rel, current_buffer, is_ime)
            
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
    l, t, r, b = best_candidate["bbox"]
    
    final_img_path_rel = candidate_img_paths_rel[-1]
    final_img_path = macros_root / final_img_path_rel
    base_img_path_rel = group_events[0].get("pre_img_path") if group_events else candidate_img_paths_rel[0]
    base_img_path = macros_root / base_img_path_rel

    final_crop_box = (l, t, r, b)

    if final_img_path.exists() and base_img_path.exists():
        try:
            img_base = cv2.imread(str(base_img_path), cv2.IMREAD_GRAYSCALE)
            img_final = cv2.imread(str(final_img_path), cv2.IMREAD_GRAYSCALE)
            
            diff = cv2.absdiff(img_base, img_final)
            _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
            
            kernel = np.ones((5, 15), np.uint8)
            dilated = cv2.dilate(thresh, kernel, iterations=1)
            
            contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            
            raw_bboxes = []
            for cnt in contours:
                x, y, w, h = cv2.boundingRect(cnt)
                if w > 5 and h > 5:
                    raw_bboxes.append((x, y, w, h))
                
            filtered_bboxes = []
            for i, bbox1 in enumerate(raw_bboxes):
                x1, y1, w1, h1 = bbox1
                is_enclosing = False
                for j, bbox2 in enumerate(raw_bboxes):
                    if i == j: continue
                    x2, y2, w2, h2 = bbox2
                    if x1 <= x2 and y1 <= y2 and x1 + w1 >= x2 + w2 and y1 + h1 >= y2 + h2:
                        if w1 > w2 or h1 > h2:
                            is_enclosing = True
                            break
                if not is_enclosing:
                    filtered_bboxes.append(bbox1)
            
            target_contours = []
            for bbox in filtered_bboxes:
                x, y, w, h = bbox
                cy = y + h / 2
                if (t - 20) <= cy <= (b + 20):
                    if x + w > l - 15: 
                        target_contours.append(bbox)
            
            if target_contours:
                min_x = min(bx for bx, by, bw, bh in target_contours)
                min_y = min(by for bx, by, bw, bh in target_contours)
                max_r = max(bx + bw for bx, by, bw, bh in target_contours)
                max_b = max(by + bh for bx, by, bw, bh in target_contours)
                
                final_l = max(0, min(l, min_x)) 
                
                final_crop_box = (final_l, min_y, max_r, max_b)
                logger.info(f"[{target_event_id}] Logically expanded BBox by combining text contours (excluding left icons): {final_crop_box}")
            else:
                final_crop_box = (l, t, r + 200, b)
                
        except Exception as e:
            logger.warning(f"[{target_event_id}] Failed to refine and expand final BBox: {e}")

    fl, ft, fr, fb = final_crop_box
    pad_x = 5
    pad_y = 5
    
    l_crop = max(0, fl - pad_x)
    t_crop = max(0, ft - pad_y)
    r_crop = fr + pad_x
    b_crop = fb + pad_y
    
    return (l_crop, t_crop, r_crop, b_crop), best_candidate["last_text"]