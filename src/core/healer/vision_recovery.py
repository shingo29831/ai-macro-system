"""Module: @role: マルチスケールテンプレート照合(Stage 2)およびOCRスキャン(Stage 3)による画像ベース自己修復を担当する。"""

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def is_logo_symbol_noise(text: str, bbox: Any = None, confidence: float = 1.0) -> bool:
    """ロゴやアイコン枠線に由来するOCR誤認識ノイズを判定する。"""
    # Why: ロゴやアイコン枠線のOCR誤認識（幾何記号・罫線・単一文字）を完全除外
    if not text:
        return True
    cleaned = text.strip()
    if not cleaned:
        return True

    meaningful = re.findall(r"[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fffA-Za-z0-9]", cleaned)
    if not meaningful:
        return True

    symbol_ratio = (len(cleaned) - len(meaningful)) / len(cleaned)
    if symbol_ratio >= 0.5:
        return True

    if len(meaningful) == 1:
        if meaningful[0] in "oOclIivVxXuU一ロ口十人入八二三日目田-|_~^+=*/\\":
            return True
        if confidence < 0.75:
            return True

    if bbox:
        w = getattr(bbox, "width", 0) or getattr(bbox, "w", 0)
        h = getattr(bbox, "height", 0) or getattr(bbox, "h", 0)
        if 0 < w <= 65 and 0 < h <= 65:
            aspect = w / h
            if 0.7 <= aspect <= 1.4 and len(meaningful) <= 2:
                return True

    return False


def match_target_text(target: str, candidate: str) -> Tuple[bool, float]:
    """対象ラベルとOCR候補文字列の一致可否およびスコアを算出する。"""
    # Why: 1文字ノイズによる逆包含誤爆を根絶し完全一致または部分単語・高類似度のみ判定
    t_clean = re.sub(r"[\s\-_]", "", target.lower())
    c_clean = re.sub(r"[\s\-_]", "", candidate.lower())
    if not t_clean or not c_clean:
        return False, 0.0

    if t_clean == c_clean:
        return True, 1.0

    if len(t_clean) >= 2 and t_clean in c_clean:
        return True, 0.95

    if len(c_clean) >= 2 and c_clean in t_clean:
        ratio = len(c_clean) / len(t_clean)
        if ratio >= 0.6:
            return True, 0.85 * ratio

    sim = SequenceMatcher(None, t_clean, c_clean).ratio()
    if sim >= 0.75 and len(t_clean) >= 2:
        return True, sim

    return False, 0.0


def resolve_crop_path(target_dir: Path, raw_event_id: str, target_id: str) -> Optional[Path]:
    """イベントIDおよびターゲットIDから要素クロップ画像パスを解決する。"""
    images_dir = target_dir / "images"
    if not images_dir.exists():
        return None
    if raw_event_id:
        direct = images_dir / f"{raw_event_id}_crop.png"
        if direct.exists():
            return direct
        base_id = re.sub(r"(?:_nav_click|_chk_.*|_submit_.*)$", "", raw_event_id)
        if base_id and base_id != raw_event_id:
            base_crop = images_dir / f"{base_id}_crop.png"
            if base_crop.exists():
                return base_crop
    if target_id:
        pattern = re.compile(rf"(?:^|[^0-9A-Za-z_]){re.escape(target_id)}(?:[^0-9A-Za-z_]|$)")
        found_crops = sorted(p for p in images_dir.glob("*_crop.png") if pattern.search(p.stem))
        if found_crops:
            return found_crops[0]
    return None


def match_multiscale_template(
    search_img: Any,
    tpl_img: Any,
    threshold: float,
    scales: List[float],
) -> Optional[Tuple[int, int, int, int, float]]:
    """複数スケールでテンプレートマッチングを実行し、閾値以上の最良矩形を返す。"""
    import cv2

    best_val, best_loc, best_w, best_h = -1.0, None, 0, 0
    for scale in scales:
        if scale != 1.0:
            tw, th = int(tpl_img.shape[1] * scale), int(tpl_img.shape[0] * scale)
            if tw >= search_img.shape[1] or th >= search_img.shape[0] or tw < 10 or th < 10:
                continue
            resized_tpl = cv2.resize(tpl_img, (tw, th), interpolation=cv2.INTER_AREA)
        else:
            tw, th = tpl_img.shape[1], tpl_img.shape[0]
            if tw >= search_img.shape[1] or th >= search_img.shape[0]:
                continue
            resized_tpl = tpl_img

        res = cv2.matchTemplate(search_img, resized_tpl, cv2.TM_CCOEFF_NORMED)
        _, max_v, _, max_l = cv2.minMaxLoc(res)
        if max_v > best_val:
            best_val, best_loc, best_w, best_h = max_v, max_l, tw, th

    if best_val >= threshold and best_loc is not None:
        return best_loc[0], best_loc[1], best_w, best_h, float(best_val)
    return None


def try_template_recovery(
    workflow_id: str,
    target_dir: Path,
    raw_event_id: str,
    target_id: str,
    full_img: Any,
    monitor_info: Any,
    win_crop_box: Optional[Tuple[int, int, int, int]],
    crop_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """テンプレート画像のマルチスケールおよびグレースケール照合により座標を修復する。"""
    import cv2
    import numpy as np

    resolved_crop = crop_path if crop_path is not None else resolve_crop_path(target_dir, raw_event_id, target_id)
    if not resolved_crop or not resolved_crop.exists():
        return None

    m_left = int(monitor_info.get("left", 0)) if isinstance(monitor_info, dict) else 0
    m_top = int(monitor_info.get("top", 0)) if isinstance(monitor_info, dict) else 0

    try:
        tpl_img = cv2.imread(str(resolved_crop), cv2.IMREAD_COLOR)
        screen_bgr = cv2.cvtColor(np.array(full_img), cv2.COLOR_RGB2BGR)
        if tpl_img is None or screen_bgr is None:
            return None

        win_scales = [1.0, 0.9, 1.1, 0.85, 1.15, 0.8, 1.2, 0.67, 1.5]
        full_scales = [1.0, 0.8, 1.25, 0.67, 1.5]
        matched = None
        off_x, off_y = 0, 0
        tpl_gray = cv2.cvtColor(tpl_img, cv2.COLOR_BGR2GRAY)

        if win_crop_box:
            x1, y1, x2, y2 = win_crop_box
            win_bgr = screen_bgr[y1:y2, x1:x2]
            matched = match_multiscale_template(win_bgr, tpl_img, 0.72, win_scales)
            if matched:
                off_x, off_y = x1, y1
            else:
                # Why: ホバー色変化やテーマ色差を吸収するためグレースケール照合を実行
                win_gray = cv2.cvtColor(win_bgr, cv2.COLOR_BGR2GRAY)
                matched = match_multiscale_template(win_gray, tpl_gray, 0.76, win_scales)
                if matched:
                    off_x, off_y = x1, y1

        if not matched:
            matched = match_multiscale_template(screen_bgr, tpl_img, 0.74, full_scales)

        if not matched:
            screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
            matched = match_multiscale_template(screen_gray, tpl_gray, 0.78, full_scales)
            off_x, off_y = 0, 0

        if matched:
            bx, by, bw, bh, score = matched
            cx = m_left + off_x + bx + bw // 2
            cy = m_top + off_y + by + bh // 2
            logger.info(
                f"[{workflow_id}] Stage 2 (Template) recovered target at ({cx}, {cy}) (Score: {score:.2f})."
            )
            return {
                "success": True,
                "stage": 2,
                "method": "template_match",
                "new_coordinates": {"x": int(cx), "y": int(cy)},
            }
    except Exception as cv_err:
        logger.debug(f"[{workflow_id}] Template match recovery bypassed: {cv_err}")
    return None


def _match_multiscale_edges(
    search_gray: Any,
    tpl_gray: Any,
    threshold: float,
    scales: List[float],
) -> Optional[Tuple[int, int, int, int, float]]:
    """グレースケール画像をスケール後にCanny算出・微膨張させてエッジ途切れを防ぐ。"""
    import cv2
    import numpy as np

    kernel = np.ones((2, 2), np.uint8)
    search_edges = cv2.dilate(cv2.Canny(search_gray, 50, 150), kernel, iterations=1)
    best_val, best_loc, best_w, best_h = -1.0, None, 0, 0

    for scale in scales:
        tw, th = int(tpl_gray.shape[1] * scale), int(tpl_gray.shape[0] * scale)
        if tw >= search_edges.shape[1] or th >= search_edges.shape[0] or tw < 16 or th < 12:
            continue
        scaled_tpl = tpl_gray if scale == 1.0 else cv2.resize(tpl_gray, (tw, th), interpolation=cv2.INTER_AREA)
        tpl_edges = cv2.dilate(cv2.Canny(scaled_tpl, 50, 150), kernel, iterations=1)
        if int(np.count_nonzero(tpl_edges)) < 20:
            continue
        res = cv2.matchTemplate(search_edges, tpl_edges, cv2.TM_CCOEFF_NORMED)
        _, max_v, _, max_l = cv2.minMaxLoc(res)
        if max_v > best_val:
            best_val, best_loc, best_w, best_h = max_v, max_l, tw, th

    if best_val >= threshold and best_loc is not None:
        return best_loc[0], best_loc[1], best_w, best_h, float(best_val)
    return None


def try_edge_template_recovery(
    workflow_id: str,
    target_dir: Path,
    raw_event_id: str,
    target_id: str,
    full_img: Any,
    monitor_info: Any,
    win_crop_box: Optional[Tuple[int, int, int, int]],
    crop_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Stage 3 OCR後でも特定できない色反転・テーマ変更UIをCannyエッジ構造一致で最終救済する。"""
    import cv2
    import numpy as np

    resolved_crop = crop_path if crop_path is not None else resolve_crop_path(target_dir, raw_event_id, target_id)
    if not resolved_crop or not resolved_crop.exists():
        return None

    m_left = int(monitor_info.get("left", 0)) if isinstance(monitor_info, dict) else 0
    m_top = int(monitor_info.get("top", 0)) if isinstance(monitor_info, dict) else 0

    try:
        tpl_gray = cv2.imread(str(resolved_crop), cv2.IMREAD_GRAYSCALE)
        screen_gray = cv2.cvtColor(np.array(full_img), cv2.COLOR_RGB2GRAY)
        if tpl_gray is None or screen_gray is None or tpl_gray.shape[0] < 12 or tpl_gray.shape[1] < 16:
            return None

        matched = None
        off_x, off_y = 0, 0
        if win_crop_box:
            x1, y1, x2, y2 = win_crop_box
            matched = _match_multiscale_edges(screen_gray[y1:y2, x1:x2], tpl_gray, 0.58, [1.0, 0.9, 1.1, 0.8, 1.25])
            if matched:
                off_x, off_y = x1, y1
        if not matched:
            matched = _match_multiscale_edges(screen_gray, tpl_gray, 0.62, [1.0, 0.8, 1.25])

        if matched:
            bx, by, bw, bh, score = matched
            cx = m_left + off_x + bx + bw // 2
            cy = m_top + off_y + by + bh // 2
            logger.info(
                f"[{workflow_id}] Stage 2b (Edge Fallback) recovered target at ({cx}, {cy}) (Score: {score:.2f})."
            )
            return {
                "success": True,
                "stage": 2,
                "method": "edge_template_match",
                "new_coordinates": {"x": int(cx), "y": int(cy)},
            }
    except Exception as edge_err:
        logger.debug(f"[{workflow_id}] Edge template fallback bypassed: {edge_err}")
    return None


def try_ocr_recovery(
    workflow_id: str,
    target_dir: Path,
    target_id: str,
    candidate_labels: List[str],
    full_img: Any,
    monitor_info: Any,
    win_crop_box: Optional[Tuple[int, int, int, int]],
    read_text_fn: Callable[[str], List[Any]],
) -> Optional[Dict[str, Any]]:
    """ノイズフィルタリング済みOCR結果と候補ラベルの照合により座標を修復する。"""
    if not candidate_labels:
        return None

    m_left = int(monitor_info.get("left", 0)) if isinstance(monitor_info, dict) else 0
    m_top = int(monitor_info.get("top", 0)) if isinstance(monitor_info, dict) else 0

    temp_dir = target_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    # Why: target_idのパス区切り文字等によるパストラバーサルや不正ファイル名エラーを防止
    safe_target_id = re.sub(r"[^0-9A-Za-z_.-]", "_", str(target_id or "unknown")).strip("._") or "unknown"
    temp_img_path = temp_dir / f"recovery_s3_{safe_target_id}.png"

    try:
        ocr_offset_x, ocr_offset_y = 0, 0
        ocr_img = full_img
        if win_crop_box:
            x1, y1, x2, y2 = win_crop_box
            ocr_img = full_img.crop((x1, y1, x2, y2))
            ocr_offset_x, ocr_offset_y = x1, y1

        ocr_img.save(temp_img_path)
        # Why: 誤検出の原因となるYOLOを遮断しクリーンアップ済みOCRのみ実行
        ocr_results = read_text_fn(str(temp_img_path))

        found_box = None
        matched_label = ""
        best_score = 0.0

        if ocr_results:
            for ocr_res in ocr_results:
                raw_text = getattr(ocr_res, "content", "")
                conf = getattr(ocr_res, "confidence", 1.0)
                box = getattr(ocr_res, "boundingBox", None)

                if is_logo_symbol_noise(raw_text, box, conf):
                    continue

                for label in candidate_labels:
                    is_match, match_score = match_target_text(label, raw_text)
                    if is_match:
                        combined = match_score * conf
                        if combined > best_score:
                            best_score = combined
                            found_box = box
                            matched_label = label

        if found_box:
            cx = m_left + ocr_offset_x + getattr(found_box, "x", 0) + (getattr(found_box, "width", 0) // 2)
            cy = m_top + ocr_offset_y + getattr(found_box, "y", 0) + (getattr(found_box, "height", 0) // 2)
            logger.info(
                f"[{workflow_id}] Stage 3 (Clean OCR) recovered '{matched_label}' at ({cx}, {cy}) (Score: {best_score:.2f})."
            )
            return {
                "success": True,
                "stage": 3,
                "method": "clean_ocr",
                "new_coordinates": {"x": int(cx), "y": int(cy)},
            }
    except Exception as e:
        logger.error(f"[{workflow_id}] Critical error during OCR recovery: {e}")
    finally:
        if temp_img_path.exists():
            try:
                temp_img_path.unlink()
            except Exception as cleanup_err:
                logger.debug(f"[{workflow_id}] Failed to remove temp recovery image: {cleanup_err}")

    return None
