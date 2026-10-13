"""Module: @role: マクロ実行時のUI要素テンプレートマッチング検証および自律自己修復(recovery_manager)を担当する。"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def verify_and_heal_target(
    workflow_id: str,
    target_id: str,
    raw_event_id: str,
    args: Dict[str, Any],
    target_dir: Path,
    last_win_args: Optional[Dict[str, Any]],
    status_callback: Optional[Callable[[str, bool], None]],
    check_stop_callback: Callable[[], None],
    window_offset: Tuple[int, int] = (0, 0),
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    needs_recovery = False
    crop_image_path = target_dir / "images" / f"{raw_event_id}_crop.png"
    recovery_info = None

    try:
        from core.recorder.screen_capturer import take_screenshot
        from core.healer.recovery_manager import attempt_recovery

        current_img_pil, monitor_info = take_screenshot()
        check_stop_callback()

        m_left = int(monitor_info.get("left", 0)) if isinstance(monitor_info, dict) else 0
        m_top = int(monitor_info.get("top", 0)) if isinstance(monitor_info, dict) else 0

        if crop_image_path.exists():
            current_img_cv = cv2.cvtColor(np.array(current_img_pil), cv2.COLOR_RGB2BGR)
            template_cv = cv2.imread(str(crop_image_path), cv2.IMREAD_COLOR)

            if template_cv is not None:
                res = cv2.matchTemplate(current_img_cv, template_cv, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

                if max_val < 0.8:
                    logger.warning(f"[{workflow_id}] Template match failed (Confidence: {max_val:.2f}). Initiating Healer...")
                    needs_recovery = True
                else:
                    # Why: モニター原点を加算した絶対画面座標で判定しマルチモニター誤爆を根絶
                    abs_match_x = m_left + max_loc[0] + template_cv.shape[1] // 2
                    abs_match_y = m_top + max_loc[1] + template_cv.shape[0] // 2
                    expected_x = args.get("x", 0) + window_offset[0]
                    expected_y = args.get("y", 0) + window_offset[1]
                    dist = ((abs_match_x - expected_x) ** 2 + (abs_match_y - expected_y) ** 2) ** 0.5

                    if dist > 25:
                        logger.warning(f"[{workflow_id}] Target UI drifted by {dist:.1f} pixels. Initiating Healer...")
                        needs_recovery = True
            else:
                needs_recovery = True
        else:
            logger.warning(f"[{workflow_id}] No crop image available. Initiating Healer...")
            needs_recovery = True

        if needs_recovery:
            check_stop_callback()
            if status_callback:
                status_callback("自己修復中...", True)

            target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else 0) or 0
            recovery_result = attempt_recovery(workflow_id, target_id, window_hwnd=target_hwnd, raw_event_id=raw_event_id)
            check_stop_callback()

            recovery_info = recovery_result
            if recovery_result.get("success"):
                new_coords = recovery_result.get("new_coordinates")
                if new_coords and "x" in new_coords and "y" in new_coords:
                    # Why: 実行時オフセットを逆算して格納し後続の二重加算を完全防止
                    args["x"] = int(new_coords["x"]) - window_offset[0]
                    args["y"] = int(new_coords["y"]) - window_offset[1]
                    logger.info(f"[{workflow_id}] Healer successfully updated base coordinates to ({args['x']}, {args['y']}).")
                    if status_callback:
                        status_callback("実行中...", False)
                    return True, recovery_info

            logger.error(f"[{workflow_id}] Healer failed to recover target '{target_id}'.")
            if status_callback:
                status_callback("実行中...", False)
            return False, recovery_info

        return True, None

    except Exception as e:
        logger.error(f"[{workflow_id}] Error during image validation/recovery: {e}")
        if status_callback:
            status_callback("実行中...", False)
        return False, {"error": str(e)}
