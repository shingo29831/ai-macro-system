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
        
        # 背景: RETR_EXTERNALからRETR_LISTに変更し、内部のロゴ要素等も個別に取得する
        contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        
        valid_bboxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w > 5 and h > 5 and w < max_w * 0.8 and h < max_h * 0.5:
                valid_bboxes.append((x, y, x+w, y+h))
                
        # 背景: 包含している側のUI(親となる外枠)を切り捨てる
        filtered_bboxes = []
        for i, (x1, y1, x2, y2) in enumerate(valid_bboxes):
            is_enclosing = False
            for j, (nx1, ny1, nx2, ny2) in enumerate(valid_bboxes):
                if i == j: continue
                # 完全に内包しているか判定
                if x1 <= nx1 and y1 <= ny1 and x2 >= nx2 and y2 >= ny2:
                    if (x2 - x1) > (nx2 - nx1) or (y2 - y1) > (ny2 - ny1):
                        is_enclosing = True
                        break
            if not is_enclosing:
                filtered_bboxes.append((x1, y1, x2, y2))
                
        merged_bboxes = []
        for bbox in filtered_bboxes:
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
    
    active_search_areas = [(i, sa) for i, sa in enumerate(search_areas)] if search_areas else []
    
    def _evaluate_frame(img_path_rel: str, buffer_to_check: str, is_ime: bool, is_final_candidate: bool = False):
        nonlocal ocr_call_count, active_search_areas
        if not buffer_to_check.strip():
            return
            
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

                len_penalty = 0.0
                change_penalty = 0.0
                
                if not is_final_candidate:
                    target_len = len(target_lower) if is_ime else len(buffer_lower)
                    ocr_len = len(c_lower)
                    len_diff = abs(target_len - ocr_len)
                    len_penalty = (len_diff / max(1, target_len)) * 0.3
                    len_penalty = min(0.8, len_penalty)

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
                
                logger.info(f"[{target_event_id}] Scoring OCR - Found: '{res.content}', Score: {frame_score:.2f} (Sim: {similarity:.2f}, Pen: {dist_penalty:.2f}, Chg: {change_penalty:.2f}, Len: {len_penalty:.2f}), Target: '{target_lower}' (Final: {is_final_candidate})")
                
                if similarity > 0.3 or is_substring or is_final_candidate:
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

            if active_search_areas and field_candidates and not is_final_candidate:
                global_max_score = max(cand["score"] for cand in field_candidates)
                
                surviving_areas = []
                for idx, s_area in active_search_areas:
                    sl, st, sr, sb = s_area
                    area_max = -float('inf')
                    for cand in field_candidates:
                        cl, ct, cr, cb = cand["bbox"]
                        cx, cy = (cl + cr) / 2, (ct + cb) / 2
                        if sl - 50 <= cx <= sr + 50 and st - 50 <= cy <= sb + 50:
                            area_max = max(area_max, cand["score"])
                    
                    if global_max_score - area_max <= 3.0:
                        surviving_areas.append((idx, s_area))
                    else:
                        logger.info(f"[{target_event_id}] Pruning search area {idx} (Area Max: {area_max:.2f}, Global Max: {global_max_score:.2f})")
                
                active_search_areas = surviving_areas

        except Exception as e:
            logger.warning(f"Error during OCR tracking evaluation: {e}")

    for event in group_events:
        img_path_rel = event.get("pre_img_path")
        is_ime = event.get("ime_active", False)
        
        if img_path_rel:
            _evaluate_frame(img_path_rel, current_buffer, is_ime, is_final_candidate=False)
            
        role = str(event.get("semantic_role", "")).lower()
        if role == "backspace":
            current_buffer = current_buffer[:-1]
        elif role == "space":
            current_buffer += " "
        elif len(role) == 1:
            current_buffer += role

    final_ime_state = any(e.get("ime_active", False) for e in group_events)

    for cand_img_path in candidate_img_paths_rel:
        _evaluate_frame(cand_img_path, current_buffer, final_ime_state, is_final_candidate=final_ime_state)

    if not field_candidates:
        return None, ""
        
    best_candidate = max(field_candidates, key=lambda x: x["score"])
    l, t, r, b = best_candidate["bbox"]
    
    final_img_path_rel = candidate_img_paths_rel[-1]
    final_img_path = macros_root / final_img_path_rel
    base_img_path_rel = group_events[0].get("pre_img_path") if group_events else candidate_img_paths_rel[0]
    base_img_path = macros_root / base_img_path_rel

    if final_img_path.exists() and base_img_path.exists():
        try:
            img_base = cv2.imread(str(base_img_path), cv2.IMREAD_GRAYSCALE)
            img_final = cv2.imread(str(final_img_path), cv2.IMREAD_GRAYSCALE)
            
            diff = cv2.absdiff(img_base, img_final)
            _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
            
            # カーネルをタイトにし、ロゴと文字を結合させない
            kernel = np.ones((3, 10), np.uint8)
            dilated = cv2.dilate(thresh, kernel, iterations=1)
            
            # 背景: RETR_LISTを用いてすべての輪郭を抽出し、包含親UI（外枠）を切り捨てる
            contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            
            raw_bboxes = []
            for cnt in contours:
                x, y, w, h = cv2.boundingRect(cnt)
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
            
            candidate_contours = []
            for bbox in filtered_bboxes:
                x, y, w, h = bbox
                if max(l, x) < min(r, x + w) and max(t, y) < min(b, y + h):
                    candidate_contours.append((x, y, w, h))
            
            if candidate_contours:
                target_text_hira = to_hiragana(current_buffer) if final_ime_state else current_buffer
                target_lower = target_text_hira.lower()
                
                best_sub_bbox = None
                best_sub_score = -float('inf')
                
                with Image.open(final_img_path) as img_pil:
                    for s_bbox in candidate_contours:
                        sx, sy, sw, sh = s_bbox
                        pad = 2
                        c_img = img_pil.crop((max(0, sx-pad), max(0, sy-pad), min(img_pil.width, sx+sw+pad), min(img_pil.height, sy+sh+pad)))
                        tmp_path = temp_dir / f"temp_sub_bbox_eval_{target_event_id}_{sx}_{sy}.png"
                        c_img.save(tmp_path)
                        
                        s_ocr_res = read_text_from_image(str(tmp_path))
                        score = 0.0
                        if s_ocr_res:
                            text_content = "".join([res_item.content for res_item in s_ocr_res if res_item.content]).lower()
                            sim = Levenshtein.ratio(target_lower, text_content)
                            is_sub = target_lower in text_content
                            score = sim + (0.5 if is_sub else 0)
                        
                        if score > best_sub_score:
                            best_sub_score = score
                            best_sub_bbox = s_bbox
                
                if best_sub_bbox and best_sub_score > 0.0:
                    bx, by, bw, bh = best_sub_bbox
                    l, t, r, b = bx, by, bx + bw, by + bh
                    logger.info(f"[{target_event_id}] Logically expanded and refined BBox to fit final text (excluding encompassing UIs): {(l, t, r, b)}")
                    
        except Exception as e:
            logger.warning(f"[{target_event_id}] Failed to refine and expand final BBox: {e}")

    pad_x = 5
    pad_y = 5
    
    l_crop = max(0, l - pad_x)
    t_crop = max(0, t - pad_y)
    r_crop = r + pad_x
    b_crop = b + pad_y
    
    return (l_crop, t_crop, r_crop, b_crop), best_candidate["last_text"]

def generate_macro_workflow(
    workflow_id: str, 
    config: AppConfig, 
    progress_callback: Optional[Callable[[int, str], None]] = None,
    check_cancel_callback: Optional[Callable[[], bool]] = None
) -> None:
    logger.info(f"[{workflow_id}] Starting log integration and AI workflow generation...")
    
    try:
        if progress_callback:
            progress_callback(0, "初期化中... ワークフローディレクトリの確認")

        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id
        temp_dir = target_dir / "temp"
        input_logs_path = temp_dir / "input_logs.json"

        if not input_logs_path.exists():
            raise FileNotFoundError(f"Missing input_logs.json at {input_logs_path}")

        if progress_callback:
            progress_callback(2, "入力ログの読み込み中...")

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

        integrated_events = []
        temp_workflow_info: List[Dict[str, Any]] = []
        
        total_events = len(log_entries)

        with ThreadPoolExecutor(max_workers=4) as cv_executor:
            for i, log_entry in enumerate(log_entries):
                if check_cancel_callback and check_cancel_callback():
                    logger.info(f"[{workflow_id}] Generation cancelled by user.")
                    raise InterruptedError("Generation cancelled by user")

                if not isinstance(log_entry, dict):
                    continue
                    
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

                window_name = log_entry.get("WindowName") or "Unknown Window"
                win_size_data = log_entry.get("WindowSize") or {"width": 0, "height": 0}
                win_coord_data = log_entry.get("WindowCoordinates") or {"x": 0, "y": 0}
                
                win_x = win_coord_data.get("x", 0)
                win_y = win_coord_data.get("y", 0)

                raw_type = str(log_entry.get("Type", ""))
                content_data = log_entry.get("Content") or {}
                
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
                
                button_val = "left"
                input_val = "unknown"
                ime_active = False
                
                if isinstance(content_data, dict):
                    button_val = content_data.get("button", "left")
                    input_val = content_data.get("combo") or content_data.get("key") or content_data.get("text") or f"{button_val}_click"
                    ime_active = content_data.get("ime_active", False)
                else:
                    input_val = str(content_data)

                raw_type_lower = raw_type.lower()
                is_scroll = "scroll" in raw_type_lower
                is_move = "hover" in raw_type_lower or "move" in raw_type_lower
                is_click = ("click" in raw_type_lower or "mouse" in raw_type_lower) and not (is_scroll or is_move)
                is_key = "key" in raw_type_lower

                dx = 0.0
                dy = 0.0

                if is_scroll:
                    action_type = "scroll"
                    if isinstance(content_data, dict):
                        dx = content_data.get("dx", 0.0)
                        dy = content_data.get("dy", 0.0)
                elif is_move:
                    action_type = "move"
                elif is_click:
                    action_type = "click"
                elif is_key:
                    action_type = "key_down"
                else:
                    action_type = "unknown"

                if progress_callback:
                    progress = int((i / total_events) * 70)
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
                        continue
                    if diff_val < 0.001:
                        continue
                
                if crop_path_str and crop_path_str != "切り抜き失敗":
                    full_crop_path = macros_root / crop_path_str
                    if full_crop_path.exists():
                        logger.info(f"[{workflow_id}] Processing CV inference: {i+1}/{total_events} (Event: {event_id})...")
                        
                        future_yolo = cv_executor.submit(detect_ui_elements, str(full_crop_path))
                        future_ocr = cv_executor.submit(read_text_from_image, str(full_crop_path))
                        
                        yolo_results = future_yolo.result()
                        ocr_results = future_ocr.result()

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

                integrated_events.append(IntegratedEvent(
                    id=event_id,
                    timestamp=safe_timestamp,
                    window=window_context
                ))

                temp_workflow_info.append({
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
                    "ime_active": ime_active
                })

        if progress_callback:
            progress_callback(75, "入力ログの最適化... 変数候補の抽出とグループ化")

        variables = {}
        processed_info = []
        current_group = []

        def flush_group(current_index: int):
            if not current_group:
                return
            
            if len(current_group) == 1 and str(current_group[0]["semantic_role"]).lower() not in ["space", "tab", "backspace", "delete"]:
                processed_info.append(current_group[0])
                current_group.clear()
                return

            avg_diff = sum(item["diff_val"] for item in current_group) / len(current_group)
            
            if avg_diff < 0.3:
                base_text = ""
                any_ime_active = any(item.get("ime_active", False) for item in current_group)
                for item in current_group:
                    role = str(item["semantic_role"])
                    r_lower = role.lower()
                    if r_lower == "backspace":
                        base_text = base_text[:-1]
                    elif r_lower == "space":
                        base_text += " "
                    elif r_lower not in ["tab", "delete", "esc"]:
                        base_text += role
                
                try:
                    from core.recorder.romaji_converter import to_hiragana
                except ImportError:
                    to_hiragana = lambda x: x
                    
                target_lower = base_text.lower()
                hiragana_target = to_hiragana(target_lower) if any_ime_active else target_lower
                
                text = base_text
                
                group_img_paths_rel = []
                for item in current_group:
                    if item.get("pre_img_path"):
                        group_img_paths_rel.append(item["pre_img_path"])

                candidate_img_paths_rel = []
                if len(current_group) > 0 and current_group[-1].get("pre_img_path"):
                    candidate_img_paths_rel.append(current_group[-1]["pre_img_path"])
                    
                if current_index < len(temp_workflow_info):
                    next_evt_img = temp_workflow_info[current_index].get("pre_img_path")
                    if next_evt_img and next_evt_img not in candidate_img_paths_rel:
                        candidate_img_paths_rel.append(next_evt_img)

                if any_ime_active:
                    for idx in range(current_index + 1, min(current_index + 3, len(temp_workflow_info))):
                        evt = temp_workflow_info[idx]
                        p = evt.get("pre_img_path")
                        if p and p not in candidate_img_paths_rel:
                            candidate_img_paths_rel.append(p)

                crop_box = None
                tracked_text = ""
                base_target_full = macros_root / candidate_img_paths_rel[0] if candidate_img_paths_rel else None
                
                target_event_id = current_group[-1]['event_id']

                if progress_callback:
                    prog_val = 75 + int((current_index / max(1, len(temp_workflow_info))) * 4)
                    progress_callback(prog_val, f"テキストフィールド追跡中... ({current_index}/{len(temp_workflow_info)})")

                search_areas = []
                last_click_pos = None
                
                last_click = next((item for item in reversed(processed_info) if item["raw_action"] == "click"), None)
                if last_click:
                    last_click_pos = (last_click.get("cursor_x", 0), last_click.get("cursor_y", 0))

                if base_target_full and base_target_full.exists():
                    try:
                        with Image.open(base_target_full) as img_temp:
                            max_w, max_h = img_temp.width, img_temp.height
                            
                        full_img_paths = [macros_root / p for p in group_img_paths_rel if (macros_root / p).exists()]
                        
                        if len(full_img_paths) >= 2:
                            diff_bboxes = get_text_field_bboxes_cv(full_img_paths, max_w, max_h)
                            if diff_bboxes:
                                raw_search_areas = []
                                for dbbox in diff_bboxes:
                                    dl, dt, dr, db = dbbox
                                    raw_search_areas.append((max(0, dl - 30), max(0, dt - 20), min(max_w, dr + 400), min(max_h, db + 50)))
                                
                                merged_areas = []
                                for rect in raw_search_areas:
                                    x1, y1, x2, y2 = rect
                                    has_merged = True
                                    while has_merged:
                                        has_merged = False
                                        for i, (mx1, my1, mx2, my2) in enumerate(merged_areas):
                                            if not (x2 < mx1 or x1 > mx2 or y2 < my1 or y1 > my2):
                                                new_rect = (min(x1, mx1), min(y1, my1), max(x2, mx2), max(y2, my2))
                                                merged_areas.pop(i)
                                                x1, y1, x2, y2 = new_rect
                                                has_merged = True
                                                break
                                    merged_areas.append((x1, y1, x2, y2))
                                search_areas = merged_areas
                                
                    except Exception as e:
                        logger.warning(f"[{workflow_id}] search_area calculation failed: {e}")

                if base_target_full and base_target_full.exists():
                    try:
                        crop_box, tracked_text = track_text_field_by_scoring(current_group, candidate_img_paths_rel, macros_root, temp_dir, target_event_id, search_areas, last_click_pos)
                    except Exception as e:
                        logger.error(f"[{workflow_id}] Tracking error: {e}")
                    
                    if crop_box:
                        logger.info(f"[{workflow_id}] Text field identified by tracking score: BBox={crop_box}")
                    else:
                        logger.warning(f"[{workflow_id}] Tracking failed. Falling back to differential BBox extraction.")
                        try:
                            if search_areas and 'diff_bboxes' in locals() and diff_bboxes:
                                best_fallback_bbox = diff_bboxes[0]
                                if last_click_pos:
                                    best_dist = float('inf')
                                    for dbbox in diff_bboxes:
                                        cx = (dbbox[0] + dbbox[2]) / 2
                                        cy = (dbbox[1] + dbbox[3]) / 2
                                        dist = math.hypot(cx - last_click_pos[0], cy - last_click_pos[1])
                                        if dist < best_dist:
                                            best_dist = dist
                                            best_fallback_bbox = dbbox

                                refined_bbox, shape_info = refine_textfield_bbox_cv(str(base_target_full), best_fallback_bbox)
                                logger.info(f"[{workflow_id}] Text field refined via CV. Shape: {shape_info}, BBox: {refined_bbox}")
                                
                                l, t, r, b = refined_bbox
                                dl, dt, dr, db = best_fallback_bbox
                                char_h = db - dt if (db - dt) > 0 else 20
                                
                                margin_x, margin_y = 5, 5
                                max_v_margin = max(30, int(char_h * 1.5))
                                
                                t_crop = max(t, dt - max_v_margin)
                                b_crop = min(b, db + max_v_margin)
                                
                                crop_box = (max(0, l - margin_x), max(0, t_crop - margin_y), min(max_w, r + margin_x), min(max_h, b_crop + margin_y))
                        except Exception as e:
                            logger.warning(f"[{workflow_id}] Error in text field extraction: {e}")

                if not crop_box and base_target_full and base_target_full.exists():
                    if last_click_pos:
                        with Image.open(base_target_full) as img:
                            cx, cy = last_click_pos
                            left, top = max(0, cx - 300), max(0, cy - 100)
                            right, bottom = min(img.width, cx + 300), min(img.height, cy + 100)
                            crop_box = (left, top, right, bottom)
                            logger.info(f"[{workflow_id}] Using click location fallback box: {crop_box}")

                best_overall_text = ""
                
                for cand_path_rel in candidate_img_paths_rel:
                    cand_full = macros_root / cand_path_rel
                    if cand_full.exists() and crop_box:
                        try:
                            with Image.open(cand_full) as img:
                                left, top, right, bottom = crop_box
                                left, top = max(0, left), max(0, top)
                                right, bottom = min(img.width, right), min(img.height, bottom)
                                current_crop_box = (left, top, right, bottom)
                                
                                crop_img = img.crop(current_crop_box)
                                temp_crop_path = temp_dir / f"temp_ocr_crop_final_{target_event_id}_{Path(cand_path_rel).stem}.png"
                                crop_img.save(temp_crop_path)
                                
                                ocr_results = read_text_from_image(str(temp_crop_path))
                                if ocr_results:
                                    valid_results = [res for res in ocr_results if res.content]
                                        
                                    if valid_results:
                                        # 背景: OCR結果に対しても包含チェックを行い、全体を囲む誤認識の巨大枠(包含親UI)を切り捨てる
                                        filtered_ocr_results = []
                                        for i, res1 in enumerate(valid_results):
                                            bx1, by1 = res1.boundingBox.x, res1.boundingBox.y
                                            br1, bb1 = bx1 + res1.boundingBox.width, by1 + res1.boundingBox.height
                                            is_enclosing = False
                                            for j, res2 in enumerate(valid_results):
                                                if i == j: continue
                                                bx2, by2 = res2.boundingBox.x, res2.boundingBox.y
                                                br2, bb2 = bx2 + res2.boundingBox.width, by2 + res2.boundingBox.height
                                                if bx1 <= bx2 and by1 <= by2 and br1 >= br2 and bb1 >= bb2:
                                                    if (br1 - bx1) > (br2 - bx2) or (bb1 - by1) > (bb2 - by2):
                                                        is_enclosing = True
                                                        break
                                            if not is_enclosing:
                                                filtered_ocr_results.append(res1)
                                        
                                        filtered_ocr_results.sort(key=lambda r: r.boundingBox.x)
                                        
                                        combined_text = ""
                                        prev_right = -1
                                        
                                        for res in filtered_ocr_results:
                                            text_part = res.content.strip()
                                            bx = res.boundingBox.x
                                            bw = res.boundingBox.width
                                            bh = res.boundingBox.height
                                            
                                            if prev_right == -1:
                                                combined_text += text_part
                                                prev_right = bx + bw
                                            else:
                                                gap = bx - prev_right
                                                max_gap = max(20, bh * 1.5)
                                                
                                                if gap <= max_gap:
                                                    combined_text += text_part
                                                    prev_right = max(prev_right, bx + bw)
                                                else:
                                                    logger.info(f"[{workflow_id}] Spatial gap {gap} exceeded max_gap {max_gap} at text '{text_part}'. Stopping concatenation to exclude unrelated UI elements.")
                                                    break
                                                    
                                        if len(combined_text) > len(best_overall_text):
                                            best_overall_text = combined_text
                                            
                        except Exception as e:
                            logger.error(f"[{workflow_id}] Failed to extract text via cropped OCR for candidate: {e}")

                if best_overall_text:
                    text = best_overall_text
                    logger.info(f"[{workflow_id}] Final OCR extracted text from BBox: {text}")
                else:
                    logger.info(f"[{workflow_id}] Falling back to raw typed text: {base_text}")

                var_name = f"search_query_{len(variables) + 1}"
                variables[var_name] = text
                
                rep = current_group[0].copy()
                rep["semantic_role"] = f"{{{{{var_name}}}}}"
                rep["raw_action"] = "type_text"
                rep["fallback_events"] = [item["event_id"] for item in current_group]
                processed_info.append(rep)
            else:
                processed_info.extend(current_group)
            
            current_group.clear()

        for i, info in enumerate(temp_workflow_info):
            if info["raw_action"] == "unknown" or "recording" in info["raw_type"].lower():
                continue
                
            if info["raw_action"] == "key_down":
                role_lower = str(info["semantic_role"]).lower() if info["semantic_role"] else ""
                
                is_special = False
                is_text_modifier = False
                
                if role_lower.startswith("key."):
                    is_special = True
                elif role_lower in ["space", "tab", "backspace", "delete"]:
                    is_special = True
                    is_text_modifier = True
                elif role_lower in ["enter", "esc", "shift", "ctrl", "alt", "cmd", "win", "windows", "up", "down", "left", "right"]:
                    is_special = True
                
                if not is_special:
                    current_group.append(info)
                elif is_text_modifier and len(current_group) > 0:
                    current_group.append(info)
                else:
                    flush_group(i)
                    processed_info.append(info)
            else:
                flush_group(i)
                processed_info.append(info)
                
        flush_group(len(temp_workflow_info))
        temp_workflow_info = processed_info

        if progress_callback:
            progress_callback(80, "AI推論準備(LLM)... 文脈データの構築中")
            
        llm_client = LLMClient(host=config.llm_host, port=int(config.llm_port))
        llm_enhanced_data = {}
        
        try:
            summary_for_llm = [
                {"id": info["event_id"], "ui": info["ui_type"], "text": info["semantic_role"]} 
                for info in temp_workflow_info
                if info["raw_action"] in ["click", "move"]
            ]
            
            if summary_for_llm:
                llm_prompt = (
                    "Analyze the following UI interaction sequence. "
                    "Return a JSON array where each object contains the original 'id', and an improved 'semantic_role' "
                    "based on the context of the entire sequence.\n"
                    f"{json.dumps(summary_for_llm, ensure_ascii=False)}"
                )
                
                max_retries = 3
                is_valid_response = False
                
                for attempt in range(max_retries):
                    if progress_callback:
                        retry_text = f" (再生成 {attempt}/{max_retries})" if attempt > 0 else ""
                        progress_callback(80 + attempt * 2, f"AI推論中(LLM)... UIの役割を解釈中{retry_text}")

                    logger.info(f"[{workflow_id}] Sending prompt to LLM (Attempt {attempt+1}/{max_retries})...")
                    llm_response = llm_client.generate(prompt=llm_prompt)
                    
                    if llm_response and isinstance(llm_response, dict) and llm_response.get("success"):
                        resp_data = llm_response.get("response", {})
                        
                        content = ""
                        if isinstance(resp_data, dict) and "choices" in resp_data and len(resp_data["choices"]) > 0:
                            content = resp_data["choices"][0].get("message", {}).get("content", "")
                        elif isinstance(resp_data, str):
                            content = resp_data
                            
                        json_start = content.find('[')
                        json_end = content.rfind(']') + 1
                        
                        if progress_callback:
                            progress_callback(86 + attempt, f"AI推論の検証中(LLM)... ハルシネーション検査{retry_text}")

                        if json_start != -1 and json_end != -1:
                            try:
                                parsed_array = json.loads(content[json_start:json_end])
                                
                                if len(parsed_array) != len(summary_for_llm):
                                    raise ValueError(f"Array length mismatch. Expected {len(summary_for_llm)}, got {len(parsed_array)}")
                                
                                temp_enhanced_data = {}
                                for item in parsed_array:
                                    if "id" not in item or "semantic_role" not in item:
                                        raise ValueError("Missing 'id' or 'semantic_role' in JSON object")
                                    
                                    role = str(item["semantic_role"])
                                    temp_enhanced_data[item["id"]] = role
                                
                                llm_enhanced_data = temp_enhanced_data
                                is_valid_response = True
                                logger.info(f"[{workflow_id}] LLM inference successful and validated.")
                                break
                                
                            except json.JSONDecodeError:
                                logger.warning(f"[{workflow_id}] JSON parsing failed on attempt {attempt+1}")
                            except ValueError as ve:
                                logger.warning(f"[{workflow_id}] Validation failed on attempt {attempt+1}: {ve}")
                
                if not is_valid_response:
                    logger.warning(f"[{workflow_id}] All LLM retry attempts failed due to hallucination. Falling back to raw CV data.")
                    
        except Exception as e:
            logger.warning(f"[{workflow_id}] LLM inference encountered fatal error. Falling back to CV results. Error: {e}")

        if progress_callback:
            progress_callback(90, "ワークフロー生成中... アクションの最適化とマッピング")

        workflow_steps = []
        ui_targets_dict = {}
        
        start_time = integrated_events[0].timestamp if integrated_events else 0
        end_time = integrated_events[-1].timestamp if integrated_events else 0
        
        screen_size = Size(width=1920, height=1080)
        
        step_idx = 1
        for info in temp_workflow_info:
            raw_action = info["raw_action"]
            raw_type = info["raw_type"].lower()
            
            if raw_action == "unknown" or "recording" in raw_type:
                continue

            event_id = info["event_id"]
            fallback_evts = info.get("fallback_events", [event_id])
            
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
                elif role_lower in ["enter", "space", "tab", "esc", "backspace", "delete", "shift", "ctrl", "alt", "cmd", "win", "windows", "up", "down", "left", "right"]:
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

            step_context = WorkflowStepContext(
                active_window_name=next((e.window.name for e in integrated_events if e.id == event_id), "Unknown")
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
            progress_callback(95, "ファイル出力中... integrated.json / workflow.json / ui_targets.json")

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
            progress_callback(98, "実行エンジンのビルド中... executable_macro.json の決定論的生成")

        try:
            raw_commands_data = []
            prev_timestamp = None
            
            for step in workflow_steps:
                raw_event_id = step.fallback_raw_events[0] if step.fallback_raw_events else None
                integ_evt = next((e for e in integrated_events if e.id == raw_event_id), None)
                
                if not integ_evt:
                    continue

                current_timestamp = integ_evt.timestamp
                if prev_timestamp is not None:
                    duration = (current_timestamp - prev_timestamp) / 1000.0
                    if duration > 0.01:
                        duration = min(duration, 1.5)
                        raw_commands_data.append({
                            "method": "wait",
                            "args": {"duration": round(duration, 3)}
                        })
                prev_timestamp = current_timestamp
                
                cmd_type = step.action.command
                params = step.action.parameters
                target_id_for_healer = f"tgt_{step.step_id}"

                if cmd_type == "MOUSE_CLICK":
                    if integ_evt.window.UIs and integ_evt.window.UIs[0].action and integ_evt.window.UIs[0].action.cursorRelativeCoordinates:
                        win_c = integ_evt.window.coordinates
                        rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
                        raw_commands_data.append({
                            "method": "click",
                            "args": {
                                "x": win_c.x + rel_c.x,
                                "y": win_c.y + rel_c.y,
                                "button": params.button or "left",
                                "clicks": 1,
                                "target_id": target_id_for_healer,
                                "raw_event_id": raw_event_id
                            }
                        })
                elif cmd_type == "MOUSE_MOVE":
                    if integ_evt.window.UIs and integ_evt.window.UIs[0].action and integ_evt.window.UIs[0].action.cursorRelativeCoordinates:
                        win_c = integ_evt.window.coordinates
                        rel_c = integ_evt.window.UIs[0].action.cursorRelativeCoordinates
                        raw_commands_data.append({
                            "method": "move",
                            "args": {
                                "x": win_c.x + rel_c.x,
                                "y": win_c.y + rel_c.y,
                                "target_id": target_id_for_healer,
                                "raw_event_id": raw_event_id
                            }
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
                        raw_commands_data.append({
                            "method": "type_text",
                            "args": {
                                "text": params.text,
                                "target_id": target_id_for_healer,
                                "raw_event_id": raw_event_id
                            }
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

        if progress_callback:
            progress_callback(100, "完了")

    except Exception as e:
        logger.error(f"[{workflow_id}] Failed to generate macro workflow: {e}")
        raise