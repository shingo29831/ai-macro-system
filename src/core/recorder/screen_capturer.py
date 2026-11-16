"""Module: @role: 録画セッション中の操作ウィンドウ切り抜き撮影、evt_pre画像保存、および連続変動率計算を担当する。"""

import logging
import cv2
import mss
import numpy as np
from PIL import Image

from core.recorder.macro_path_manager import get_images_dir, to_macro_relative_path
from core.shared.screen_capture import crop_image_to_window, take_screenshot

logger = logging.getLogger(__name__)

_last_screenshot_gray: np.ndarray | None = None


def calculate_diff_ratio(current_img: Image.Image) -> float:
    """連続する画面キャプチャ間のピクセル変動比率 (0.0〜1.0) を計算する"""
    global _last_screenshot_gray

    current_gray = cv2.cvtColor(np.array(current_img), cv2.COLOR_RGB2GRAY)

    if _last_screenshot_gray is None:
        _last_screenshot_gray = current_gray
        return 0.0

    if _last_screenshot_gray.shape != current_gray.shape:
        _last_screenshot_gray = current_gray
        return 1.0

    diff = cv2.absdiff(_last_screenshot_gray, current_gray)
    _, thresholded = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)

    changed_pixels = np.count_nonzero(thresholded)
    total_pixels = current_gray.size

    _last_screenshot_gray = current_gray
    return float(changed_pixels) / float(total_pixels)


def take_window_screenshot(window_info: dict | None = None) -> tuple[Image.Image, dict]:
    """主画面から操作中ウィンドウのみを切り抜いたスクリーンショットとモニタ情報を取得する"""
    full_img, monitor = take_screenshot()
    if window_info is None:
        try:
            from core.recorder import window_inspector

            window_info = window_inspector.get_foreground_window_info()
        except Exception as e:
            logger.debug(f"Failed to get foreground window for screenshot: {e}")
            window_info = None

    if window_info and "rect" in window_info:
        win_img = crop_image_to_window(full_img, monitor, window_info["rect"])
        return win_img, monitor

    return full_img, monitor


def save_event_pre_image(event_no: str, window_info: dict | None = None) -> str:
    """現在の操作中ウィンドウをキャプチャし、evt_{event_no}_pre.png として保存する"""
    images_dir = get_images_dir()
    img, _ = take_window_screenshot(window_info)
    path = images_dir / f"evt_{event_no}_pre.png"
    img.save(path)
    return to_macro_relative_path(path)


def save_pre_image_from_pil(
    event_no: str,
    img: Image.Image,
    window_info: dict | None = None,
    monitor: dict | None = None,
) -> str:
    """渡されたPIL画像から操作中ウィンドウ領域を evt_{event_no}_pre.png として保存する"""
    images_dir = get_images_dir()
    path = images_dir / f"evt_{event_no}_pre.png"

    if window_info is None:
        try:
            from core.recorder import window_inspector

            window_info = window_inspector.get_foreground_window_info()
        except Exception as e:
            logger.debug(f"Failed to get foreground window for pre-image crop: {e}")
            window_info = None

    target_img = img
    if window_info and "rect" in window_info:
        if monitor is None:
            with mss.MSS() as sct:
                monitor = sct.monitors[0]
        win_w = max(0, int(window_info["rect"].get("right", 0) - window_info["rect"].get("left", 0)))
        win_h = max(0, int(window_info["rect"].get("bottom", 0) - window_info["rect"].get("top", 0)))
        # Why: 渡された画像が全画面の場合のみウィンドウ領域で切り抜いて保存
        if abs(img.width - win_w) > 20 or abs(img.height - win_h) > 20:
            target_img = crop_image_to_window(img, monitor, window_info["rect"])

    target_img.save(path)
    return to_macro_relative_path(path)
