"""Module: @role: マクロ実行時のUI要素テンプレートマッチング検証および自律自己修復(recovery_manager)を担当する。"""

import ctypes
import json
import logging
import platform
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import cv2
import numpy as np

from core.shared.window_utils import is_ignored_system_window

logger = logging.getLogger(__name__)

_INVALID_TARGET_LABELS = {
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
_GENERIC_TAGS = {"div", "span", "p", "a", "li", "ul", "ol", "body", "html", "section", "header", "footer"}


def _resolve_crop_image_path(target_dir: Path, raw_event_id: str, target_id: str) -> Optional[Path]:
    """合成接尾辞を除去したベースIDも含めて要素クロップ画像を解決する。"""
    images_dir = target_dir / "images"
    if not images_dir.exists():
        return None
    candidates = []
    if raw_event_id:
        candidates.append(images_dir / f"{raw_event_id}_crop.png")
        base_event_id = re.sub(r"(?:_nav_click|_chk_.*|_submit_.*)$", "", raw_event_id)
        if base_event_id and base_event_id != raw_event_id:
            candidates.append(images_dir / f"{base_event_id}_crop.png")
    for cand in candidates:
        if cand.exists():
            return cand
    if target_id:
        pattern = re.compile(rf"(?:^|[^0-9A-Za-z_]){re.escape(target_id)}(?:[^0-9A-Za-z_]|$)")
        found = sorted(p for p in images_dir.glob("*_crop.png") if pattern.search(p.stem))
        if found:
            return found[0]
    return None


def _has_semantic_locator(target_dir: Path, target_id: str, args: Dict[str, Any]) -> bool:
    """テンプレート不在時にUIA/OCR/セレクタで特定可能な根拠があるかを判定する。"""
    sel = str(args.get("selector") or "").strip()
    if sel and sel.lower() not in _GENERIC_TAGS:
        return True
    elem_name = str(args.get("element_name") or "").strip()
    if elem_name and elem_name.lower() not in _INVALID_TARGET_LABELS:
        return True
    if str(args.get("url") or "").strip().startswith("http"):
        return True

    ui_targets_path = target_dir / "ui_targets.json"
    if ui_targets_path.exists():
        try:
            with open(ui_targets_path, "r", encoding="utf-8") as f:
                ui_targets = json.load(f)
            role = str((ui_targets.get(target_id) or {}).get("semantic_role") or "").strip()
            if role and role.lower() not in _INVALID_TARGET_LABELS:
                return True
        except Exception:
            pass
    return False


def _resolve_target_hwnd(last_win_args: Optional[Dict[str, Any]]) -> int:
    hwnd = int((last_win_args.get("mapped_hwnd") if last_win_args else 0) or 0)
    if hwnd or platform.system() != "Windows" or not last_win_args:
        return hwnd
    fg_hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    if not fg_hwnd:
        return 0
    ln = ctypes.windll.user32.GetWindowTextLengthW(fg_hwnd)
    buf = ctypes.create_unicode_buffer(ln + 1)
    ctypes.windll.user32.GetWindowTextW(fg_hwnd, buf, ln + 1)
    return 0 if is_ignored_system_window(buf.value) else fg_hwnd


def _has_small_region_changed(prev_arr: np.ndarray, next_arr: np.ndarray) -> bool:
    """全画面平均ではなく有意差分ピクセル数で小UIの描画・出現変化を検知する。"""
    if prev_arr.shape != next_arr.shape:
        return True
    diff = np.abs(next_arr.astype(np.int16) - prev_arr.astype(np.int16))
    pix_diff = np.max(diff, axis=2) if diff.ndim == 3 else diff
    return int(np.count_nonzero(pix_diff > 20)) >= 200


def _evaluate_template_on_screen(
    current_img_pil: Any,
    monitor_info: Any,
    crop_image_path: Path,
    args: Dict[str, Any],
    window_offset: Tuple[int, int],
    workflow_id: str,
) -> Tuple[bool, Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    """現在画面に対してテンプレート照合し、(一致OKか, ドリフト代替座標, 局所グレースケール一致座標) を返す。"""
    m_left = int(monitor_info.get("left", 0)) if isinstance(monitor_info, dict) else 0
    m_top = int(monitor_info.get("top", 0)) if isinstance(monitor_info, dict) else 0

    current_img_cv = cv2.cvtColor(np.array(current_img_pil), cv2.COLOR_RGB2BGR)
    template_cv = cv2.imread(str(crop_image_path), cv2.IMREAD_COLOR)
    if (
        template_cv is None
        or template_cv.shape[0] > current_img_cv.shape[0]
        or template_cv.shape[1] > current_img_cv.shape[1]
    ):
        return False, None, None

    res = cv2.matchTemplate(current_img_cv, template_cv, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    tpl_h, tpl_w = template_cv.shape[:2]
    match_left = m_left + max_loc[0]
    match_top = m_top + max_loc[1]
    match_right = match_left + tpl_w
    match_bottom = match_top + tpl_h
    abs_match_x = match_left + tpl_w // 2
    abs_match_y = match_top + tpl_h // 2

    raw_x, raw_y = args.get("x"), args.get("y")
    if max_val < 0.8:
        logger.warning(f"[{workflow_id}] Template match failed (Confidence: {max_val:.2f}). Initiating Healer...")
        if raw_x is not None and raw_y is not None:
            exp_x = int(raw_x) + window_offset[0]
            exp_y = int(raw_y) + window_offset[1]
            if max_val >= 0.68 and (match_left - 25) <= exp_x <= (match_right + 25) and (match_top - 25) <= exp_y <= (match_bottom + 25):
                in_box = match_left <= exp_x <= match_right and match_top <= exp_y <= match_bottom
                return False, None, (exp_x, exp_y) if in_box else (abs_match_x, abs_match_y)
            # Why: フォーカス枠・入力済み等の軽微変化のみ厳格閾値と実一致位置で救済し空白誤爆を防ぐ
            pad = 35
            lx1 = max(0, (exp_x - m_left) - tpl_w // 2 - pad)
            ly1 = max(0, (exp_y - m_top) - tpl_h // 2 - pad)
            lx2 = min(current_img_cv.shape[1], (exp_x - m_left) + tpl_w // 2 + pad)
            ly2 = min(current_img_cv.shape[0], (exp_y - m_top) + tpl_h // 2 + pad)
            if (lx2 - lx1) >= tpl_w and (ly2 - ly1) >= tpl_h:
                loc_gray = cv2.cvtColor(current_img_cv[ly1:ly2, lx1:lx2], cv2.COLOR_BGR2GRAY)
                tpl_gray = cv2.cvtColor(template_cv, cv2.COLOR_BGR2GRAY)
                if float(np.std(tpl_gray)) >= 10.0:
                    _, g_val, _, g_loc = cv2.minMaxLoc(cv2.matchTemplate(loc_gray, tpl_gray, cv2.TM_CCOEFF_NORMED))
                    patch = loc_gray[g_loc[1] : g_loc[1] + tpl_h, g_loc[0] : g_loc[0] + tpl_w]
                    if g_val >= 0.78 and float(np.std(patch)) >= 10.0:
                        matched_cx = m_left + lx1 + g_loc[0] + tpl_w // 2
                        matched_cy = m_top + ly1 + g_loc[1] + tpl_h // 2
                        return False, None, (int(matched_cx), int(matched_cy))
        return False, None, None

    if raw_x is None or raw_y is None:
        return False, (abs_match_x, abs_match_y), None

    expected_x = int(raw_x) + window_offset[0]
    expected_y = int(raw_y) + window_offset[1]
    in_matched_box = (
        (match_left - 25) <= expected_x <= (match_right + 25)
        and (match_top - 25) <= expected_y <= (match_bottom + 25)
    )
    if not in_matched_box:
        dist = ((abs_match_x - expected_x) ** 2 + (abs_match_y - expected_y) ** 2) ** 0.5
        logger.warning(f"[{workflow_id}] Target UI drifted by {dist:.1f} pixels. Initiating Healer...")
        drift_fb = (abs_match_x, abs_match_y) if max_val >= 0.85 else None
        return False, drift_fb, None

    return True, None, None


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
    fast_fail: bool = False,
    heartbeat_callback: Optional[Callable[[], None]] = None,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    needs_recovery = False
    crop_image_path = _resolve_crop_image_path(target_dir, raw_event_id, target_id)
    recovery_info = None
    fallback_match_coords: Optional[Tuple[int, int]] = None
    local_in_place_coords: Optional[Tuple[int, int]] = None

    try:
        from core.shared.screen_capture import take_screenshot
        from core.healer.recovery_manager import attempt_recovery

        has_crop = bool(crop_image_path and crop_image_path.exists())
        if not has_crop and not _has_semantic_locator(target_dir, target_id, args):
            logger.info(
                f"[{workflow_id}] No crop image or semantic locator for '{target_id}' ({raw_event_id}); using recorded coords."
            )
            return True, None

        current_img_pil, monitor_info = take_screenshot()
        check_stop_callback()
        if heartbeat_callback:
            heartbeat_callback()

        if has_crop and crop_image_path is not None:
            matched_ok, fallback_match_coords, local_in_place_coords = _evaluate_template_on_screen(
                current_img_pil, monitor_info, crop_image_path, args, window_offset, workflow_id
            )
            needs_recovery = not matched_ok
        else:
            logger.info(f"[{workflow_id}] No crop image available; initiating semantic Healer for '{target_id}'...")
            needs_recovery = True

        if needs_recovery:
            check_stop_callback()
            if status_callback:
                status_callback("自己修復中...", True)

            target_hwnd = _resolve_target_hwnd(last_win_args)
            recovery_result = attempt_recovery(
                workflow_id,
                target_id,
                window_hwnd=target_hwnd,
                raw_event_id=raw_event_id,
                target_context=args,
                last_win_args=last_win_args,
                fast_mode=fast_fail,
                heartbeat_callback=heartbeat_callback,
            )
            check_stop_callback()
            if heartbeat_callback:
                heartbeat_callback()

            recovery_info = recovery_result
            if recovery_result.get("success"):
                new_coords = recovery_result.get("new_coordinates")
                if new_coords and "x" in new_coords and "y" in new_coords:
                    # Why: 実行時オフセットを逆算して格納し後続の二重加算を完全防止
                    args["x"] = int(new_coords["x"]) - window_offset[0]
                    args["y"] = int(new_coords["y"]) - window_offset[1]
                    logger.info(
                        f"[{workflow_id}] Healer successfully updated base coordinates to ({args['x']}, {args['y']})."
                    )
                    if status_callback:
                        status_callback("実行中...", False)
                    return True, recovery_info

            # Why: 描画遅延中の遷移を短周期ポーリングで検知し、画面変化後のみ再修復を1回試行
            if not fast_fail and fallback_match_coords is None:
                prev_img_arr = np.array(current_img_pil)
                re_recovered = False
                for _ in range(2):
                    time.sleep(0.45)
                    check_stop_callback()
                    if heartbeat_callback:
                        heartbeat_callback()
                    next_img_pil, next_monitor = take_screenshot()
                    check_stop_callback()
                    next_arr = np.array(next_img_pil)
                    screen_changed = _has_small_region_changed(prev_img_arr, next_arr)
                    prev_img_arr = next_arr

                    if has_crop and crop_image_path is not None:
                        matched_ok, fb_coords, lp_coords = _evaluate_template_on_screen(
                            next_img_pil, next_monitor, crop_image_path, args, window_offset, workflow_id
                        )
                        if matched_ok:
                            if status_callback:
                                status_callback("実行中...", False)
                            return True, None
                        fallback_match_coords = fallback_match_coords or fb_coords
                        local_in_place_coords = local_in_place_coords or lp_coords

                    if screen_changed and not re_recovered:
                        re_recovered = True
                        retry_res = attempt_recovery(
                            workflow_id,
                            target_id,
                            window_hwnd=target_hwnd,
                            raw_event_id=raw_event_id,
                            target_context=args,
                            last_win_args=last_win_args,
                            fast_mode=False,
                            heartbeat_callback=heartbeat_callback,
                        )
                        check_stop_callback()
                        recovery_info = retry_res
                        if retry_res.get("success"):
                            new_coords = retry_res.get("new_coordinates")
                            if new_coords and "x" in new_coords and "y" in new_coords:
                                args["x"] = int(new_coords["x"]) - window_offset[0]
                                args["y"] = int(new_coords["y"]) - window_offset[1]
                                if status_callback:
                                    status_callback("実行中...", False)
                                return True, recovery_info
                    if not screen_changed:
                        break

            chosen_fb = fallback_match_coords or local_in_place_coords
            fb_method = "template_drift_fallback" if fallback_match_coords is not None else "local_gray_fallback"
            if chosen_fb is not None:
                args["x"] = int(chosen_fb[0]) - window_offset[0]
                args["y"] = int(chosen_fb[1]) - window_offset[1]
                logger.info(f"[{workflow_id}] Using {fb_method} at ({args['x']}, {args['y']}).")
                if status_callback:
                    status_callback("実行中...", False)
                return True, {
                    "success": True,
                    "stage": 0,
                    "method": fb_method,
                    "new_coordinates": {"x": int(chosen_fb[0]), "y": int(chosen_fb[1])},
                }

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
