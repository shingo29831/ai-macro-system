"""Module: @role: 記録時画面画像(SSIM/ORB)との一致位置まで自律ホイールスクロール探索を担当する。"""

import ctypes
import logging
import platform
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import cv2
import numpy as np

from core.executor.screen_comparison import is_screen_match, notify_execution_status
from core.recorder.screen_capturer import take_screenshot

logger = logging.getLogger(__name__)


def _send_wheel_scroll(notches: int, wheel_delta: int, mouse_event_wheel: int, mouse_ctrl: Any) -> None:
    if platform.system() == "Windows":
        wheel_val = int(notches * wheel_delta)
        ctypes.windll.user32.mouse_event(mouse_event_wheel, 0, 0, ctypes.c_long(wheel_val).value, 0)
    else:
        mouse_ctrl.scroll(0, notches)


def _capture_gray_and_offset() -> Tuple[np.ndarray, int, int]:
    step_pil, step_mon = take_screenshot()
    step_gray = cv2.cvtColor(np.array(step_pil), cv2.COLOR_RGB2GRAY)
    s_off_x = step_mon.get("left", 0) if isinstance(step_mon, dict) else 0
    s_off_y = step_mon.get("top", 0) if isinstance(step_mon, dict) else 0
    return step_gray, s_off_x, s_off_y


def _is_scroll_step_matched(is_step_match: bool, step_scores: Dict[str, float]) -> bool:
    return bool(
        is_step_match
        or step_scores.get("ssim", 0.0) >= 0.72
        or step_scores.get("orb", 0.0) >= 0.12
        or (step_scores.get("sim", 0.0) >= 0.86 and step_scores.get("diff_ratio", 1.0) <= 0.15)
    )


def align_scroll_by_screen_match(
    target_dir: Path,
    raw_event_id: str,
    win_x: int,
    win_y: int,
    win_w: int,
    win_h: int,
    target_hwnd: int = 0,
    max_down_steps: int = 15,
    max_up_steps: int = 5,
    status_callback: Optional[Callable[[str, bool], None]] = None,
    check_cancel_callback: Optional[Callable[[], bool]] = None,
) -> Tuple[bool, Dict[str, float]]:
    # Why: 記録時pre画像の特徴・構造(SSIM/ORB)と一致するまでホイールスクロールを自律反復
    if not raw_event_id or win_w <= 0 or win_h <= 0:
        return False, {}

    pre_image_path = target_dir / "images" / f"{raw_event_id}_pre.png"
    if not pre_image_path.exists():
        return False, {}

    try:
        curr_pil, curr_mon = take_screenshot()
        curr_color = cv2.cvtColor(np.array(curr_pil), cv2.COLOR_RGB2BGR)
        curr_gray = cv2.cvtColor(curr_color, cv2.COLOR_BGR2GRAY)
        off_x = curr_mon.get("left", 0) if isinstance(curr_mon, dict) else 0
        off_y = curr_mon.get("top", 0) if isinstance(curr_mon, dict) else 0

        is_match, scores = is_screen_match(
            pre_image_path, curr_gray, win_x, win_y, win_w, win_h, off_x, off_y
        )
        if is_match:
            return True, scores

        cx = win_x + win_w // 2 if win_w > 100 else 960
        cy = win_y + win_h // 2 if win_h > 100 else 540

        from pynput.mouse import Controller as MouseController
        from core.executor.runner import MOUSEEVENTF_WHEEL, WHEEL_DELTA, _smooth_move

        _smooth_move(cx, cy, duration=0.06)
        time.sleep(0.04)

        mouse_ctrl = MouseController()
        best_score = scores.get("ssim", 0.0) + scores.get("orb", 0.0)
        best_scores = scores
        best_step = 0
        current_step = 0

        notify_execution_status(status_callback, "スクロール位置を自動照合中...", False)
        notches_per_step = 2

        # 1. 下方向へスクロールしながら特徴・構造の一致を探索
        for step in range(1, max_down_steps + 1):
            if check_cancel_callback and check_cancel_callback():
                return False, scores

            _send_wheel_scroll(-notches_per_step, WHEEL_DELTA, MOUSEEVENTF_WHEEL, mouse_ctrl)
            time.sleep(0.14)
            current_step += 1

            step_gray, s_off_x, s_off_y = _capture_gray_and_offset()
            is_step_match, step_scores = is_screen_match(
                pre_image_path, step_gray, win_x, win_y, win_w, win_h, s_off_x, s_off_y
            )

            current_composite = step_scores.get("ssim", 0.0) + step_scores.get("orb", 0.0)
            if current_composite > best_score:
                best_score = current_composite
                best_scores = step_scores
                best_step = current_step

            if _is_scroll_step_matched(is_step_match, step_scores):
                logger.info(f"Visual scroll matched at down-step {step}: {step_scores}")
                notify_execution_status(status_callback, "画面の一致を確認しました。", False)
                time.sleep(0.1)
                return True, step_scores

        # 2. 下方向未検出時は巻き戻しつつ上方向も探索
        steps_to_rewind = current_step + max_up_steps
        for _ in range(steps_to_rewind):
            if check_cancel_callback and check_cancel_callback():
                return False, scores

            _send_wheel_scroll(notches_per_step, WHEEL_DELTA, MOUSEEVENTF_WHEEL, mouse_ctrl)
            time.sleep(0.14)
            current_step -= 1

            step_gray, s_off_x, s_off_y = _capture_gray_and_offset()
            is_step_match, step_scores = is_screen_match(
                pre_image_path, step_gray, win_x, win_y, win_w, win_h, s_off_x, s_off_y
            )

            current_composite = step_scores.get("ssim", 0.0) + step_scores.get("orb", 0.0)
            if current_composite > best_score:
                best_score = current_composite
                best_scores = step_scores
                best_step = current_step

            if _is_scroll_step_matched(is_step_match, step_scores):
                logger.info(f"Visual scroll matched at rewind-step {current_step}: {step_scores}")
                notify_execution_status(status_callback, "画面の一致を確認しました。", False)
                time.sleep(0.1)
                return True, step_scores

        # 3. 最適スコア位置への復帰
        if best_step != current_step and best_score >= 0.55:
            delta_steps = best_step - current_step
            notch_step = -notches_per_step if delta_steps > 0 else notches_per_step
            for _ in range(abs(delta_steps)):
                _send_wheel_scroll(notch_step, WHEEL_DELTA, MOUSEEVENTF_WHEEL, mouse_ctrl)
                time.sleep(0.05)
            time.sleep(0.1)
            logger.info(f"Visual scroll settled at best step {best_step} (score: {best_score:.2f})")
            return (best_scores.get("ssim", 0.0) >= 0.65 or best_scores.get("orb", 0.0) >= 0.08), best_scores

        return False, best_scores
    except Exception as e:
        logger.warning(f"Error during align_scroll_by_screen_match: {e}")
        return False, {}
