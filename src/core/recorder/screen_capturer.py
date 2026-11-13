"""Module: @role: 全画面スクリーンショットの撮影、前回画面との差分率・変動率計算、および画像保存を担当する。"""

import mss
from PIL import Image
import cv2
import numpy as np

# 既存モジュールからの直接呼出し互換性維持のため、分割先から公開シンボルを re-export
from core.recorder.macro_path_manager import (
    get_macros_root,
    make_directory,
    get_current_macro_dir,
    get_temp_dir,
    get_images_dir,
    to_macro_relative_path,
)
from core.recorder.ui_cropper import (
    AROUND_WIDTH,
    AROUND_HEIGHT,
    MIN_UI_WIDTH,
    MIN_UI_HEIGHT,
    MAX_UI_WIDTH,
    MAX_UI_HEIGHT,
    PADDING,
    ENABLE_VISIBLE_CONTENT_TRIM,
    VISIBLE_TRIM_PADDING,
    VISIBLE_TRIM_THRESHOLD,
    MIN_TRIMMED_WIDTH,
    MIN_TRIMMED_HEIGHT,
    save_diff_crop,
    trim_crop_to_visible_content,
    is_trimmed_image_valid,
    save_ui_crop_by_rect,
    crop_around_click,
    detect_ui_contours,
    contains_point,
    select_clicked_ui,
    crop_ui_element,
    save_ui_crop,
)

# =========================
# 設定定数
# =========================

DIFF_PIXEL_THRESHOLD = 25

# =========================
# 状態管理
# =========================

_last_screenshot_gray: np.ndarray | None = None


# =========================
# 変動率計算
# =========================

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


# =========================
# 全画面スクリーンショット
# =========================

def take_screenshot() -> tuple[Image.Image, dict]:
    """mss を使用して主画面全体のスクリーンショットとモニタ情報を取得する"""
    with mss.MSS() as sct:
        monitor = sct.monitors[0]
        screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
        return img, monitor


def crop_image_to_window(img: Image.Image, monitor: dict, window_rect: dict) -> Image.Image:
    """全画面画像から操作中ウィンドウ領域を切り抜く"""
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


def take_window_screenshot(window_info: dict | None = None) -> tuple[Image.Image, dict]:
    """主画面から操作中ウィンドウのみを切り抜いたスクリーンショットとモニタ情報を取得する"""
    full_img, monitor = take_screenshot()
    if window_info is None:
        try:
            from core.recorder import window_inspector
            window_info = window_inspector.get_foreground_window_info()
        except Exception:
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
        except Exception:
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
# =========================
# 差分率計算
# =========================

def calculate_diff_percent(previous_img: Image.Image | None, current_img: Image.Image) -> str:
    """2枚の画像間の平均ピクセル変化率をパーセント表記文字列（例: '12.34%'）で計算する"""
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

