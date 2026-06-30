# src/core/healer/recovery_manager.py
# @role: 実行中の異常（UI変更や座標ズレ）を検知し、システムを止めずに自律修復を試みる。
# 
# 【参照元】
#   - core/executor/runner.py (ポーリング待機がタイムアウトまたは画像差分エラー発生時)
# 
# 【参照先】
#   - engines/yolo/detector.py (座標上書き用UI検出)
#   - engines/ocr/reader.py (座標上書き用テキスト検出)
#   - core/generator/log_integrator.py (動的再生成用)

import json
import logging
from pathlib import Path
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor

from core.recorder.screen_capturer import take_screenshot, get_macros_root
from engines.yolo.detector import detect_ui_elements
from engines.ocr.reader import read_text_from_image

logger = logging.getLogger(__name__)

def attempt_recovery(workflow_id: str, current_step_index: int) -> Dict[str, Any]:
    """
    エラー発生時に自己修復ツリーを実行し、修復結果（成功/失敗と補正された座標等のアクション）を返す。
    """
    logger.info(f"[{workflow_id}] Initiating self-recovery for step {current_step_index}.")

    macros_root = get_macros_root()
    target_dir = macros_root / workflow_id
    workflow_path = target_dir / "workflow.json"
    
    if not workflow_path.exists():
        logger.error(f"[{workflow_id}] Recovery failed: workflow.json not found.")
        return {"success": False, "reason": "workflow_not_found"}

    try:
        with open(workflow_path, 'r', encoding='utf-8') as f:
            workflow_data = json.load(f)
    except Exception as e:
        logger.error(f"[{workflow_id}] Recovery failed: Could not parse workflow.json. Error: {e}")
        return {"success": False, "reason": "workflow_parse_error"}

    target_step = next((step for step in workflow_data.get("steps", []) if step.get("step_id") == current_step_index), None)
    
    if not target_step:
        logger.warning(f"[{workflow_id}] Recovery aborted: Step {current_step_index} not found.")
        return {"success": False, "reason": "step_not_found"}

    action_params = target_step.get("action", {}).get("parameters", {})
    target_selector = action_params.get("target", {})
    semantic_role = target_selector.get("semantic_role") if isinstance(target_selector, dict) else None

    # UI要素に依存しないアクション（待機やキーボードショートカットなど）の場合は修復対象外
    if not semantic_role:
        logger.warning(f"[{workflow_id}] Recovery aborted: No semantic_role found for step {current_step_index}.")
        return {"success": False, "reason": "no_semantic_role_target"}

    logger.info(f"[{workflow_id}] Stage 2: Scanning current screen for element '{semantic_role}'...")
    
    full_img, monitor_info = take_screenshot()
    
    temp_dir = target_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_img_path = temp_dir / f"recovery_s2_step{current_step_index}.png"
    
    try:
        full_img.save(temp_img_path)
    except Exception as e:
        logger.error(f"[{workflow_id}] Recovery failed: Could not save temporary screenshot. Error: {e}")
        return {"success": False, "reason": "screenshot_save_error"}

    try:
        # OCRとYOLOを並列実行してボトルネックを最小化
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_ocr = executor.submit(read_text_from_image, str(temp_img_path))
            future_yolo = executor.submit(detect_ui_elements, str(temp_img_path))
            
            ocr_results = future_ocr.result()
            yolo_results = future_yolo.result()

        found_box = None
        best_confidence = 0.0
        target_text_lower = str(semantic_role).lower()

        # 1. OCR結果からテキストの一致を探索 (完全一致または部分一致を優先)
        if ocr_results:
            for ocr_res in ocr_results:
                if not ocr_res.content:
                    continue
                res_text_lower = ocr_res.content.lower()
                
                if target_text_lower in res_text_lower or res_text_lower in target_text_lower:
                    if ocr_res.confidence > best_confidence:
                        best_confidence = ocr_res.confidence
                        found_box = ocr_res.boundingBox

        # 2. YOLO結果から該当する役割（button, iconなど）を探す（テキストが見つからなかった場合のフォールバック）
        if not found_box and yolo_results:
            for yolo_res in yolo_results:
                if yolo_res.type.lower() == target_text_lower:
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
            
            # 発見したBoundingBoxの中心座標をスクリーン絶対座標として計算
            center_x = monitor_left + b_x + (b_w // 2)
            center_y = monitor_top + b_y + (b_h // 2)
            
            logger.info(f"[{workflow_id}] Stage 2 Success: '{semantic_role}' found at ({center_x}, {center_y}) [Confidence: {best_confidence:.2f}].")
            
            return {
                "success": True,
                "stage": 2,
                "action": "update_coordinates",
                "new_coordinates": {"x": int(center_x), "y": int(center_y)}
            }

        logger.warning(f"[{workflow_id}] Stage 2 Failed: '{semantic_role}' not found. Escalating to Stage 3...")
        
        # TODO: Stage 3 (AI動的再生成) への移行ロジックをここに実装予定
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