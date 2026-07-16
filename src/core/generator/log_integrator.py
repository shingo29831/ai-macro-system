# src/core/generator/log_integrator.py
# @role: temp/ に保存された一時生データ（入力ログ・画像）とローカルAI（YOLO/OCR/LLM）の解析結果を統合し、意味を理解した実行可能なワークフローを生成する。

import json
import logging
import shutil
import os
import math
import statistics
import Levenshtein
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageChops

import cv2
import numpy as np

from models.data_types import (
    AppConfig, Workflow, WorkflowStep, WorkflowCommandAction, 
    WorkflowStepContext, ActionParameters, UniversalSelector, WorkflowMetadata,
    IntegratedEvent, WindowContext, Size, Coordinates,
    InteractedUiElement, BoundingBox, ActionDetail,
    ContextComponent
)
from models.executable_macro import ExecutableMacro

from engines.yolo.detector import detect_ui_elements
from engines.ocr.reader import read_text_from_image
from engines.llm.client import LLMClient

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

def _parse_raw_event(log_entry: dict, i: int, total_events: int, workflow_id: str, macros_root: Path, progress_callback) -> Optional[Dict[str, Any]]:
    """
    1件の生ログエントリをパースし、CV解析を行って統合イベント情報を返す。
    不要なシステムウィンドウ等の場合は None を返す。
    """
    if not isinstance(log_entry, dict):
        return None
        
    window_name = log_entry.get("WindowName") or "Unknown Window"
    command_line = log_entry.get("WindowCommandLine", "")
    # システムウィンドウ（記録ウィジェットやOSシェル等）の操作をマクロから除外
    system_windows = ["python", "unknown window", "検索", "スタート", "start", "search", "taskbar", "タスクバー", "cortana", "ジャンプ リスト"]
    if not window_name.strip() or any(sw in window_name.lower() for sw in system_windows):
        return None
        
    event_no = log_entry.get("EventNo", f"{i+1:03d}")
    event_id = f"evt_{event_no}" if not str(event_no).startswith("evt_") else str(event_no)
    
    ts_val = log_entry.get("TimeStamp", 0)
    try:
        if isinstance(ts_val, str):
            dt = datetime.fromisoformat(ts_val.replace('Z', '+00:00'))
            safe_timestamp = int(dt.timestamp() * 1000)
        else:
            safe_timestamp = int(ts_val)
    except Exception:
        safe_timestamp = 0

    win_size_data = log_entry.get("WindowSize") or {"width": 0, "height": 0}
    win_coord_data = log_entry.get("WindowCoordinates") or {"x": 0, "y": 0}
    
    win_x = win_coord_data.get("x", 0)
    win_y = win_coord_data.get("y", 0)

    raw_type = str(log_entry.get("Type", ""))
    content_data = log_entry.get("Content") or {}
    app_context = log_entry.get("AppSpecificContext") or log_entry.get("appSpecificContext") or {}
    
    raw_screen_coords = content_data.get("screen_coordinates") if isinstance(content_data, dict) else None
    if raw_screen_coords:
        cursor_x = raw_screen_coords.get("x", 0)
        cursor_y = raw_screen_coords.get("y", 0)
    else:
        cursor_coord_data = log_entry.get("CursorCoordinates") or {"x": 0, "y": 0}
        cursor_x = cursor_coord_data.get("x", 0)
        cursor_y = cursor_coord_data.get("y", 0)

    rel_x = cursor_x - win_x
    rel_y = cursor_y - win_y
    
    raw_type_lower = raw_type.lower()
    is_scroll = "scroll" in raw_type_lower
    is_move = "hover" in raw_type_lower or "move" in raw_type_lower
    is_click = ("click" in raw_type_lower or "mouse" in raw_type_lower) and not (is_scroll or is_move)
    is_key = "key" in raw_type_lower
    is_uia = "uia" in raw_type_lower
    is_meta = "meta" in raw_type_lower
    is_office = "office" in raw_type_lower

    dx = 0.0
    dy = 0.0

    if is_scroll:
        action_type = "scroll"
        if isinstance(content_data, dict):
            dx = content_data.get("dx", 0.0)
            dy = content_data.get("dy", 0.0)
    elif is_office:
        action_type = "office_event"
    elif is_meta:
        action_type = "meta"
    elif is_move:
        action_type = "move"
    elif is_click:
        action_type = "click"
    elif is_key:
        action_type = "key_down"
    elif is_uia:
        action_type = "uia_scan"
    else:
        action_type = "unknown"

    button_val = "left"
    input_val = "unknown"
    ime_active = False
    
    if isinstance(content_data, dict):
        button_val = content_data.get("button", "left")
        ime_active = content_data.get("ime_active", False)
        
        if action_type == "click":
            input_val = f"{button_val}_click"
        elif action_type == "key_down":
            input_val = content_data.get("combo") or content_data.get("key") or content_data.get("text") or "unknown_key"
        elif action_type == "scroll":
            input_val = "scroll"
        elif action_type == "move":
            input_val = "move"
        elif action_type == "uia_scan":
            input_val = content_data.get("action") or "uia_scan"
        elif action_type == "office_event":
            input_val = content_data.get("office_info", {}).get("message", "")
        else:
            input_val = content_data.get("combo") or content_data.get("key") or content_data.get("text") or "unknown"
    else:
        input_val = str(content_data)

    if progress_callback and i % max(1, total_events // 20) == 0:
        progress = 2 + int((i / total_events) * 68)
        action_name = action_type if action_type != "unknown" else raw_type
        progress_callback(progress, f"画像解析中(CV)... {action_name}イベントの処理 ({i+1}/{total_events})")

    ui_type = "unknown"
    semantic_role = input_val
    context_components = []
    
    images_data = log_entry.get("Images", {})
    crop_path_str = images_data.get("Crop")
    pre_img_path_str = images_data.get("Pre")

    raw_diff = images_data.get("Diff", "0.0%")
    try:
        if isinstance(raw_diff, str) and raw_diff.endswith("%"):
            diff_val = float(raw_diff.replace("%", "")) / 100.0
        else:
            diff_val = float(raw_diff)
    except (ValueError, TypeError):
        diff_val = 0.0

    if action_type == "move":
        if crop_path_str and "delete_" in crop_path_str:
            return None
        if diff_val < 0.05:
            return None
    
    if crop_path_str and crop_path_str != "切り抜き失敗":
        context_text = app_context.get("text") or app_context.get("value") or app_context.get("url")
        if context_text:
            logger.info(f"[{workflow_id}] Found app_specific_context for Event {event_id}. Skipping CV inference.")
            semantic_role = context_text
            ui_type = app_context.get("type", "unknown")
        else:
            full_crop_path = macros_root / crop_path_str
            if full_crop_path.exists():
                logger.info(f"[{workflow_id}] Processing CV inference: {i+1}/{total_events} (Event: {event_id})...")
                
                yolo_results = detect_ui_elements(str(full_crop_path))
                ocr_results = read_text_from_image(str(full_crop_path))

                if yolo_results:
                    best_yolo = max(yolo_results, key=lambda x: x.confidence)
                    ui_type = best_yolo.type
                
                if ocr_results:
                    best_ocr = max(ocr_results, key=lambda x: x.confidence)
                    if best_ocr.content and action_type in ["click", "move"]:
                        semantic_role = best_ocr.content
                        
                    for ocr_res in ocr_results:
                        context_components.append(ContextComponent(
                            type="text",
                            content=ocr_res.content,
                            relativeBoundingBox=ocr_res.boundingBox,
                            confidence=ocr_res.confidence,
                            parentRelevance=1.0
                        ))

    action_detail = ActionDetail(
        inputType=raw_type,
        inputValue=input_val,
        cursorRelativeCoordinates=Coordinates(x=rel_x, y=rel_y),
        diffRatio=diff_val
    )

    ui_element = InteractedUiElement(
        type=ui_type,
        relativeBoundingBox=BoundingBox(x=rel_x, y=rel_y, width=0, height=0),
        confidence=1.0,
        action=action_detail,
        context=context_components
    )

    window_context = WindowContext(
        name=window_name,
        size=Size(width=win_size_data.get("width", 0), height=win_size_data.get("height", 0)),
        coordinates=Coordinates(x=win_x, y=win_y),
        UIs=[ui_element]
    )

    integrated_event = IntegratedEvent(
        id=event_id,
        timestamp=safe_timestamp,
        window=window_context
    )

    workflow_info = {
        "event_id": event_id,
        "timestamp": safe_timestamp,
        "raw_type": raw_type,
        "raw_action": action_type,
        "button": button_val,
        "ui_type": ui_type,
        "semantic_role": semantic_role,
        "diff_val": diff_val,
        "dx": dx,
        "dy": dy,
        "cursor_x": cursor_x,
        "cursor_y": cursor_y,
        "pre_img_path": pre_img_path_str,
        "win_x": win_x,
        "win_y": win_y,
        "win_w": win_size_data.get("width", 0),
        "win_h": win_size_data.get("height", 0),
        "ime_active": ime_active,
        "app_context": app_context,
        "window_name": window_name,
        "command_line": command_line,
        "integrated_event": integrated_event
    }
    
    return workflow_info

def generate_macro_workflow(
    workflow_id: str, 
    config: AppConfig, 
    progress_callback: Optional[Callable[[int, str], None]] = None,
    check_cancel_callback: Optional[Callable[[], bool]] = None
) -> None:
    logger.info(f"[{workflow_id}] Starting log integration and AI workflow generation...")
    
    generation_debug_log = {
        "workflow_id": workflow_id,
        "start_time": datetime.now().isoformat(),
        "stages": [],
        "errors": []
    }
    
    try:
        if progress_callback:
            progress_callback(0, "初期化中... ワークフローディレクトリの確認")
        generation_debug_log["stages"].append({"name": "Initialization", "status": "started"})

        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id
        temp_dir = target_dir / "temp"
        input_logs_path = temp_dir / "input_logs.json"

        if not input_logs_path.exists():
            raise FileNotFoundError(f"Missing input_logs.json at {input_logs_path}")

        with open(input_logs_path, 'r', encoding='utf-8') as f:
            raw_logs = json.load(f)

        log_entries = []
        if isinstance(raw_logs, dict) and "Logs" in raw_logs:
            log_entries = raw_logs["Logs"]
        elif isinstance(raw_logs, dict) and "source_logs" in raw_logs and "Logs" in raw_logs["source_logs"]:
            log_entries = raw_logs["source_logs"]["Logs"]
        elif isinstance(raw_logs, list):
            log_entries = raw_logs
        else:
            raise ValueError(f"Unsupported JSON structure: {type(raw_logs)}")

        total_events = len(log_entries)
        if progress_callback:
            progress_callback(2, f"入力ログの読み込み完了... ({total_events}件のイベントを処理します)")
        generation_debug_log["stages"].append({"name": "Load Logs", "event_count": total_events})

        integrated_events = []
        temp_workflow_info: List[Dict[str, Any]] = []

        for i, log_entry in enumerate(log_entries):
            if check_cancel_callback and check_cancel_callback():
                logger.info(f"[{workflow_id}] Generation cancelled by user.")
                raise InterruptedError("Generation cancelled by user")

            parsed_info = _parse_raw_event(log_entry, i, total_events, workflow_id, macros_root, progress_callback)
            if parsed_info:
                integrated_events.append(parsed_info.pop("integrated_event"))
                temp_workflow_info.append(parsed_info)

        variables = {}
        if progress_callback:
            progress_callback(70, "入力ログの最適化... 文字入力バッファの集約とUIAレスキュー")
        generation_debug_log["stages"].append({"name": "Typing Aggregation", "status": "started"})

        try:
            from core.generator.typing_aggregator import TypingSessionAggregator
            aggregator = TypingSessionAggregator(session_timeout_ms=2000)
            
            temp_workflow_info = aggregator.aggregate_events(temp_workflow_info)
            
            logger.info(f"[{workflow_id}] キー入力集約完了: 最適化後のステップ数 = {len(temp_workflow_info)}")
        except Exception as e:
            logger.error(f"[{workflow_id}] 文字入力集約処理でエラーが発生しました: {e}")

        if progress_callback:
            progress_callback(75, "Officeイベントの統合とクリーンアップ中...")
        generation_debug_log["stages"].append({"name": "Office Event Integration", "status": "started"})

        # --- Officeイベントの統合とクリーンアップ ---
        cleaned_workflow_info = []
        for info in temp_workflow_info:
            if info["raw_action"] == "office_event":
                msg = info.get("inputValue", "")
                import re
                if "入力確定" in msg:
                    match = re.search(r"セル:\s*([^\s|]+)\s*\|\s*値:\s*(.+)", msg)
                    if match:
                        cell = match.group(1).replace("$", "")
                        val = match.group(2).strip()
                        if val.endswith(".0"):
                            val = val[:-2]
                        
                        # 直近の文字入力を探して正確な値とセル情報で上書きする
                        for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                            prev_info = cleaned_workflow_info[i]
                            if prev_info["raw_action"] in ["key_down", "type_text"]:
                                role = str(prev_info.get("semantic_role", "")).lower()
                                if role not in ["enter", "tab", "esc", "backspace", "delete"] and not role.startswith("key."):
                                    prev_info["semantic_role"] = val
                                    prev_info["raw_action"] = "type_text"
                                    prev_info["excel_cell"] = cell
                                    break
                elif "選択移動" in msg:
                    match = re.search(r"セル:\s*([^\s|]+)", msg)
                    if match:
                        cell = match.group(1).replace("$", "")
                        for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                            prev_info = cleaned_workflow_info[i]
                            if prev_info["raw_action"] in ["key_down", "click"]:
                                prev_info["excel_dest_cell"] = cell
                                break
                # office_event自体は実行コマンドにならないため除外
                continue
            
            cleaned_workflow_info.append(info)
            
        temp_workflow_info = cleaned_workflow_info

        if progress_callback:
            progress_callback(80, "ループブロックの解析と重複排除・差分抽出中...")
        generation_debug_log["stages"].append({"name": "Loop Analysis", "status": "started"})

        # --- ループブロックの解析と重複排除・差分抽出 ---
        optimized_workflow_info = []
        idx = 0
        while idx < len(temp_workflow_info):
            info = temp_workflow_info[idx]
            if info.get("raw_type", "").lower() == "meta_loop_start":
                optimized_workflow_info.append(info)
                
                loop_events = []
                j = idx + 1
                while j < len(temp_workflow_info) and temp_workflow_info[j].get("raw_type", "").lower() != "meta_loop_end":
                    loop_events.append(temp_workflow_info[j])
                    j += 1
                
                # 周期の推定とノイズ排除 (シーケンスアライメントによるスコアリング)
                import difflib
                
                actions = [e["raw_action"] for e in loop_events]
                n = len(actions)
                best_period = 0
                best_score = 0.0
                best_first_iter = []
                best_second_iter = []
                
                # 周期の候補を探す (長さ2から N/2 まで)
                for p in range(2, n // 2 + 1):
                    template = actions[:p]
                    target = actions[p:p*2]
                    sm = difflib.SequenceMatcher(None, template, target)
                    score = sm.ratio()
                    
                    # ユーザーの誤操作を考慮し、完全一致でなくてもスコアが高ければ候補とする
                    if score > best_score and score >= 0.6:
                        best_score = score
                        best_period = p
                        
                        first_iter = []
                        second_iter = []
                        for tag, i1, i2, j1, j2 in sm.get_opcodes():
                            if tag in ['equal', 'replace']:
                                # 誤操作（insert, delete）は無視し、対応するアクションのみ抽出
                                for i, j in zip(range(i1, i2), range(j1, j2)):
                                    first_iter.append(loop_events[i])
                                    second_iter.append(loop_events[p + j])
                        
                        best_first_iter = first_iter
                        best_second_iter = second_iter

                y_offset = 0
                x_offset = 0
                
                if best_period > 0 and best_first_iter and best_second_iter:
                    first_iter = best_first_iter
                    second_iter = best_second_iter
                    
                    for k in range(len(first_iter)):
                        # 座標の差分抽出
                        if first_iter[k].get("raw_action") in ["click", "move"] and second_iter[k].get("raw_action") == first_iter[k].get("raw_action"):
                            diff_y = second_iter[k].get("cursor_y", 0) - first_iter[k].get("cursor_y", 0)
                            diff_x = second_iter[k].get("cursor_x", 0) - first_iter[k].get("cursor_x", 0)
                            if 10 < abs(diff_y) < 200:
                                y_offset = diff_y
                            if 10 < abs(diff_x) < 200:
                                x_offset = diff_x
                                
                        # 連続値（連番）の抽出
                        if first_iter[k].get("raw_action") == "type_text" and second_iter[k].get("raw_action") == "type_text":
                            val1 = first_iter[k].get("semantic_role")
                            val2 = second_iter[k].get("semantic_role")
                            try:
                                if val1 is not None and val2 is not None:
                                    num1 = int(val1)
                                    num2 = int(val2)
                                    if num2 - num1 != 0:
                                        first_iter[k]["sequence_value"] = {"start": num1, "step": num2 - num1}
                                        first_iter[k]["is_sequence"] = True
                            except (ValueError, TypeError):
                                pass
                                
                    info["loop_variables"] = {"y_offset": y_offset, "x_offset": x_offset}
                    optimized_workflow_info.extend(first_iter)
                    logger.info(f"[{workflow_id}] Loop pattern detected (score={best_score:.2f}, period={best_period}). Initial actions kept. Offsets: y={y_offset}, x={x_offset}")
                else:
                    info["loop_variables"] = {"y_offset": 0, "x_offset": 0}
                    optimized_workflow_info.extend(loop_events)
                    logger.info(f"[{workflow_id}] Could not determine loop period. Keeping all events.")
                
                if j < len(temp_workflow_info):
                    optimized_workflow_info.append(temp_workflow_info[j])
                else:
                    # 記録終了時に「繰り返し終了」が押されなかった場合の自動補完
                    optimized_workflow_info.append({
                        "raw_type": "meta_loop_end",
                        "raw_action": "unknown",
                        "event_id": "auto_loop_end",
                        "semantic_role": "",
                        "window_name": info.get("window_name", "")
                    })
                idx = j + 1
            else:
                optimized_workflow_info.append(info)
                idx += 1
                
        temp_workflow_info = optimized_workflow_info
        
        # --- 変数化処理 (ループ解析後) ---
        for info in temp_workflow_info:
            if info.get("raw_action") == "type_text" and info.get("semantic_role"):
                if info.get("is_sequence"):
                    continue # 連番として抽出されたものは変数化しない
                    
                role_str = str(info["semantic_role"])
                role_lower = role_str.lower()
                
                if role_lower not in ["enter", "tab", "esc", "backspace", "delete"] and not role_lower.startswith("key."):
                    var_name = f"search_query_{len(variables) + 1}"
                    variables[var_name] = role_str
                    info["semantic_role"] = f"{{{{{var_name}}}}}"
        # ------------------------------------------------

        if progress_callback:
            progress_callback(85, "ワークフロー生成中... アクションの最適化とマッピング")
        generation_debug_log["stages"].append({"name": "Workflow Mapping", "status": "started"})

        llm_enhanced_data = {} 

        workflow_steps = []
        ui_targets_dict = {}
        
        start_time = integrated_events[0].timestamp if integrated_events else 0
        end_time = integrated_events[-1].timestamp if integrated_events else 0
        
        screen_size = Size(width=1920, height=1080)
        
        step_idx = 1
        prev_window_name = None
        prev_win_rect = None
        
        for info in temp_workflow_info:
            raw_action = info["raw_action"]
            raw_type = info["raw_type"].lower()
            
            if raw_action in ["unknown", "uia_scan"] or "recording" in raw_type:
                continue

            event_id = info["event_id"]
            fallback_evts = info.get("fallback_events", [event_id])
            
            # --- ウィンドウアクティブ化コマンドの自動挿入（サイズと座標情報をJSONで付与） ---
            current_window = next((e.window.name for e in integrated_events if e.id == event_id), "Unknown")
            
            if raw_type == "meta_loop_start":
                loop_vars = info.get("loop_variables", {"y_offset": 30})
                workflow_steps.append(WorkflowStep(
                    step_id=step_idx,
                    intent="START_LOOP",
                    description="Start repeating actions.",
                    context=WorkflowStepContext(active_window_name=current_window),
                    action=WorkflowCommandAction(
                        command="LOOP_START",
                        parameters=ActionParameters(loop_count=10, loop_variables=loop_vars)
                    ),
                    fallback_raw_events=[event_id]
                ))
                step_idx += 1
                continue
                
            elif raw_type == "meta_loop_end":
                workflow_steps.append(WorkflowStep(
                    step_id=step_idx,
                    intent="END_LOOP",
                    description="End repeating actions.",
                    context=WorkflowStepContext(active_window_name=current_window),
                    action=WorkflowCommandAction(
                        command="LOOP_END",
                        parameters=ActionParameters()
                    ),
                    fallback_raw_events=[event_id]
                ))
                step_idx += 1
                continue

            if current_window != "Unknown":
                win_ctx = next((e.window for e in integrated_events if e.id == event_id), None)
                win_x = win_ctx.coordinates.x if win_ctx else info.get("win_x", 0)
                win_y = win_ctx.coordinates.y if win_ctx else info.get("win_y", 0)
                win_w = win_ctx.size.width if win_ctx else info.get("win_w", 0)
                win_h = win_ctx.size.height if win_ctx else info.get("win_h", 0)
                
                current_win_rect = (win_x, win_y, win_w, win_h)
                
                needs_activation = False
                if current_window != prev_window_name:
                    needs_activation = True
                elif prev_win_rect:
                    px, py, pw, ph = prev_win_rect
                    if abs(win_x - px) > 10 or abs(win_y - py) > 10 or abs(win_w - pw) > 10 or abs(win_h - ph) > 10:
                        needs_activation = True
                        
                if needs_activation:
                    command_line = info.get("command_line", "")
                    win_info_json = json.dumps({
                        "title": current_window,
                        "x": win_x,
                        "y": win_y,
                        "width": win_w,
                        "height": win_h,
                        "launch_cmd": command_line
                    }, ensure_ascii=False)

                    workflow_steps.append(WorkflowStep(
                        step_id=step_idx,
                        intent="ACTIVATE_WINDOW",
                        description=f"Activate and resize window: {current_window}",
                        context=WorkflowStepContext(active_window_name=current_window),
                        action=WorkflowCommandAction(
                            command="ACTIVATE_WINDOW",
                            parameters=ActionParameters(text=win_info_json)
                        ),
                        fallback_raw_events=[event_id]
                    ))
                    step_idx += 1
                    prev_window_name = current_window
                    prev_win_rect = current_win_rect
            # ------------------------------------------------
            
            final_semantic_role = llm_enhanced_data.get(event_id, info["semantic_role"])
            
            target_id = None
            if raw_action in ["click", "move", "type_text", "key_down"]:
                target_id = f"tgt_{step_idx}"
                ui_targets_dict[target_id] = {
                    "semantic_role": final_semantic_role,
                    "ui_type": info.get("ui_type", "unknown")
                }
            
            if raw_action == "click":
                cmd = "MOUSE_CLICK"
                intent = "CLICK_UI_ELEMENT"
                desc = f"Click on the {final_semantic_role} element."
                params = ActionParameters(
                    target=UniversalSelector(semantic_role=final_semantic_role),
                    button=info["button"]
                )
            elif raw_action == "move":
                cmd = "MOUSE_MOVE"
                intent = "MOVE_CURSOR"
                desc = f"Move cursor to the {final_semantic_role} element."
                params = ActionParameters(
                    target=UniversalSelector(semantic_role=final_semantic_role)
                )
            elif raw_action == "type_text":
                cmd = "TYPE_TEXT"
                intent = "INPUT_TEXT"
                desc = f"Type the text: '{final_semantic_role}'"
                params = ActionParameters(text=final_semantic_role)
                if "sequence_value" in info:
                    params.sequence_value = info["sequence_value"]
            elif raw_action == "scroll":
                cmd = "MOUSE_SCROLL"
                intent = "SCROLL_WINDOW"
                desc = f"Scroll window (dx: {info['dx']}, dy: {info['dy']})"
                cursor_x = info.get("cursor_x", 0)
                cursor_y = info.get("cursor_y", 0)
                params = ActionParameters(text=f"{info['dx']},{info['dy']},{cursor_x},{cursor_y}")
            else:
                role_lower = final_semantic_role.lower() if final_semantic_role else ""
                is_special_key = False
                parsed_key = role_lower
                
                if role_lower.startswith("key."):
                    is_special_key = True
                    parsed_key = role_lower.replace("key.", "")
                elif role_lower in ["enter", "space", "tab", "esc", "backspace", "delete", "shift", "ctrl", "alt", "cmd", "win", "windows", "up", "down", "left", "right"] or "+" in role_lower:
                    is_special_key = True

                if is_special_key:
                    cmd = "KEYBOARD_SHORTCUT"
                    intent = "PRESS_SPECIAL_KEY"
                    desc = f"Press the {parsed_key} key."
                    params = ActionParameters(key=parsed_key)
                else:
                    cmd = "TYPE_TEXT"
                    intent = "INPUT_TEXT"
                    desc = f"Type the text: '{final_semantic_role}'"
                    params = ActionParameters(text=final_semantic_role)

            if "excel_cell" in info:
                params.excel_cell = info["excel_cell"]
            if "excel_dest_cell" in info:
                params.excel_dest_cell = info["excel_dest_cell"]

            step_context = WorkflowStepContext(
                active_window_name=current_window
            )

            workflow_steps.append(WorkflowStep(
                step_id=step_idx,
                intent=intent,
                description=desc,
                context=step_context,
                action=WorkflowCommandAction(command=cmd, parameters=params),
                fallback_raw_events=fallback_evts
            ))
            step_idx += 1

        workflow = Workflow(
            version="2.0",
            workflow_ID=workflow_id,
            metadata=WorkflowMetadata(
                os="Windows",
                resolution=screen_size,
                duration_ms=max(0, end_time - start_time)
            ),
            steps=workflow_steps
        )

        if progress_callback:
            progress_callback(90, "ファイル出力中... integrated.json / workflow.json / ui_targets.json")

        integrated_path = target_dir / "integrated.json"
        workflow_path = target_dir / "workflow.json"
        variables_path = target_dir / "variables.json"
        ui_targets_path = target_dir / "ui_targets.json"

        with open(integrated_path, 'w', encoding='utf-8') as f:
            json.dump([evt.model_dump() for evt in integrated_events], f, indent=4, ensure_ascii=False)

        with open(workflow_path, 'w', encoding='utf-8') as f:
            f.write(workflow.model_dump_json(indent=4))
            
        with open(variables_path, 'w', encoding='utf-8') as f:
            json.dump(variables, f, indent=4, ensure_ascii=False)
            
        with open(ui_targets_path, 'w', encoding='utf-8') as f:
            json.dump(ui_targets_dict, f, indent=4, ensure_ascii=False)

        logger.info(f"[{workflow_id}] Successfully generated integrated, workflow v2.0, variables, and ui_targets.json.")

        if progress_callback:
            progress_callback(95, "実行エンジンのビルド中... executable_macro.json の決定論的生成")
        generation_debug_log["stages"].append({"name": "Executable Macro Build", "status": "started"})

        try:
            raw_commands_data = []
            prev_timestamp = None
            
            for step in workflow_steps:
                raw_event_id = step.fallback_raw_events[0] if step.fallback_raw_events else None
                integ_evt = next((e for e in integrated_events if e.id == raw_event_id), None)
                
                cmd_type = step.action.command
                
                if not integ_evt and cmd_type != "LOOP_END":
                    continue

                current_timestamp = integ_evt.timestamp if integ_evt else prev_timestamp or 0
                if prev_timestamp is not None:
                    duration = (current_timestamp - prev_timestamp) / 1000.0
                    if duration > 0.01:
                        duration = min(duration, 1.5)
                        raw_commands_data.append({
                            "method": "wait",
                            "args": {"duration": round(duration, 3)}
                        })
                prev_timestamp = current_timestamp
                
                params = step.action.parameters
                
                target_id_for_healer = None

                # --- 実行用コマンドの生成 ---
                if cmd_type == "ACTIVATE_WINDOW":
                    if params.text:
                        try:
                            win_info = json.loads(params.text)
                            window_title = win_info.get("title", "")
                            win_x = win_info.get("x", 0)
                            win_y = win_info.get("y", 0)
                            win_w = win_info.get("width", 0)
                            win_h = win_info.get("height", 0)
                            launch_cmd = win_info.get("launch_cmd", "")
                            
                            raw_commands_data.append({
                                "method": "activate_window",
                                "args": {
                                    "window_title": window_title,
                                    "x": win_x,
                                    "y": win_y,
                                    "width": win_w,
                                    "height": win_h,
                                    "launch_cmd": launch_cmd,
                                    "target_id": target_id_for_healer,
                                    "raw_event_id": raw_event_id
                                }
                            })
                        except Exception as e:
                            logger.warning(f"Failed to parse ACTIVATE_WINDOW params: {e}")
                elif cmd_type == "MOUSE_CLICK":
                    if integ_evt.window.UIs and integ_evt.window.UIs[0].action and integ_evt.window.UIs[0].action.cursorRelativeCoordinates:
                        win_c = integ_evt.window.coordinates
                        rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
                        cmd_args = {
                            "x": win_c.x + rel_c.x,
                            "y": win_c.y + rel_c.y,
                            "button": params.button or "left",
                            "clicks": 1,
                            "target_id": target_id_for_healer,
                            "raw_event_id": raw_event_id
                        }
                        if params.excel_dest_cell:
                            cmd_args["excel_dest_cell"] = params.excel_dest_cell
                        raw_commands_data.append({
                            "method": "click",
                            "args": cmd_args
                        })
                elif cmd_type == "MOUSE_MOVE":
                    if integ_evt.window.UIs and integ_evt.window.UIs[0].action and integ_evt.window.UIs[0].action.cursorRelativeCoordinates:
                        win_c = integ_evt.window.coordinates
                        rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
                        cmd_args = {
                            "x": win_c.x + rel_c.x,
                            "y": win_c.y + rel_c.y,
                            "target_id": target_id_for_healer,
                            "raw_event_id": raw_event_id
                        }
                        if params.excel_dest_cell:
                            cmd_args["excel_dest_cell"] = params.excel_dest_cell
                        raw_commands_data.append({
                            "method": "move",
                            "args": cmd_args
                        })
                elif cmd_type == "MOUSE_SCROLL":
                    if params.text:
                        try:
                            parts = params.text.split(',')
                            dx_val = float(parts[0])
                            dy_val = float(parts[1])
                            x_val = float(parts[2]) if len(parts) > 2 else 0.0
                            y_val = float(parts[3]) if len(parts) > 3 else 0.0
                            raw_commands_data.append({
                                "method": "scroll",
                                "args": {
                                    "dx": dx_val,
                                    "dy": dy_val,
                                    "x": int(x_val),
                                    "y": int(y_val)
                                }
                            })
                        except Exception:
                            pass
                elif cmd_type == "KEYBOARD_SHORTCUT":
                    if params.key:
                        raw_commands_data.append({
                            "method": "press_key",
                            "args": {
                                "key": params.key,
                                "target_id": target_id_for_healer,
                                "raw_event_id": raw_event_id
                            }
                        })
                elif cmd_type == "TYPE_TEXT":
                    if params.text:
                        cmd_args = {
                            "text": params.text,
                            "target_id": target_id_for_healer,
                            "raw_event_id": raw_event_id
                        }
                        if params.sequence_value:
                            cmd_args["sequence_value"] = params.sequence_value
                        if params.excel_cell:
                            cmd_args["excel_cell"] = params.excel_cell
                            
                        raw_commands_data.append({
                            "method": "type_text",
                            "args": cmd_args
                        })
                elif cmd_type == "LOOP_START":
                    raw_commands_data.append({
                        "method": "loop_start",
                        "args": {
                            "loop_count": params.loop_count or 10,
                            "loop_variables": params.loop_variables or {}
                        }
                    })
                elif cmd_type == "LOOP_END":
                    raw_commands_data.append({
                        "method": "loop_end",
                        "args": {}
                    })

            commands_data = []
            for cmd in raw_commands_data:
                if not commands_data:
                    commands_data.append(cmd)
                    continue
                
                if cmd["method"] == "scroll":
                    merged = False
                    last_cmd = commands_data[-1]
                    
                    if last_cmd["method"] == "scroll":
                        if last_cmd["args"]["x"] == cmd["args"]["x"] and last_cmd["args"]["y"] == cmd["args"]["y"]:
                            last_cmd["args"]["dx"] = round(last_cmd["args"]["dx"] + cmd["args"]["dx"], 2)
                            last_cmd["args"]["dy"] = round(last_cmd["args"]["dy"] + cmd["args"]["dy"], 2)
                            merged = True
                    elif last_cmd["method"] == "wait" and len(commands_data) >= 2:
                        prev_cmd = commands_data[-2]
                        if prev_cmd["method"] == "scroll":
                            if last_cmd["args"]["duration"] < 1.0 and prev_cmd["args"]["x"] == cmd["args"]["x"] and prev_cmd["args"]["y"] == cmd["args"]["y"]:
                                prev_cmd["args"]["dx"] = round(prev_cmd["args"]["dx"] + cmd["args"]["dx"], 2)
                                prev_cmd["args"]["dy"] = round(prev_cmd["args"]["dy"] + cmd["args"]["dy"], 2)
                                commands_data.pop()
                                merged = True
                                
                    if not merged:
                        commands_data.append(cmd)
                else:
                    commands_data.append(cmd)

            exec_macro_dict = {
                "macro_id": workflow_id,
                "target_application": "auto_generated",
                "commands": commands_data
            }
            
            executable_macro_path = target_dir / "executable_macro.json"
            with open(executable_macro_path, 'w', encoding='utf-8') as f:
                json.dump(exec_macro_dict, f, indent=4, ensure_ascii=False)
                
            logger.info(f"[{workflow_id}] Successfully generated executable_macro.json deterministically.")

        except Exception as e:
             logger.error(f"[{workflow_id}] Error generating Executable Macro: {e}")

        generation_debug_log["end_time"] = datetime.now().isoformat()
        generation_debug_log["status"] = "success"
        try:
            with open(target_dir / "generation_log.json", 'w', encoding='utf-8') as f:
                json.dump(generation_debug_log, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

        if progress_callback:
            progress_callback(100, "完了")

    except Exception as e:
        generation_debug_log["errors"].append(str(e))
        generation_debug_log["status"] = "failed"
        try:
            with open(target_dir / "generation_log.json", 'w', encoding='utf-8') as f:
                json.dump(generation_debug_log, f, indent=4, ensure_ascii=False)
        except Exception:
            pass
        logger.error(f"[{workflow_id}] Failed to generate macro workflow: {e}")
        raise