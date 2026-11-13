"""Module: @role: 画面マッチングの待機ループ制御を担当し、画像比較・動的マスク評価・スクロール照合モジュールを統合するファサード。"""

import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import cv2
import numpy as np

from core.executor.screen_comparison import (
    calculate_orb_match,
    calculate_ssim,
    crop_window_region,
    is_screen_match,
    notify_execution_status,
)
from core.executor.screen_match_evaluator import (
    DynamicVideoMaskTracker,
    build_static_evaluation_mask,
    create_text_debug_callback,
    evaluate_frame_match,
    is_screen_transitioning,
    save_match_images,
    save_timeout_masked_image,
)
from core.executor.scroll_aligner import align_scroll_by_screen_match
from core.recorder.screen_capturer import take_screenshot

logger = logging.getLogger(__name__)

__all__ = [
    "calculate_ssim",
    "calculate_orb_match",
    "is_screen_match",
    "align_scroll_by_screen_match",
    "wait_for_screen_match",
]


def _copy_target_image(pre_image_path: Path, exec_logs_dir: Path, step_index: int, raw_event_id: str) -> None:
    try:
        target_copy_path = exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_target.png"
        if not target_copy_path.exists():
            shutil.copy2(pre_image_path, target_copy_path)
    except Exception as e:
        logger.warning(f"Failed to copy target image: {e}")


def wait_for_screen_match(
    target_dir: Path,
    raw_event_id: str,
    win_x: int,
    win_y: int,
    win_w: int,
    win_h: int,
    workflow_id: str,
    status_callback: Optional[Callable[[str, bool], None]],
    step_index: int,
    timeout: float = 30.0,
    check_cancel_callback: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    result_info: Dict[str, Any] = {"matched": False, "time_taken": 0.0, "scores": {}}
    if not raw_event_id or win_w <= 0 or win_h <= 0:
        return result_info

    pre_image_path = target_dir / "images" / f"{raw_event_id}_pre.png"
    if not pre_image_path.exists():
        return result_info

    exec_logs_dir = target_dir / "execution_logs"
    exec_logs_dir.mkdir(parents=True, exist_ok=True)
    _copy_target_image(pre_image_path, exec_logs_dir, step_index, raw_event_id)

    try:
        pre_img_cv = cv2.imread(str(pre_image_path), cv2.IMREAD_GRAYSCALE)
        if pre_img_cv is None:
            return result_info

        _, monitor_info = take_screenshot()
        offset_x = monitor_info.get("left", 0) if isinstance(monitor_info, dict) else 0
        offset_y = monitor_info.get("top", 0) if isinstance(monitor_info, dict) else 0

        pre_rect = crop_window_region(
            pre_img_cv, win_x, win_y, win_w, win_h, offset_x, offset_y, margin=8, fallback_use_margin=True
        )
        if pre_rect is None:
            return result_info

        x1, y1, x2, y2 = pre_rect
        pre_crop = pre_img_cv[y1:y2, x1:x2]
        scale = min(1.0, 512.0 / max(pre_crop.shape[0], pre_crop.shape[1]))
        if scale < 1.0:
            pre_crop_eval = cv2.resize(pre_crop, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        else:
            pre_crop_eval = pre_crop.copy()

        video_tracker = DynamicVideoMaskTracker()
        text_debug_cb = create_text_debug_callback(exec_logs_dir, step_index, raw_event_id)
        waiting_logged = False
        start_time = time.time()
        last_frame_crop: Optional[np.ndarray] = None
        last_curr_crop_color: Optional[np.ndarray] = None
        last_video_mask: Optional[np.ndarray] = None
        last_text_mask: Optional[np.ndarray] = None

        while True:
            if check_cancel_callback and check_cancel_callback():
                break

            elapsed = time.time() - start_time
            if elapsed > timeout:
                logger.warning(f"[{workflow_id}] Screen match timeout ({timeout}s). Proceeding to next action.")
                if waiting_logged:
                    notify_execution_status(status_callback, "タイムアウトしました。マクロを再開します。", False)
                save_timeout_masked_image(
                    exec_logs_dir,
                    step_index,
                    raw_event_id,
                    workflow_id,
                    last_curr_crop_color,
                    last_video_mask,
                    last_text_mask,
                )
                break

            curr_img_pil, curr_monitor = take_screenshot()
            curr_img_color = cv2.cvtColor(np.array(curr_img_pil), cv2.COLOR_RGB2BGR)
            curr_img_cv = cv2.cvtColor(curr_img_color, cv2.COLOR_BGR2GRAY)

            c_offset_x = curr_monitor.get("left", 0) if isinstance(curr_monitor, dict) else 0
            c_offset_y = curr_monitor.get("top", 0) if isinstance(curr_monitor, dict) else 0

            curr_rect = crop_window_region(
                curr_img_cv, win_x, win_y, win_w, win_h, c_offset_x, c_offset_y, margin=8, fallback_use_margin=False
            )

            if curr_rect is not None:
                cx1, cy1, cx2, cy2 = curr_rect
                curr_crop = curr_img_cv[cy1:cy2, cx1:cx2]
                last_curr_crop_color = curr_img_color[cy1:cy2, cx1:cx2]
                if scale < 1.0 or curr_crop.shape != pre_crop_eval.shape:
                    curr_crop_eval = cv2.resize(
                        curr_crop, (pre_crop_eval.shape[1], pre_crop_eval.shape[0]), interpolation=cv2.INTER_AREA
                    )
                else:
                    curr_crop_eval = curr_crop.copy()

                static_mask, last_video_mask, last_text_mask = build_static_evaluation_mask(
                    pre_crop_eval=pre_crop_eval,
                    curr_crop_eval=curr_crop_eval,
                    video_tracker=video_tracker,
                    abs_cx1=c_offset_x + cx1,
                    abs_cy1=c_offset_y + cy1,
                    scale=scale,
                    on_text_region=text_debug_cb,
                )

                valid_area = int(np.count_nonzero(static_mask))
                transitioning = is_screen_transitioning(last_frame_crop, curr_crop_eval, static_mask, valid_area)
                last_frame_crop = curr_crop_eval.copy()

                matched, scores, flags = evaluate_frame_match(pre_crop_eval, curr_crop_eval, static_mask)
                result_info["scores"] = scores

                if matched:
                    save_match_images(exec_logs_dir, step_index, raw_event_id, curr_crop, pre_crop)
                    if waiting_logged:
                        if not flags["pixel"] and (flags["ssim"] or flags["orb"] or flags["struct"]):
                            notify_execution_status(
                                status_callback, "動的領域・テキストを除外して一致を確認しました。\nマクロを再開します。", False
                            )
                        else:
                            notify_execution_status(
                                status_callback, "画面の一致を確認しました。\nマクロを再開します。", False
                            )
                        logger.info(
                            f"[{workflow_id}] Screen matched (diff: {scores['diff_ratio']:.1%}, sim: {scores['sim']:.2f}, ssim: {scores['ssim']:.2f}, orb: {scores['orb']:.2f}). Resuming."
                        )
                        time.sleep(1.5)
                    result_info["matched"] = True
                    break

                if transitioning:
                    notify_execution_status(status_callback, "画面遷移を待機しています...", False)
                    waiting_logged = False
                elif elapsed > 3.0:
                    detail_msg = (
                        f"記録時と同じ画面にしてください。\n"
                        f"差分: {scores['diff_ratio']:.1%} / 構造: {scores['ssim']:.2f} / 特徴: {scores['orb']:.2f}"
                    )
                    notify_execution_status(status_callback, detail_msg, True)
                    if not waiting_logged:
                        logger.info(
                            f"[{workflow_id}] Waiting for screen to match... (diff: {scores['diff_ratio']:.1%}, sim: {scores['sim']:.2f}, ssim: {scores['ssim']:.2f}, orb: {scores['orb']:.2f})"
                        )
                        waiting_logged = True
                elif not waiting_logged:
                    notify_execution_status(status_callback, "画面の応答を待機しています...", False)
            else:
                if elapsed > 3.0:
                    notify_execution_status(
                        status_callback, "記録時と同じ画面にしてください。\n(ウィンドウサイズが異なります)", True
                    )
                    if not waiting_logged:
                        logger.info(f"[{workflow_id}] Waiting for screen to match... (size mismatch)")
                        waiting_logged = True
                elif not waiting_logged:
                    notify_execution_status(status_callback, "画面の応答を待機しています...", False)

            time.sleep(0.5)

        result_info["time_taken"] = time.time() - start_time
    except Exception as e:
        logger.warning(f"Error during screen match waiting: {e}")

    return result_info
