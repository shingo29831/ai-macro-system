"""Module: @role: 実行中の異常（UI変更や座標ズレ）を検知し、UIA・画像照合・OCRの3段階で自律修復を統括するファサード。"""

import json
import logging
from typing import Any, Dict, List, Optional

from core.healer.uia_recovery import (
    get_effective_hwnd,
    get_window_crop_box,
    try_browser_uia_recovery,
    try_windows_uia_recovery,
)
from core.healer.vision_recovery import (
    is_logo_symbol_noise as _is_logo_symbol_noise,
    match_target_text as _match_target_text,
    resolve_crop_path,
    try_ocr_recovery,
    try_template_recovery,
)
from core.shared.macro_paths import get_macros_root
from core.shared.screen_capture import take_screenshot
from engines.ocr.reader import read_text_from_image

logger = logging.getLogger(__name__)

_INVALID_RECOVERY_LABELS = {
    "left_click",
    "right_click",
    "middle_click",
    "double_click",
    "move",
    "click",
    "unknown",
    "ndlocr未設定",
    "切り抜き失敗",
}

def _collect_candidate_labels(target_info: Dict[str, Any], target_context: Optional[Dict[str, Any]]) -> List[str]:
    """コマンド引数とui_targetsからノイズを除外した修復用テキストラベル候補を収集する。"""
    candidates: List[str] = []
    raw_values = []
    if target_context:
        raw_values.extend([target_context.get("element_name"), target_context.get("text")])
    raw_values.append(target_info.get("semantic_role"))

    for val in raw_values:
        s = str(val or "").strip()
        if not s or s.startswith("{{") or s.startswith("${") or s.startswith("http"):
            continue
        if s.lower() in _INVALID_RECOVERY_LABELS:
            continue
        if s not in candidates:
            candidates.append(s)
    return candidates


def attempt_recovery(
    workflow_id: str,
    target_id: str,
    window_hwnd: int = 0,
    raw_event_id: str = "",
    target_context: Optional[Dict[str, Any]] = None,
    last_win_args: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """UIA、マルチスケール画像照合、AIビジョンの3段階フォールバックにより目標UI座標を自律修復する。"""
    logger.info(f"[{workflow_id}] Initiating multi-stage self-recovery for target: {target_id}")

    macros_root = get_macros_root()
    target_dir = macros_root / workflow_id
    ui_targets_path = target_dir / "ui_targets.json"

    ui_targets: Dict[str, Any] = {}
    if ui_targets_path.exists():
        try:
            with open(ui_targets_path, "r", encoding="utf-8") as f:
                ui_targets = json.load(f)
        except Exception as e:
            logger.warning(f"[{workflow_id}] Could not parse ui_targets.json: {e}")

    target_info = ui_targets.get(target_id, {})
    candidate_labels = _collect_candidate_labels(target_info, target_context)
    effective_hwnd = get_effective_hwnd(window_hwnd)

    # --- Stage 1: Browser/Windows UI Automation (最速・高精度) ---
    browser_uia_res = try_browser_uia_recovery(workflow_id, target_context, last_win_args, candidate_labels)
    if browser_uia_res:
        return browser_uia_res

    win_uia_res = try_windows_uia_recovery(workflow_id, effective_hwnd, candidate_labels)
    if win_uia_res:
        return win_uia_res

    crop_path = resolve_crop_path(target_dir, raw_event_id, target_id)
    has_crop = bool(crop_path and crop_path.exists())
    if not has_crop and not candidate_labels:
        logger.warning(f"[{workflow_id}] All recovery stages exhausted for '{target_id}'.")
        return {"success": False, "reason": "element_not_found"}

    # --- Stage 2 & 3 共通の現在画面キャプチャ ---
    full_img, monitor_info = take_screenshot()
    win_crop_box = get_window_crop_box(effective_hwnd, monitor_info, full_img.width, full_img.height)

    # --- Stage 2: テンプレート画像のマルチスケール照合 (レスポンシブ・微小ズレ救済) ---
    if has_crop:
        template_res = try_template_recovery(
            workflow_id=workflow_id,
            target_dir=target_dir,
            raw_event_id=raw_event_id,
            target_id=target_id,
            full_img=full_img,
            monitor_info=monitor_info,
            win_crop_box=win_crop_box,
            crop_path=crop_path,
        )
        if template_res:
            return template_res

    # --- Stage 3: クリーンアップ済みOCRビジョンスキャン (YOLO誤爆排除) ---
    ocr_res = try_ocr_recovery(
        workflow_id=workflow_id,
        target_dir=target_dir,
        target_id=target_id,
        candidate_labels=candidate_labels,
        full_img=full_img,
        monitor_info=monitor_info,
        win_crop_box=win_crop_box,
        read_text_fn=read_text_from_image,
    )
    if ocr_res:
        return ocr_res

    logger.warning(f"[{workflow_id}] All recovery stages exhausted for '{target_id}'.")
    return {"success": False, "reason": "element_not_found"}


__all__ = ["attempt_recovery", "_is_logo_symbol_noise", "_match_target_text"]
