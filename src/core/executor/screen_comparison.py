"""Module: @role: 画像類似度評価(SSIM/ORB)、動的テキストマスク抽出および画面一致判定を担当する。"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import cv2
import numpy as np

from core.executor.window_manager import get_system_window_rects

logger = logging.getLogger(__name__)


def notify_execution_status(
    status_callback: Optional[Callable[[str, bool], None]],
    text: str,
    is_warning: bool = False,
) -> None:
    if status_callback:
        status_callback(text, is_warning)
        return
    try:
        from ui.views.running_dialog import RunningDialog
        RunningDialog.set_status(text, is_warning)
    except Exception:
        pass


def crop_window_region(
    img_cv: np.ndarray,
    win_x: int,
    win_y: int,
    win_w: int,
    win_h: int,
    offset_x: int,
    offset_y: int,
    margin: int = 8,
    fallback_use_margin: bool = True,
) -> Optional[Tuple[int, int, int, int]]:
    img_h, img_w = img_cv.shape[:2]
    x1 = max(0, win_x - offset_x + margin)
    y1 = max(0, win_y - offset_y + margin)
    x2 = min(img_w, win_x - offset_x + win_w - margin)
    y2 = min(img_h, win_y - offset_y + win_h - margin)

    if x2 <= x1 or y2 <= y1:
        fb_margin = margin if fallback_use_margin else 0
        x1 = max(0, win_x + fb_margin)
        y1 = max(0, win_y + fb_margin)
        x2 = min(img_w, win_x + win_w - fb_margin)
        y2 = min(img_h, win_y + win_h - fb_margin)
        if x2 <= x1 or y2 <= y1:
            return None

    return x1, y1, x2, y2


def get_masked_system_rects(
    abs_cx1: int,
    abs_cy1: int,
    scale: float,
    width: int,
    height: int,
) -> list[Tuple[int, int, int, int]]:
    masked_rects: list[Tuple[int, int, int, int]] = []
    for (sl, st, sr, sb) in get_system_window_rects():
        ml = max(0, int((sl - abs_cx1) * scale))
        mt = max(0, int((st - abs_cy1) * scale))
        mr = min(width, int((sr - abs_cx1) * scale))
        mb = min(height, int((sb - abs_cy1) * scale))
        if mr > ml and mb > mt:
            masked_rects.append((ml, mt, mr, mb))
    return masked_rects


def detect_text_change_mask(
    pre_crop_eval: np.ndarray,
    curr_crop_eval: np.ndarray,
    fill_rect: bool = False,
    on_region_detected: Optional[Callable[[np.ndarray, np.ndarray, np.ndarray], None]] = None,
) -> np.ndarray:
    diff_for_mask = cv2.absdiff(pre_crop_eval, curr_crop_eval)
    _, diff_thresh = cv2.threshold(diff_for_mask, 30, 255, cv2.THRESH_BINARY)

    kernel_text = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    diff_dilated = cv2.dilate(diff_thresh, kernel_text, iterations=1)

    text_mask = np.zeros_like(curr_crop_eval, dtype=np.uint8)
    contours, _ = cv2.findContours(diff_dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if 2 <= w < curr_crop_eval.shape[1] * 0.8 and 5 <= h < curr_crop_eval.shape[0] * 0.5:
            region_curr = curr_crop_eval[y:y + h, x:x + w]
            region_pre = pre_crop_eval[y:y + h, x:x + w]
            region_diff = diff_thresh[y:y + h, x:x + w]

            edges_curr = cv2.Canny(region_curr, 50, 150)
            edges_pre = cv2.Canny(region_pre, 50, 150)

            valid_edges_curr = cv2.bitwise_and(edges_curr, edges_curr, mask=region_diff)
            valid_edges_pre = cv2.bitwise_and(edges_pre, edges_pre, mask=region_diff)

            area = w * h
            edge_count = max(np.count_nonzero(valid_edges_curr), np.count_nonzero(valid_edges_pre))
            edge_density = edge_count / area if area > 0 else 0
            if edge_density > 0.05:
                if fill_rect:
                    cv2.rectangle(text_mask, (x, y), (x + w, y + h), 255, -1)
                else:
                    text_mask[y:y + h, x:x + w] = cv2.bitwise_or(text_mask[y:y + h, x:x + w], region_diff)
                if on_region_detected is not None:
                    on_region_detected(region_curr, region_pre, region_diff)

    return text_mask


def calculate_ssim(img1: np.ndarray, img2: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    c1 = 6.5025
    c2 = 58.5225
    i1 = img1.astype(np.float32)
    i2 = img2.astype(np.float32)
    mu1 = cv2.GaussianBlur(i1, (11, 11), 1.5)
    mu2 = cv2.GaussianBlur(i2, (11, 11), 1.5)
    mu1_sq = mu1 ** 2
    mu2_sq = mu2 ** 2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = cv2.GaussianBlur(i1 ** 2, (11, 11), 1.5) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(i2 ** 2, (11, 11), 1.5) - mu2_sq
    sigma12 = cv2.GaussianBlur(i1 * i2, (11, 11), 1.5) - mu1_mu2
    ssim_map = ((2 * mu1_mu2 + c1) * (2 * sigma12 + c2)) / (
        (mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2)
    )

    if mask is not None:
        valid_pixels = mask > 0
        if not np.any(valid_pixels):
            return 0.0
        return float(ssim_map[valid_pixels].mean())
    return float(ssim_map.mean())


def calculate_orb_match(img1: np.ndarray, img2: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    orb = cv2.ORB_create(nfeatures=500)
    kp1, des1 = orb.detectAndCompute(img1, mask)
    kp2, des2 = orb.detectAndCompute(img2, mask)
    if des1 is None or des2 is None or len(kp1) == 0 or len(kp2) == 0:
        return 0.0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(des1, des2)
    good_matches = [m for m in matches if m.distance < 50]
    return float(len(good_matches) / max(len(kp1), 1))


def is_screen_match(
    pre_image_path: Path,
    curr_img_cv: np.ndarray,
    win_x: int,
    win_y: int,
    win_w: int,
    win_h: int,
    offset_x: int,
    offset_y: int,
    global_dynamic_mask: Optional[np.ndarray] = None,
) -> Tuple[bool, Dict[str, float]]:
    scores = {"diff_ratio": 1.0, "sim": 0.0, "edge_sim": 1.0, "ssim": 0.0, "orb": 0.0}

    if not pre_image_path.exists():
        return False, scores

    pre_img_cv = cv2.imread(str(pre_image_path), cv2.IMREAD_GRAYSCALE)
    if pre_img_cv is None:
        return False, scores

    pre_rect = crop_window_region(pre_img_cv, win_x, win_y, win_w, win_h, offset_x, offset_y)
    if pre_rect is None:
        return False, scores
    x1, y1, x2, y2 = pre_rect
    pre_crop = pre_img_cv[y1:y2, x1:x2]

    curr_rect = crop_window_region(curr_img_cv, win_x, win_y, win_w, win_h, offset_x, offset_y)
    if curr_rect is None:
        return False, scores
    cx1, cy1, cx2, cy2 = curr_rect
    curr_crop = curr_img_cv[cy1:cy2, cx1:cx2]

    scale = min(1.0, 512.0 / max(pre_crop.shape[0], pre_crop.shape[1]))
    if scale < 1.0:
        pre_crop_eval = cv2.resize(pre_crop, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        curr_crop_eval = cv2.resize(
            curr_crop, (pre_crop_eval.shape[1], pre_crop_eval.shape[0]), interpolation=cv2.INTER_AREA
        )
    else:
        pre_crop_eval = pre_crop.copy()
        if pre_crop.shape != curr_crop.shape:
            curr_crop_eval = cv2.resize(
                curr_crop, (pre_crop.shape[1], pre_crop.shape[0]), interpolation=cv2.INTER_AREA
            )
        else:
            curr_crop_eval = curr_crop.copy()

    static_mask = np.ones_like(curr_crop_eval, dtype=np.uint8) * 255
    if global_dynamic_mask is not None:
        dynamic_crop = global_dynamic_mask[cy1:cy2, cx1:cx2]
        if scale < 1.0 or dynamic_crop.shape != curr_crop_eval.shape:
            dynamic_crop_eval = cv2.resize(
                dynamic_crop, (curr_crop_eval.shape[1], curr_crop_eval.shape[0]), interpolation=cv2.INTER_NEAREST
            )
        else:
            dynamic_crop_eval = dynamic_crop.copy()

        curr_crop_eval[dynamic_crop_eval == 255] = 0
        pre_crop_eval[dynamic_crop_eval == 255] = 0
        static_mask[dynamic_crop_eval == 255] = 0

    for (ml, mt, mr, mb) in get_masked_system_rects(
        offset_x + cx1, offset_y + cy1, scale, curr_crop_eval.shape[1], curr_crop_eval.shape[0]
    ):
        curr_crop_eval[mt:mb, ml:mr] = 0
        pre_crop_eval[mt:mb, ml:mr] = 0
        static_mask[mt:mb, ml:mr] = 0

    text_mask = detect_text_change_mask(pre_crop_eval, curr_crop_eval, fill_rect=True)
    curr_crop_eval[text_mask == 255] = 0
    pre_crop_eval[text_mask == 255] = 0
    static_mask[text_mask == 255] = 0

    diff_full = cv2.absdiff(pre_crop_eval, curr_crop_eval)
    _, thresh_full = cv2.threshold(diff_full, 30, 255, cv2.THRESH_BINARY)

    valid_area = np.count_nonzero(static_mask)
    total_area = pre_crop_eval.shape[0] * pre_crop_eval.shape[1]
    if valid_area > 500:
        diff_ratio = np.count_nonzero(thresh_full) / valid_area
    else:
        diff_ratio = np.count_nonzero(thresh_full) / total_area

    pre_edges = cv2.Canny(pre_crop_eval, 50, 150)
    curr_edges = cv2.Canny(curr_crop_eval, 50, 150)

    edge_diff = cv2.absdiff(pre_edges, curr_edges)
    if valid_area > 500:
        edge_diff_ratio = np.count_nonzero(cv2.bitwise_and(edge_diff, edge_diff, mask=static_mask)) / valid_area
    else:
        edge_diff_ratio = np.count_nonzero(edge_diff) / total_area

    res = cv2.matchTemplate(curr_crop_eval, pre_crop_eval, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)

    ssim_val = calculate_ssim(pre_crop_eval, curr_crop_eval, mask=static_mask)
    orb_score = calculate_orb_match(pre_crop_eval, curr_crop_eval, mask=static_mask)

    scores = {
        "diff_ratio": float(diff_ratio),
        "sim": float(max_val),
        "edge_sim": float(edge_diff_ratio),
        "ssim": ssim_val,
        "orb": orb_score,
    }

    is_exact_match = (diff_ratio <= 0.05) and (edge_diff_ratio <= 0.03) and (max_val >= 0.92)
    is_high_match = (diff_ratio <= 0.15) and (edge_diff_ratio <= 0.10) and (max_val >= 0.85)
    is_structural_match = (ssim_val >= 0.75) or (orb_score >= 0.15) or (orb_score >= 0.08 and max_val >= 0.6)

    return (is_exact_match or is_high_match or is_structural_match), scores
