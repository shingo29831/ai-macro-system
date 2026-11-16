"""Module: @role: 全画面スクリーンショット撮影、ウィンドウ切り抜き、および画像差分率計算の共通機能を提供する。"""

import mss
import numpy as np
from PIL import Image

DIFF_PIXEL_THRESHOLD = 25


def take_screenshot() -> tuple[Image.Image, dict]:
    """mss を使用して主画面全体のスクリーンショットとモニタ情報を取得する。"""
    with mss.MSS() as sct:
        monitor = sct.monitors[0]
        screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
        return img, monitor


def crop_image_to_window(img: Image.Image, monitor: dict, window_rect: dict) -> Image.Image:
    """全画面画像から操作中ウィンドウ領域を切り抜く。"""
    screen_left = int(monitor.get("left", 0))
    screen_top = int(monitor.get("top", 0))

    left = int(window_rect.get("left", 0) - screen_left)
    top = int(window_rect.get("top", 0) - screen_top)
    right = int(window_rect.get("right", 0) - screen_left)
    bottom = int(window_rect.get("bottom", 0) - screen_top)

    left = max(0, min(img.width, left))
    top = max(0, min(img.height, top))
    right = max(0, min(img.width, right))
    bottom = max(0, min(img.height, bottom))

    if right - left < 10 or bottom - top < 10:
        return img

    return img.crop((left, top, right, bottom))


def calculate_diff_percent(previous_img: Image.Image | None, current_img: Image.Image) -> str:
    """2枚の画像間の平均ピクセル変化率をパーセント表記文字列（例: '12.34%'）で計算する。"""
    if previous_img is None:
        return "100%"

    previous = previous_img.convert("RGB")
    current = current_img.convert("RGB")

    if current.size != previous.size:
        current = current.resize(previous.size)

    previous_np = np.array(previous).astype(np.int16)
    current_np = np.array(current).astype(np.int16)

    diff = np.abs(previous_np - current_np)
    pixel_diff = np.mean(diff, axis=2)

    changed_pixels = np.sum(pixel_diff >= DIFF_PIXEL_THRESHOLD)
    total_pixels = pixel_diff.size

    percent = float(changed_pixels / total_pixels * 100.0)
    return f"{percent:.2f}%"
