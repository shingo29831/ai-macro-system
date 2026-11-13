"""Module: @role: タイピング追跡時のOCRフレーム評価・スコアリング・候補プルーニングおよび差分輪郭BBox拡張を担当する。"""

import logging
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import Levenshtein
import numpy as np
from PIL import Image

from engines.ocr.reader import read_text_from_image

logger = logging.getLogger(__name__)


def collect_ocr_results(
    img_path: Path,
    active_search_areas: List[Tuple[int, Tuple[int, int, int, int]]],
    temp_dir: Path,
) -> List[Tuple[Any, Optional[int]]]:
    """指定画像または探索領域クロップに対してOCRを実行し、近接重複を除いた結果を返す。"""
    ocr_results: List[Tuple[Any, Optional[int]]] = []
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
        return []

    unique_results: List[Tuple[Any, Optional[int]]] = []
    seen_centers: List[Tuple[int, int]] = []
    for res, idx in ocr_results:
        if not res.content:
            continue
        cx = res.boundingBox.x + res.boundingBox.width // 2
        cy = res.boundingBox.y + res.boundingBox.height // 2
        if any(abs(cx - sx) < 20 and abs(cy - sy) < 20 for sx, sy in seen_centers):
            continue
        seen_centers.append((cx, cy))
        unique_results.append((res, idx))
    return unique_results


def score_and_update_candidates(
    ocr_results: List[Tuple[Any, Optional[int]]],
    field_candidates: List[Dict[str, Any]],
    buffer_to_check: str,
    is_ime: bool,
    to_hiragana_fn: Callable[[str], str],
    last_click_pos: Optional[Tuple[int, int]],
    img_path: Path,
    temp_dir: Path,
    target_event_id: str,
    ocr_call_count: int,
) -> int:
    """OCR認識結果と入力バッファの類似度・各種ペナルティを計算し、追跡候補を更新する。"""
    target_text = to_hiragana_fn(buffer_to_check) if is_ime else buffer_to_check
    target_lower = target_text.lower()
    buffer_lower = buffer_to_check.lower()

    for res, _area_idx in ocr_results:
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

        matched_cand = next(
            (
                cand
                for cand in field_candidates
                if abs(l - cand["bbox"][0]) < 30 and abs(t - cand["bbox"][1]) < 15
            ),
            None,
        )

        target_len = len(target_lower) if is_ime else len(buffer_lower)
        ocr_len = len(c_lower)
        len_penalty = min(0.8, (abs(target_len - ocr_len) / max(1, target_len)) * 0.3)

        change_penalty = 0.0
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
        elif len(c_lower) > max(3, target_len * 2):
            change_penalty = 0.3

        frame_score = similarity - dist_penalty - change_penalty - len_penalty
        logger.info(
            f"[{target_event_id}] Scoring OCR - Found: '{res.content}', Score: {frame_score:.2f} "
            f"(Sim: {similarity:.2f}, Pen: {dist_penalty:.2f}, Chg: {change_penalty:.2f}, Len: {len_penalty:.2f}), "
            f"Target: '{target_lower}' (IME: {is_ime})"
        )

        if similarity > 0.3 or is_substring:
            ocr_call_count += 1
            try:
                with Image.open(img_path) as tracking_img:
                    t_crop = tracking_img.crop((l, t, r, b))
                    t_crop_path = (
                        temp_dir / f"temp_ocr_crop_tracking_{target_event_id}_{img_path.stem}_{ocr_call_count}.png"
                    )
                    t_crop.save(t_crop_path)
            except Exception:
                pass

            if matched_cand:
                cl, ct, cr, cb = matched_cand["bbox"]
                matched_cand["score"] += frame_score * 2.0
                matched_cand["bbox"] = (min(l, cl), min(t, ct), max(r, cr), max(b, cb))
                matched_cand["last_text"] = res.content
            else:
                field_candidates.append(
                    {
                        "bbox": (l, t, r, b),
                        "score": frame_score,
                        "last_text": res.content,
                    }
                )

    return ocr_call_count


def prune_candidates_and_areas(
    field_candidates: List[Dict[str, Any]],
    active_search_areas: List[Tuple[int, Tuple[int, int, int, int]]],
    frame_count: int,
    target_event_id: str,
) -> Tuple[List[Dict[str, Any]], List[Tuple[int, Tuple[int, int, int, int]]]]:
    """スコアの低い候補および有効候補を含まない探索領域を枝刈りする。"""
    if field_candidates:
        max_score = max(cand["score"] for cand in field_candidates)
        surviving_candidates = []
        for cand in field_candidates:
            if max_score - cand["score"] <= 3.0:
                surviving_candidates.append(cand)
            else:
                logger.info(
                    f"[{target_event_id}] Pruning poor candidate at {cand['bbox']} "
                    f"(Score: {cand['score']:.2f}, Max: {max_score:.2f})"
                )
        field_candidates = surviving_candidates

    if active_search_areas and field_candidates:
        global_max_score = max(cand["score"] for cand in field_candidates)
        surviving_areas = []
        for idx, s_area in active_search_areas:
            sl, st, sr, sb = s_area
            area_has_candidate = False
            area_max = -float("inf")
            for cand in field_candidates:
                cl, ct, cr, cb = cand["bbox"]
                cx, cy = (cl + cr) / 2, (ct + cb) / 2
                if sl - 50 <= cx <= sr + 50 and st - 50 <= cy <= sb + 50:
                    area_has_candidate = True
                    area_max = max(area_max, cand["score"])

            if frame_count >= 3 and not area_has_candidate:
                logger.info(
                    f"[{target_event_id}] Pruning search area {idx} (No valid candidates found in early frames)"
                )
                continue
            if area_has_candidate and (global_max_score - area_max > 3.0):
                logger.info(
                    f"[{target_event_id}] Pruning search area {idx} "
                    f"(Area Max: {area_max:.2f}, Global Max: {global_max_score:.2f})"
                )
                continue
            surviving_areas.append((idx, s_area))

        if surviving_areas:
            active_search_areas = surviving_areas

    return field_candidates, active_search_areas


def expand_final_bbox_by_diff_contours(
    base_img_path: Path,
    final_img_path: Path,
    bbox: Tuple[int, int, int, int],
    target_event_id: str,
) -> Tuple[int, int, int, int]:
    """入力前後画像のピクセル差分輪郭からテキスト領域を結合し、最終BBoxを拡張する。"""
    l, t, r, b = bbox
    if not (final_img_path.exists() and base_img_path.exists()):
        return bbox

    try:
        img_base = cv2.imread(str(base_img_path), cv2.IMREAD_GRAYSCALE)
        img_final = cv2.imread(str(final_img_path), cv2.IMREAD_GRAYSCALE)
        diff = cv2.absdiff(img_base, img_final)
        _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
        kernel = np.ones((5, 15), np.uint8)
        dilated = cv2.dilate(thresh, kernel, iterations=1)
        contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        raw_bboxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w > 5 and h > 5:
                raw_bboxes.append((x, y, w, h))

        filtered_bboxes = []
        for i, (x1, y1, w1, h1) in enumerate(raw_bboxes):
            is_enclosing = any(
                i != j
                and x1 <= x2
                and y1 <= y2
                and x1 + w1 >= x2 + w2
                and y1 + h1 >= y2 + h2
                and (w1 > w2 or h1 > h2)
                for j, (x2, y2, w2, h2) in enumerate(raw_bboxes)
            )
            if not is_enclosing:
                filtered_bboxes.append((x1, y1, w1, h1))

        target_contours = [
            (x, y, w, h)
            for (x, y, w, h) in filtered_bboxes
            if (t - 20) <= (y + h / 2) <= (b + 20) and (x + w) > (l - 15)
        ]

        if target_contours:
            min_x = min(bx for bx, _, _, _ in target_contours)
            min_y = min(by for _, by, _, _ in target_contours)
            max_r = max(bx + bw for bx, _, bw, _ in target_contours)
            max_b = max(by + bh for _, by, _, bh in target_contours)
            final_crop_box = (max(0, min(l, min_x)), min_y, max_r, max_b)
            logger.info(
                f"[{target_event_id}] Logically expanded BBox by combining text contours (excluding left icons): {final_crop_box}"
            )
            return final_crop_box
        return (l, t, r + 200, b)
    except Exception as e:
        logger.warning(f"[{target_event_id}] Failed to refine and expand final BBox: {e}")
        return bbox
