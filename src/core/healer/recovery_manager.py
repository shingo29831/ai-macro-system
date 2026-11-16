# src/core/healer/recovery_manager.py
# @role: 実行中の異常（UI変更や座標ズレ）を検知し、システムを止めずに自律修復を試みる。
# 
# 【参照元】
#   - core/executor/runner.py (ポーリング待機がタイムアウトまたは画像差分エラー発生時)
# 
# 【参照先】
#   - engines/yolo/detector.py (座標上書き用UI検出)
#   - engines/ocr/reader.py (座標上書き用テキスト検出)

import ctypes
import json
import logging
import platform
import re
import threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.shared.macro_paths import get_macros_root
from core.shared.screen_capture import take_screenshot
from core.shared.window_utils import get_window_bounds, is_browser_window, is_ignored_system_window
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
_GENERIC_SELECTOR_TAGS = {"div", "span", "p", "a", "li", "ul", "ol", "body", "html", "section", "header", "footer"}
_CONTAINER_CONTROL_TYPES = {"windowcontrol", "documentcontrol", "titlebarcontrol", "scrollbarcontrol"}

def _is_logo_symbol_noise(text: str, bbox: Any = None, confidence: float = 1.0) -> bool:
    # Why: ロゴやアイコン枠線のOCR誤認識（幾何記号・罫線・単一文字）を完全除外
    if not text:
        return True
    cleaned = text.strip()
    if not cleaned:
        return True

    meaningful = re.findall(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fffA-Za-z0-9]', cleaned)
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

def _match_target_text(target: str, candidate: str) -> Tuple[bool, float]:
    # Why: 1文字ノイズによる逆包含誤爆を根絶し完全一致または部分単語・高類似度のみ判定
    t_clean = re.sub(r'[\s\-_]', '', target.lower())
    c_clean = re.sub(r'[\s\-_]', '', candidate.lower())
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


def _resolve_crop_path(target_dir: Path, raw_event_id: str, target_id: str) -> Optional[Path]:
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


def _get_effective_hwnd(window_hwnd: int) -> int:
    """明示されたHWNDが有効かつ自システム/除外対象外である場合のみ返す。"""
    if platform.system() != "Windows" or not window_hwnd:
        return 0
    try:
        user32 = ctypes.windll.user32
        hwnd_int = int(window_hwnd)
        if not user32.IsWindow(hwnd_int):
            return 0
        ln = user32.GetWindowTextLengthW(hwnd_int)
        if ln > 0:
            buf = ctypes.create_unicode_buffer(ln + 1)
            user32.GetWindowTextW(hwnd_int, buf, ln + 1)
            if is_ignored_system_window(buf.value):
                return 0
        return hwnd_int
    except Exception:
        return 0


def _get_window_crop_box(
    hwnd: int, monitor_info: Any, img_w: int, img_h: int
) -> Optional[Tuple[int, int, int, int]]:
    """対象ウィンドウ矩形をスクリーンショット内の切り抜き座標 (x1, y1, x2, y2) へ変換する。"""
    if platform.system() != "Windows" or not hwnd:
        return None
    try:
        bounds = get_window_bounds(int(hwnd))
        if not bounds:
            return None
        left, top, right, bottom = bounds
        m_left = int(monitor_info.get("left", 0)) if isinstance(monitor_info, dict) else 0
        m_top = int(monitor_info.get("top", 0)) if isinstance(monitor_info, dict) else 0
        x1 = max(0, min(img_w, left - m_left))
        y1 = max(0, min(img_h, top - m_top))
        x2 = max(0, min(img_w, right - m_left))
        y2 = max(0, min(img_h, bottom - m_top))
        if (x2 - x1) >= 40 and (y2 - y1) >= 40:
            return x1, y1, x2, y2
    except Exception:
        pass
    return None


def _try_browser_uia_recovery(
    workflow_id: str,
    target_context: Optional[Dict[str, Any]],
    last_win_args: Optional[Dict[str, Any]],
    candidate_labels: List[str],
) -> Optional[Dict[str, Any]]:
    if not target_context:
        return None
    win_title = str(last_win_args.get("window_title") or "") if last_win_args else ""
    selector = str(target_context.get("selector") or "").strip()
    sel_usable = bool(selector and selector.lower() not in _GENERIC_SELECTOR_TAGS)
    url = str(target_context.get("url") or "").strip()
    if not (is_browser_window(win_title) or sel_usable or url.startswith("http")):
        return None

    elem_name = str(target_context.get("element_name") or "").strip() or (candidate_labels[0] if candidate_labels else "")
    if not (sel_usable or elem_name or url.startswith("http")):
        return None

    try:
        from core.executor.browser_uia_scroller import scroll_into_view
        from core.executor.browser_uia_searcher import find_uia_element

        # Why: ズレた旧座標でのControlFromPoint誤爆を防ぐため座標を渡さずDOM/UIA名で探索
        elem = find_uia_element(
            selector=selector if sel_usable else None,
            last_win_args=last_win_args,
            timeout_sec=1.2,
            element_name=elem_name or None,
            url=url or None,
            text=target_context.get("text"),
        )
        if not elem:
            return None

        rect = elem.rectangle()
        if rect and rect.width() > 0 and rect.height() > 0:
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
            win_bounds = get_window_bounds(target_hwnd)
            in_viewport = cx > 0 and cy > 0
            if win_bounds:
                wl, wt, wr, wb = win_bounds
                in_viewport = wl <= cx <= wr and (wt + 80) <= cy <= (wb - 40)
            if in_viewport:
                logger.info(f"[{workflow_id}] Stage 1 (Browser UIA) recovered '{elem_name or selector}' at ({cx}, {cy}).")
                return {
                    "success": True,
                    "stage": 1,
                    "method": "browser_uia",
                    "new_coordinates": {"x": int(cx), "y": int(cy)},
                }

            scrolled_coords = scroll_into_view(
                selector=selector if sel_usable else None,
                last_win_args=last_win_args,
                timeout_sec=1.0,
                element_name=elem_name or None,
                url=url or None,
                text=target_context.get("text"),
            )
            if scrolled_coords and scrolled_coords[0] > 0 and scrolled_coords[1] > 0:
                logger.info(
                    f"[{workflow_id}] Stage 1 (Browser Scroll) recovered '{elem_name or selector}' at {scrolled_coords}."
                )
                return {
                    "success": True,
                    "stage": 1,
                    "method": "browser_scroll",
                    "new_coordinates": {"x": int(scrolled_coords[0]), "y": int(scrolled_coords[1])},
                }
    except Exception as b_err:
        logger.debug(f"[{workflow_id}] Browser UIA recovery bypassed: {b_err}")
    return None


def _is_valid_uia_target_rect(rect: Any, win_bounds: Optional[Tuple[int, int, int, int]]) -> bool:
    if not rect or rect.width() <= 0 or rect.height() <= 0:
        return False
    cx = rect.left + rect.width() // 2
    cy = rect.top + rect.height() // 2
    if win_bounds:
        wl, wt, wr, wb = win_bounds
        if not (wl <= cx <= wr and wt <= cy <= wb):
            return False
        win_w = max(1, wr - wl)
        win_h = max(1, wb - wt)
        # Why: タブやページ全体など巨大コンテナの部分一致誤爆を防止
        if rect.width() >= win_w * 0.85 and rect.height() >= win_h * 0.7:
            return False
        if rect.height() >= win_h * 0.8:
            return False
    return True


def _try_windows_uia_recovery(
    workflow_id: str,
    effective_hwnd: int,
    candidate_labels: List[str],
    timeout_sec: float = 1.8,
) -> Optional[Dict[str, Any]]:
    if platform.system() != "Windows" or not effective_hwnd or not candidate_labels:
        return None

    result_holder: List[Optional[Dict[str, Any]]] = [None]
    done_event = threading.Event()
    win_bounds = get_window_bounds(effective_hwnd)

    def _worker() -> None:
        try:
            import uiautomation as auto

            # Why: 実行スレッドのCOMアパートメント競合と無制限走査フリーズを回避
            with auto.UIAutomationInitializerInThread():
                root_ctrl = auto.ControlFromHandle(effective_hwnd)
                if not (root_ctrl and root_ctrl.Exists(0, 0)):
                    return

                for label in candidate_labels:
                    matched_ctrl = root_ctrl.Control(searchDepth=16, Name=label)
                    if not matched_ctrl or not matched_ctrl.Exists(0, 0):
                        matched_ctrl = root_ctrl.Control(searchDepth=16, SubName=label)
                    if matched_ctrl and matched_ctrl.Exists(0, 0) and not getattr(matched_ctrl, "IsOffscreen", False):
                        ct_name = str(getattr(matched_ctrl, "ControlTypeName", "") or "").lower()
                        rect = matched_ctrl.BoundingRectangle
                        if ct_name not in _CONTAINER_CONTROL_TYPES and _is_valid_uia_target_rect(rect, win_bounds):
                            cx = rect.left + rect.width() // 2
                            cy = rect.top + rect.height() // 2
                            logger.info(f"[{workflow_id}] Stage 1 (UIA) recovered '{label}' at ({cx}, {cy}).")
                            result_holder[0] = {
                                "success": True,
                                "stage": 1,
                                "method": "uia",
                                "new_coordinates": {"x": int(cx), "y": int(cy)},
                            }
                            return

                lower_labels = [lb.lower() for lb in candidate_labels if len(lb) >= 2]
                for ctrl, _depth in auto.WalkControl(root_ctrl, maxDepth=16):
                    if done_event.is_set():
                        return
                    if getattr(ctrl, "IsOffscreen", False):
                        continue
                    ct_name = str(getattr(ctrl, "ControlTypeName", "") or "").lower()
                    if ct_name in _CONTAINER_CONTROL_TYPES:
                        continue
                    rect = getattr(ctrl, "BoundingRectangle", None)
                    if not _is_valid_uia_target_rect(rect, win_bounds):
                        continue
                    name = str(getattr(ctrl, "Name", "") or "").strip().lower()
                    aid = str(getattr(ctrl, "AutomationId", "") or "").strip().lower()
                    if not name and not aid:
                        continue
                    if any(lb == name or lb == aid or lb in name or lb in aid for lb in lower_labels):
                        cx = rect.left + rect.width() // 2
                        cy = rect.top + rect.height() // 2
                        logger.info(f"[{workflow_id}] Stage 1 (UIA Walk) recovered '{name or aid}' at ({cx}, {cy}).")
                        result_holder[0] = {
                            "success": True,
                            "stage": 1,
                            "method": "uia_walk",
                            "new_coordinates": {"x": int(cx), "y": int(cy)},
                        }
                        return
        except Exception as uia_err:
            logger.debug(f"[{workflow_id}] UIA recovery bypassed: {uia_err}")
        finally:
            done_event.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    if done_event.wait(timeout=timeout_sec):
        return result_holder[0]
    done_event.set()
    logger.debug(f"[{workflow_id}] Stage 1 (UIA) timed out after {timeout_sec}s.")
    return None


def _match_multiscale_template(
    search_img: Any,
    tpl_img: Any,
    threshold: float,
    scales: List[float],
) -> Optional[Tuple[int, int, int, int, float]]:
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
    effective_hwnd = _get_effective_hwnd(window_hwnd)

    # --- Stage 1: Browser/Windows UI Automation (最速・高精度) ---
    browser_uia_res = _try_browser_uia_recovery(workflow_id, target_context, last_win_args, candidate_labels)
    if browser_uia_res:
        return browser_uia_res

    win_uia_res = _try_windows_uia_recovery(workflow_id, effective_hwnd, candidate_labels)
    if win_uia_res:
        return win_uia_res

    # --- Stage 2: テンプレート画像のマルチスケール照合 (レスポンシブ・微小ズレ救済) ---
    import cv2
    import numpy as np

    crop_path = _resolve_crop_path(target_dir, raw_event_id, target_id)
    full_img, monitor_info = take_screenshot()
    m_left = int(monitor_info.get("left", 0)) if isinstance(monitor_info, dict) else 0
    m_top = int(monitor_info.get("top", 0)) if isinstance(monitor_info, dict) else 0
    win_crop_box = _get_window_crop_box(effective_hwnd, monitor_info, full_img.width, full_img.height)

    if crop_path and crop_path.exists():
        try:
            tpl_img = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
            screen_bgr = cv2.cvtColor(np.array(full_img), cv2.COLOR_RGB2BGR)
            if tpl_img is not None and screen_bgr is not None:
                scales = [1.0, 0.9, 1.1, 0.85, 1.15, 0.8, 1.2]
                matched = None
                off_x, off_y = 0, 0

                if win_crop_box:
                    x1, y1, x2, y2 = win_crop_box
                    win_bgr = screen_bgr[y1:y2, x1:x2]
                    matched = _match_multiscale_template(win_bgr, tpl_img, 0.72, scales)
                    if matched:
                        off_x, off_y = x1, y1
                    else:
                        # Why: ホバー色変化やテーマ色差を吸収するため対象ウィンドウ内限定でグレースケール照合
                        win_gray = cv2.cvtColor(win_bgr, cv2.COLOR_BGR2GRAY)
                        tpl_gray = cv2.cvtColor(tpl_img, cv2.COLOR_BGR2GRAY)
                        matched = _match_multiscale_template(win_gray, tpl_gray, 0.78, scales)
                        if matched:
                            off_x, off_y = x1, y1

                if not matched:
                    matched = _match_multiscale_template(screen_bgr, tpl_img, 0.72, scales)

                if matched:
                    bx, by, bw, bh, score = matched
                    cx = m_left + off_x + bx + bw // 2
                    cy = m_top + off_y + by + bh // 2
                    logger.info(f"[{workflow_id}] Stage 2 (Template) recovered target at ({cx}, {cy}) (Score: {score:.2f}).")
                    return {
                        "success": True,
                        "stage": 2,
                        "method": "template_match",
                        "new_coordinates": {"x": int(cx), "y": int(cy)},
                    }
        except Exception as cv_err:
            logger.debug(f"[{workflow_id}] Template match recovery bypassed: {cv_err}")

    # --- Stage 3: クリーンアップ済みOCRビジョンスキャン (YOLO誤爆排除) ---
    if candidate_labels:
        temp_dir = target_dir / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_img_path = temp_dir / f"recovery_s3_{target_id}.png"

        try:
            ocr_offset_x, ocr_offset_y = 0, 0
            ocr_img = full_img
            if win_crop_box:
                x1, y1, x2, y2 = win_crop_box
                ocr_img = full_img.crop((x1, y1, x2, y2))
                ocr_offset_x, ocr_offset_y = x1, y1

            ocr_img.save(temp_img_path)
            # Why: 誤検出の原因となるYOLOを遮断しクリーンアップ済みOCRのみ実行
            ocr_results = read_text_from_image(str(temp_img_path))

            found_box = None
            matched_label = ""
            best_score = 0.0

            if ocr_results:
                for ocr_res in ocr_results:
                    raw_text = getattr(ocr_res, "content", "")
                    conf = getattr(ocr_res, "confidence", 1.0)
                    box = getattr(ocr_res, "boundingBox", None)

                    if _is_logo_symbol_noise(raw_text, box, conf):
                        continue

                    for label in candidate_labels:
                        is_match, match_score = _match_target_text(label, raw_text)
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

    logger.warning(f"[{workflow_id}] All recovery stages exhausted for '{target_id}'.")
    return {"success": False, "reason": "element_not_found"}
