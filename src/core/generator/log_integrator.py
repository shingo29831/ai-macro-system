# shingo29831/ai-macro-system/ai-macro-system-Umeda/src/core/generator/log_integrator.py
# @role: temp/ に保存された一時生データ（入力ログ・画像）とローカルAI（YOLO/OCR/LLM）の解析結果を統合し、意味を理解した実行可能なワークフローを生成する。

import json
import logging
import shutil
import os
import statistics
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

def get_text_field_bbox_pil(images_paths: List[Path], max_w: int, max_h: int) -> Optional[Tuple[int, int, int, int]]:
    """
    複数枚のキー入力フレーム間のピクセル差分を計算し、文字入力領域を特定する。
    入力された文字の高さ（文字サイズ）を計算し、下部へのサジェストを排除する。
    """
    if len(images_paths) < 2:
        return None
        
    try:
        valid_bboxes = []
        for i in range(1, len(images_paths)):
            try:
                img1 = Image.open(images_paths[i-1]).convert("RGB")
                img2 = Image.open(images_paths[i]).convert("RGB")
                if img1.size != img2.size:
                    continue
                diff = ImageChops.difference(img1, img2)
                diff_bw = diff.convert("L").point(lambda p: 255 if p > 30 else 0)
                bbox = diff_bw.getbbox()
                if bbox:
                    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                    # 極端に巨大な画面変化（パネル出現など）は最初から無視
                    if w < max_w * 0.7 and h < max_h * 0.25:
                        valid_bboxes.append(bbox)
            except Exception:
                continue
                
        if not valid_bboxes:
            return None

        # 差分（＝入力された文字）の高さを収集し、文字サイズ（char_h）の中央値を算出
        heights = [b[3] - b[1] for b in valid_bboxes]
        char_h = statistics.median(heights) if heights else 20
        if char_h > 100: char_h = 30 # 誤検知対策
            
        min_x = min(b[0] for b in valid_bboxes)
        max_x = max(b[2] for b in valid_bboxes)
        min_y = min(b[1] for b in valid_bboxes)
        raw_max_y = max(b[3] for b in valid_bboxes)
        
        # サジェストやブラウザショートカットが差分に含まれてしまった場合を切り落とすため、
        # PILで検出する「文字入力領域」は文字サイズの最大4倍までに制限。
        max_y = min(raw_max_y, min_y + int(char_h * 4.0))
        
        if (max_x - min_x) > max_w * 0.8:
            last_b = valid_bboxes[-1]
            min_x = max(0, last_b[0] - 250)
            max_x = min(max_w, last_b[2] + 250)
            min_y = last_b[1]
            max_y = min(last_b[3], min_y + int(char_h * 4.0))
            
        return (min_x, min_y, max_x, max_y)
        
    except Exception as e:
        logger.warning(f"PIL cumulative diff failed: {e}")
        return None

def refine_textfield_bbox_cv(image_path: str, diff_bbox: Tuple[int, int, int, int]) -> Tuple[Tuple[int, int, int, int], str]:
    """
    OpenCVを用いてUIの枠線を抽出し、文字領域を包含する【最小の枠線】にスナップさせる。
    メモアプリなどの巨大な枠にも対応可能。
    """
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
        
        # 枠線は少なくとも入力された文字を包含できる高さが必要
        min_field_h = max(15, int(char_h * 1.0))
        
        best_bbox = diff_bbox
        best_shape = "text_area (no specific boundary found)"
        min_margin_area = float('inf')
        
        img_h, img_w = img.shape[:2]
        # ウィンドウの枠など、画面の90%以上を占める意味のない巨大枠は除外
        max_allowed_area = img_w * img_h * 0.9
        
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            
            # 文字より小さい枠は除外
            if h < min_field_h:
                continue
            
            # 【包含判定】入力文字領域(diff_bbox)を内包しているか
            # 枠線ギリギリに文字がある場合を考慮し、マージンを持たせる
            margin = 20
            if x <= dl + margin and y <= dt + margin and (x+w) >= dr - margin and (y+h) >= db - margin:
                area = w * h
                
                # ウィンドウ全体のような巨大すぎる枠は除外
                if area < max_allowed_area:
                    # 【最小包含枠の探索】文字領域を囲む枠の中で、最も面積が小さい(最も内側の)枠を採用
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
                
                if isinstance(content_data, dict):
                    button_val = content_data.get("button", "left")
                    input_val = content_data.get("combo") or content_data.get("key") or content_data.get("text") or f"{button_val}_click"
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
                    "win_h": win_size_data.get("height", 0)
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
                for item in current_group:
                    role = str(item["semantic_role"])
                    r_lower = role.lower()
                    if r_lower == "backspace":
                        base_text = base_text[:-1]
                    elif r_lower == "space":
                        base_text += " "
                    elif r_lower not in ["tab", "delete", "esc"]:
                        base_text += role
                
                text = base_text
                
                group_img_paths_rel = []
                for item in current_group:
                    if item.get("pre_img_path"):
                        group_img_paths_rel.append(item["pre_img_path"])

                # 確定後画像の取得 (Enterキーなどの後続イベント画像も候補とする)
                candidate_img_paths_rel = []
                if len(current_group) > 0 and current_group[-1].get("pre_img_path"):
                    candidate_img_paths_rel.append(current_group[-1]["pre_img_path"])
                    
                for idx in range(current_index, min(current_index + 3, len(temp_workflow_info))):
                    evt = temp_workflow_info[idx]
                    p = evt.get("pre_img_path")
                    if p and p not in candidate_img_paths_rel:
                        candidate_img_paths_rel.append(p)

                crop_box = None
                base_target_full = macros_root / candidate_img_paths_rel[0] if candidate_img_paths_rel else None

                if base_target_full and base_target_full.exists():
                    try:
                        with Image.open(base_target_full) as img_temp:
                            max_w, max_h = img_temp.width, img_temp.height
                            
                        full_img_paths = [macros_root / p for p in group_img_paths_rel if (macros_root / p).exists()]
                        
                        if len(full_img_paths) >= 2:
                            bbox = get_text_field_bbox_pil(full_img_paths, max_w, max_h)
                            if bbox:
                                refined_bbox, shape_info = refine_textfield_bbox_cv(str(base_target_full), bbox)
                                logger.info(f"[{workflow_id}] Text field refined via CV. Shape: {shape_info}, BBox: {refined_bbox}")
                                
                                l, t, r, b = refined_bbox
                                margin_x, margin_y = 5, 5
                                crop_box = (max(0, l - margin_x), max(0, t - margin_y), min(max_w, r + margin_x), min(max_h, b + margin_y))
                    except Exception as e:
                        logger.warning(f"[{workflow_id}] Error in text field extraction: {e}")

                if not crop_box and base_target_full and base_target_full.exists():
                    last_click = next((item for item in reversed(processed_info) if item["raw_action"] == "click"), None)
                    if last_click:
                        with Image.open(base_target_full) as img:
                            cx, cy = last_click["cursor_x"], last_click["cursor_y"]
                            left, top = max(0, cx - 400), max(0, cy - 25)
                            right, bottom = min(img.width, cx + 400), min(img.height, cy + 25)
                            crop_box = (left, top, right, bottom)

                best_overall_text = ""
                
                # 候補となる確定前・確定後画像をすべてスキャンし、最も精度が高い文字列を拾う
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
                                temp_crop_path = temp_dir / f"temp_ocr_crop_{current_group[-1]['event_id']}_{Path(cand_path_rel).stem}.png"
                                crop_img.save(temp_crop_path)
                                
                                ocr_results = read_text_from_image(str(temp_crop_path))
                                if ocr_results:
                                    valid_texts = [res.content for res in ocr_results if res.content]
                                    if valid_texts:
                                        best_text_for_frame = max(valid_texts, key=len)
                                        if len(best_text_for_frame) > len(best_overall_text):
                                            best_overall_text = best_text_for_frame
                        except Exception as e:
                            logger.error(f"[{workflow_id}] Failed to extract text via cropped OCR for candidate: {e}")

                if best_overall_text and (len(best_overall_text) >= 2 or len(base_text) <= 2):
                    text = best_overall_text
                    logger.info(f"[{workflow_id}] Final OCR extracted text from best candidate BBox: {text}")
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
            if info["raw_action"] == "key_down":
                role_lower = str(info["semantic_role"]).lower() if info["semantic_role"] else ""
                
                is_special = False
                is_text_modifier = False
                
                if role_lower.startswith("key."):
                    is_special = True
                elif role_lower in ["space", "tab", "backspace", "delete"]:
                    # 入力文字を変化・増減させるキーは文字入力の一環とみなす
                    is_special = True
                    is_text_modifier = True
                elif role_lower in ["enter", "esc", "shift", "ctrl", "alt", "cmd", "win", "windows", "up", "down", "left", "right"]:
                    # 確定や移動を行うキー
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

            # --- 最適化: 連続するスクロール操作および短い待機を結合する ---
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
                            # 1秒未満の待機であれば一連のスクロール操作とみなして結合
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