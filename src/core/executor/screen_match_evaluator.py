"""Module: @role: 画面待機マッチングにおける動的マスク生成（動画・テキスト・システムウィンドウ）、類似度評価、およびデバッグ画像出力を担当する。"""

import logging
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from core.executor.screen_comparison import (
    calculate_orb_match,
    calculate_ssim,
    detect_text_change_mask,
    get_masked_system_rects,
)

logger = logging.getLogger(__name__)


class DynamicVideoMaskTracker:
    """直近フレームバッファの輝度分散と静的エッジ保護から動画領域マスクを生成する。"""

    def __init__(self, max_frames: int = 5) -> None:
        self._max_frames = max_frames
        self._frame_buffer: List[np.ndarray] = []

    def update_and_build_mask(self, curr_crop_eval: np.ndarray) -> np.ndarray:
        self._frame_buffer.append(curr_crop_eval)
        if len(self._frame_buffer) > self._max_frames:
            self._frame_buffer.pop(0)

        video_mask = np.zeros_like(curr_crop_eval, dtype=np.uint8)
        if len(self._frame_buffer) < 3:
            return video_mask

        std_dev = np.std(self._frame_buffer, axis=0)
        video_mask = (std_dev > 12).astype(np.uint8) * 255

        edges_list = [cv2.Canny(f, 50, 150) for f in self._frame_buffer]
        static_edges = edges_list[0]
        for edge in edges_list[1:]:
            static_edges = cv2.bitwise_and(static_edges, edge)

        kernel_protect = np.ones((3, 3), np.uint8)
        static_edges_dilated = cv2.dilate(static_edges, kernel_protect, iterations=1)
        video_mask[static_edges_dilated == 255] = 0

        # Why: 動画が画面の30%以上を占めても静的領域を残し動的マスクを維持
        mask_ratio = np.count_nonzero(video_mask) / video_mask.size
        if mask_ratio > 0.85:
            video_mask = (std_dev > 25).astype(np.uint8) * 255
            video_mask[static_edges_dilated == 255] = 0

        return video_mask


def create_text_debug_callback(
    exec_logs_dir: Path,
    step_index: int,
    raw_event_id: str,
) -> Callable[[np.ndarray, np.ndarray, np.ndarray], None]:
    def _save_debug(region_curr: np.ndarray, region_pre: np.ndarray, region_diff: np.ndarray) -> None:
        try:
            debug_dir = exec_logs_dir / "text_detection_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            timestamp = int(time.time() * 1000)
            cv2.imwrite(str(debug_dir / f"step_{step_index:03d}_{raw_event_id}_{timestamp}_curr.png"), region_curr)
            cv2.imwrite(str(debug_dir / f"step_{step_index:03d}_{raw_event_id}_{timestamp}_pre.png"), region_pre)
            cv2.imwrite(str(debug_dir / f"step_{step_index:03d}_{raw_event_id}_{timestamp}_diff.png"), region_diff)
        except Exception as e:
            logger.warning(f"Failed to save text detection debug image: {e}")

    return _save_debug


def build_static_evaluation_mask(
    pre_crop_eval: np.ndarray,
    curr_crop_eval: np.ndarray,
    video_tracker: DynamicVideoMaskTracker,
    abs_cx1: int,
    abs_cy1: int,
    scale: float,
    on_text_region: Optional[Callable[[np.ndarray, np.ndarray, np.ndarray], None]] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    video_mask = video_tracker.update_and_build_mask(curr_crop_eval)
    text_mask = detect_text_change_mask(
        pre_crop_eval,
        curr_crop_eval,
        fill_rect=False,
        on_region_detected=on_text_region,
    )
    dynamic_mask = cv2.bitwise_or(video_mask, text_mask)

    for (ml, mt, mr, mb) in get_masked_system_rects(
        abs_cx1, abs_cy1, scale, curr_crop_eval.shape[1], curr_crop_eval.shape[0]
    ):
        dynamic_mask[mt:mb, ml:mr] = 255

    static_mask = cv2.bitwise_not(dynamic_mask)
    return static_mask, video_mask, text_mask


def is_screen_transitioning(
    last_frame_crop: Optional[np.ndarray],
    curr_crop_eval: np.ndarray,
    static_mask: np.ndarray,
    valid_area: int,
) -> bool:
    if last_frame_crop is None or valid_area <= 500:
        return False
    diff_with_last = cv2.absdiff(last_frame_crop, curr_crop_eval)
    _, thresh_last = cv2.threshold(diff_with_last, 30, 255, cv2.THRESH_BINARY)
    thresh_last = cv2.bitwise_and(thresh_last, thresh_last, mask=static_mask)
    change_ratio = np.count_nonzero(thresh_last) / valid_area
    return change_ratio > 0.05


def evaluate_frame_match(
    pre_crop_eval: np.ndarray,
    curr_crop_eval: np.ndarray,
    static_mask: np.ndarray,
) -> Tuple[bool, Dict[str, float], Dict[str, bool]]:
    valid_area = int(np.count_nonzero(static_mask))
    pre_crop_static = cv2.bitwise_and(pre_crop_eval, pre_crop_eval, mask=static_mask)
    curr_crop_static = cv2.bitwise_and(curr_crop_eval, curr_crop_eval, mask=static_mask)

    diff = cv2.absdiff(pre_crop_static, curr_crop_static)
    _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)

    if valid_area > 500:
        diff_ratio = np.count_nonzero(thresh) / valid_area
    else:
        diff_full = cv2.absdiff(pre_crop_eval, curr_crop_eval)
        _, thresh_full = cv2.threshold(diff_full, 30, 255, cv2.THRESH_BINARY)
        diff_ratio = np.count_nonzero(thresh_full) / (curr_crop_eval.shape[0] * curr_crop_eval.shape[1])

    is_pixel_match = diff_ratio <= 0.05

    res = cv2.matchTemplate(curr_crop_static, pre_crop_static, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)
    is_struct_match = (max_val >= 0.88) and (diff_ratio <= 0.10)

    pre_edges = cv2.Canny(pre_crop_static, 50, 150)
    curr_edges = cv2.Canny(curr_crop_static, 50, 150)
    is_edge_match = False
    max_val_edges = 0.0
    if np.count_nonzero(pre_edges) > 50:
        res_edges = cv2.matchTemplate(curr_edges, pre_edges, cv2.TM_CCOEFF_NORMED)
        _, max_val_edges, _, _ = cv2.minMaxLoc(res_edges)
        if max_val_edges >= 0.75 and diff_ratio <= 0.15:
            is_edge_match = True

    ssim_val = calculate_ssim(pre_crop_eval, curr_crop_eval, mask=static_mask)
    orb_score = calculate_orb_match(pre_crop_eval, curr_crop_eval, mask=static_mask)

    is_ssim_match = ssim_val >= 0.75
    is_orb_match = orb_score >= 0.15 or (orb_score >= 0.08 and max_val >= 0.6)

    scores = {
        "diff_ratio": float(diff_ratio),
        "sim": float(max_val),
        "edge_sim": float(max_val_edges),
        "ssim": ssim_val,
        "orb": orb_score,
    }
    flags = {
        "pixel": is_pixel_match,
        "struct": is_struct_match,
        "edge": is_edge_match,
        "ssim": is_ssim_match,
        "orb": is_orb_match,
    }
    matched = is_pixel_match or is_struct_match or is_edge_match or is_ssim_match or is_orb_match
    return matched, scores, flags


def save_match_images(
    exec_logs_dir: Path,
    step_index: int,
    raw_event_id: str,
    curr_crop: np.ndarray,
    pre_crop: np.ndarray,
) -> None:
    try:
        cv2.imwrite(str(exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_match_curr.png"), curr_crop)
        cv2.imwrite(str(exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_match_pre.png"), pre_crop)
    except Exception:
        pass


def save_timeout_masked_image(
    exec_logs_dir: Path,
    step_index: int,
    raw_event_id: str,
    workflow_id: str,
    curr_crop_color: Optional[np.ndarray],
    video_mask: Optional[np.ndarray],
    text_mask: Optional[np.ndarray],
) -> None:
    if curr_crop_color is None or video_mask is None or text_mask is None:
        return
    try:
        if video_mask.shape[:2] != curr_crop_color.shape[:2]:
            video_mask_resized = cv2.resize(
                video_mask, (curr_crop_color.shape[1], curr_crop_color.shape[0]), interpolation=cv2.INTER_NEAREST
            )
            text_mask_resized = cv2.resize(
                text_mask, (curr_crop_color.shape[1], curr_crop_color.shape[0]), interpolation=cv2.INTER_NEAREST
            )
        else:
            video_mask_resized = video_mask
            text_mask_resized = text_mask

        overlay = curr_crop_color.copy()
        overlay[video_mask_resized == 255] = [0, 0, 255]
        overlay[text_mask_resized == 255] = [0, 255, 0]
        highlighted_img = cv2.addWeighted(overlay, 0.5, curr_crop_color, 0.5, 0)

        cv2.imwrite(str(exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_timeout_masked.png"), highlighted_img)
        logger.info(f"[{workflow_id}] Saved timeout masked image (Video: Red, Text: Green).")
    except Exception as e:
        logger.warning(f"Failed to save timeout masked image: {e}")
