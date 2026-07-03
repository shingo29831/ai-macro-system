# src/core/healer/recovery_manager.py
# @role: 実行中の異常（UI変更や座標ズレ）を検知し、システムを止めずに自律修復を試みる。
# 
# 【参照元】
#   - core/executor/runner.py (ポーリング待機がタイムアウトまたは画像差分エラー発生時)
# 
# 【参照先】
#   - engines/yolo/detector.py (座標上書き用UI検出)
#   - engines/ocr/reader.py (座標上書き用テキスト検出)

import json
import logging
from pathlib import Path
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor

from core.recorder.screen_capturer import take_screenshot, get_macros_root
from engines.yolo.detector import detect_ui_elements
from engines.ocr.reader import read_text_from_image

logger = logging.getLogger(__name__)

def attempt_recovery(workflow_id: str, target_id: str) -> Dict[str, Any]:
    """
    エラー発生時に ui_targets.json の変数を参照して画面をスキャンし、修復された座標を返す。
    """
    logger.info(f"[{workflow_id}] Initiating self-recovery using variables for target: {target_id}")

    macros_root = get_macros_root()
    target_dir = macros_root / workflow_id
    ui_targets_path = target_dir / "ui_targets.json"
    
    if not ui_targets_path.exists():
        logger.error(f"[{workflow_id}] Recovery failed: ui_targets.json not found.")
        return {"success": False, "reason": "ui_targets_not_found"}

    try:
        with open(ui_targets_path, 'r', encoding='utf-8') as f:
            ui_targets = json.load(f)
    except Exception as e:
        logger.error(f"[{workflow_id}] Recovery failed: Could not parse ui_targets.json. Error: {e}")
        return {"success": False, "reason": "targets_parse_error"}

    target_info = ui_targets.get(target_id)
    if not target_info:
        logger.warning(f"[{workflow_id}] Recovery aborted: Target ID {target_id} not found in variables.")
        return {"success": False, "reason": "target_id_missing"}

    semantic_role = target_info.get("semantic_role")
    ui_type = target_info.get("ui_type")

    if not semantic_role:
        logger.warning(f"[{workflow_id}] Recovery aborted: No semantic_role text found for {target_id}.")
        return {"success": False, "reason": "no_semantic_role"}

    logger.info(f"[{workflow_id}] Stage 2: Scanning current screen for variable '{semantic_role}' (Type: {ui_type})...")
    
    full_img, monitor_info = take_screenshot()
    
    temp_dir = target_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_img_path = temp_dir / f"recovery_s2_{target_id}.png"
    
    try:
        full_img.save(temp_img_path)
    except Exception as e:
        logger.error(f"[{workflow_id}] Recovery failed: Could not save temporary screenshot. Error: {e}")
        return {"success": False, "reason": "screenshot_save_error"}

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_ocr = executor.submit(read_text_from_image, str(temp_img_path))
            future_yolo = executor.submit(detect_ui_elements, str(temp_img_path))
            
            ocr_results = future_ocr.result()
            yolo_results = future_yolo.result()

        found_box = None
        best_confidence = 0.0
        target_text_lower = str(semantic_role).lower()

        # 1. OCR結果からテキストの一致を探索
        if ocr_results:
            for ocr_res in ocr_results:
                if not ocr_res.content:
                    continue
                res_text_lower = ocr_res.content.lower()
                
                if target_text_lower in res_text_lower or res_text_lower in target_text_lower:
                    if ocr_res.confidence > best_confidence:
                        best_confidence = ocr_res.confidence
                        found_box = ocr_res.boundingBox

        # 2. YOLO結果から該当する役割（buttonなど）を探す
        if not found_box and yolo_results:
            for yolo_res in yolo_results:
                if yolo_res.type.lower() == ui_type.lower() or yolo_res.type.lower() == target_text_lower:
                    if yolo_res.confidence > best_confidence:
                        best_confidence = yolo_res.confidence
                        found_box = yolo_res.boundingBox

        if found_box:
            b_x = getattr(found_box, 'x', 0)
            b_y = getattr(found_box, 'y', 0)
            b_w = getattr(found_box, 'width', 0)
            b_h = getattr(found_box, 'height', 0)

            monitor_left = monitor_info.get("left", 0) if isinstance(monitor_info, dict) else 0
            monitor_top = monitor_info.get("top", 0) if isinstance(monitor_info, dict) else 0
            
            center_x = monitor_left + b_x + (b_w // 2)
            center_y = monitor_top + b_y + (b_h // 2)
            
            logger.info(f"[{workflow_id}] Stage 2 Success: Variable '{semantic_role}' found at ({center_x}, {center_y}) [Confidence: {best_confidence:.2f}].")
            
            return {
                "success": True,
                "stage": 2,
                "action": "update_coordinates",
                "new_coordinates": {"x": int(center_x), "y": int(center_y)}
            }

        logger.warning(f"[{workflow_id}] Stage 2 Failed: Variable '{semantic_role}' not found.")
        return {"success": False, "reason": "element_not_found"}

    except Exception as e:
        logger.error(f"[{workflow_id}] Recovery process encountered a critical error: {e}")
        return {"success": False, "reason": "scan_error"}
    finally:
        if temp_img_path.exists():
            try:
                temp_img_path.unlink()
            except Exception:
                pass