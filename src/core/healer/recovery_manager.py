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

def attempt_recovery(workflow_id: str, target_id: str, window_hwnd: int = 0, raw_event_id: str = "") -> Dict[str, Any]:
    """UIA、マルチスケール画像照合、AIビジョンの3段階フォールバックにより目標UI座標を自律修復する。"""
    logger.info(f"[{workflow_id}] Initiating multi-stage self-recovery for target: {target_id}")

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
        logger.error(f"[{workflow_id}] Recovery failed: Could not parse ui_targets.json: {e}")
        return {"success": False, "reason": "targets_parse_error"}

    target_info = ui_targets.get(target_id, {})
    semantic_role = str(target_info.get("semantic_role") or "").strip()
    ui_type = str(target_info.get("ui_type") or "").strip()

    # --- Stage 1: Windows UI Automation (最速・高精度) ---
    import platform
    if platform.system() == "Windows" and window_hwnd:
        try:
            import uiautomation as auto
            root_ctrl = auto.ControlFromHandle(window_hwnd)
            if root_ctrl and root_ctrl.Exists(0, 0):
                matched_ctrl = None
                if semantic_role:
                    matched_ctrl = root_ctrl.Control(searchDepth=8, Name=semantic_role)
                if not matched_ctrl or not matched_ctrl.Exists(0, 0):
                    if ui_type:
                        matched_ctrl = root_ctrl.Control(searchDepth=8, ControlTypeName=ui_type)

                if matched_ctrl and matched_ctrl.Exists(0, 0):
                    rect = matched_ctrl.BoundingRectangle
                    if rect and rect.width() > 0 and rect.height() > 0:
                        cx = rect.left + rect.width() // 2
                        cy = rect.top + rect.height() // 2
                        logger.info(f"[{workflow_id}] Stage 1 (UIA) recovered '{semantic_role}' at ({cx}, {cy}).")
                        return {
                            "success": True,
                            "stage": 1,
                            "method": "uia",
                            "new_coordinates": {"x": int(cx), "y": int(cy)}
                        }
        except Exception as uia_err:
            logger.debug(f"[{workflow_id}] UIA recovery bypassed: {uia_err}")

    # --- Stage 2: テンプレート画像のマルチスケール照合 (レスポンシブ・微小ズレ救済) ---
    import cv2
    import numpy as np
    crop_path = target_dir / "images" / f"{raw_event_id}_crop.png" if raw_event_id else None
    if not crop_path or not crop_path.exists():
        found_crops = list((target_dir / "images").glob(f"*{target_id}*_crop.png"))
        if found_crops:
            crop_path = found_crops[0]

    full_img, monitor_info = take_screenshot()
    if crop_path and crop_path.exists():
        try:
            tpl_img = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
            screen_bgr = cv2.cvtColor(np.array(full_img), cv2.COLOR_RGB2BGR)
            if tpl_img is not None and screen_bgr is not None:
                best_val, best_loc, best_scale = -1.0, None, 1.0
                # Why: DPI拡縮や微小なレンダリング変動(±15%)をマルチスケール照合で確実に吸収
                for scale in [1.0, 0.9, 1.1, 0.85, 1.15]:
                    if scale != 1.0:
                        tw, th = int(tpl_img.shape[1] * scale), int(tpl_img.shape[0] * scale)
                        if tw >= screen_bgr.shape[1] or th >= screen_bgr.shape[0] or tw < 10 or th < 10:
                            continue
                        resized_tpl = cv2.resize(tpl_img, (tw, th), interpolation=cv2.INTER_AREA)
                    else:
                        resized_tpl = tpl_img

                    res = cv2.matchTemplate(screen_bgr, resized_tpl, cv2.TM_CCOEFF_NORMED)
                    _, max_v, _, max_l = cv2.minMaxLoc(res)
                    if max_v > best_val:
                        best_val, best_loc, best_scale = max_v, max_l, scale

                if best_val >= 0.72 and best_loc:
                    tw = int(tpl_img.shape[1] * best_scale)
                    th = int(tpl_img.shape[0] * best_scale)
                    m_left = monitor_info.get("left", 0) if isinstance(monitor_info, dict) else 0
                    m_top = monitor_info.get("top", 0) if isinstance(monitor_info, dict) else 0
                    cx = m_left + best_loc[0] + tw // 2
                    cy = m_top + best_loc[1] + th // 2
                    logger.info(f"[{workflow_id}] Stage 2 (Template) recovered target at ({cx}, {cy}) (Score: {best_val:.2f}).")
                    return {
                        "success": True,
                        "stage": 2,
                        "method": "template_match",
                        "new_coordinates": {"x": int(cx), "y": int(cy)}
                    }
        except Exception as cv_err:
            logger.debug(f"[{workflow_id}] Template match recovery bypassed: {cv_err}")

    # --- Stage 3: OCR & YOLO ビジョンスキャン (大幅UI再配置・テキスト認識) ---
    temp_dir = target_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_img_path = temp_dir / f"recovery_s3_{target_id}.png"

    try:
        full_img.save(temp_img_path)
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_ocr = executor.submit(read_text_from_image, str(temp_img_path))
            future_yolo = executor.submit(detect_ui_elements, str(temp_img_path))
            ocr_results = future_ocr.result()
            yolo_results = future_yolo.result()

        found_box = None
        best_confidence = 0.0
        target_text_lower = semantic_role.lower()

        if ocr_results and target_text_lower:
            for ocr_res in ocr_results:
                if not ocr_res.content:
                    continue
                res_text = ocr_res.content.lower()
                if target_text_lower in res_text or res_text in target_text_lower:
                    if ocr_res.confidence > best_confidence:
                        best_confidence = ocr_res.confidence
                        found_box = ocr_res.boundingBox

        if not found_box and yolo_results and ui_type:
            for yolo_res in yolo_results:
                if yolo_res.type.lower() == ui_type.lower():
                    if yolo_res.confidence > best_confidence:
                        best_confidence = yolo_res.confidence
                        found_box = yolo_res.boundingBox

        if found_box:
            m_left = monitor_info.get("left", 0) if isinstance(monitor_info, dict) else 0
            m_top = monitor_info.get("top", 0) if isinstance(monitor_info, dict) else 0
            cx = m_left + getattr(found_box, 'x', 0) + (getattr(found_box, 'width', 0) // 2)
            cy = m_top + getattr(found_box, 'y', 0) + (getattr(found_box, 'height', 0) // 2)
            logger.info(f"[{workflow_id}] Stage 3 (AI Vision) recovered target at ({cx}, {cy}).")
            return {
                "success": True,
                "stage": 3,
                "method": "ai_vision",
                "new_coordinates": {"x": int(cx), "y": int(cy)}
            }
    except Exception as e:
        logger.error(f"[{workflow_id}] Critical error during AI vision recovery: {e}")
    finally:
        if temp_img_path.exists():
            try:
                temp_img_path.unlink()
            except Exception:
                pass

    logger.warning(f"[{workflow_id}] All recovery stages exhausted for '{target_id}'.")
    return {"success": False, "reason": "element_not_found"}