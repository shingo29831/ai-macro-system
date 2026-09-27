# Role: 全画面スクリーンショットの撮影、前回画面との差分率・変動率計算、および画像保存を担当する。

from pathlib import Path
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


def save_event_pre_image(event_no: str) -> str:
    """現在の画面をキャプチャし、evt_{event_no}_pre.png として保存する"""
    images_dir = get_images_dir()
    img, _ = take_screenshot()
    path = images_dir / f"evt_{event_no}_pre.png"
    img.save(path)
    return to_macro_relative_path(path)


def save_pre_image_from_pil(event_no: str, img: Image.Image) -> str:
    """渡されたPIL画像を evt_{event_no}_pre.png として保存する"""
    images_dir = get_images_dir()
    path = images_dir / f"evt_{event_no}_pre.png"
    img.save(path)
    return to_macro_relative_path(path)
def save_diff_crop(event_no: str, pre_img: Image.Image, post_img: Image.Image) -> dict | None:
    """
    キー入力前後の画像(pre_img, post_img)から変化した領域（文字が増減した箇所）を抽出し、
    入力フィールドの領域として切り抜いて保存する。
    """
    images_dir = get_images_dir()
    
    pre_cv = cv2.cvtColor(np.array(pre_img.convert("RGB")), cv2.COLOR_RGB2BGR)
    post_cv = cv2.cvtColor(np.array(post_img.convert("RGB")), cv2.COLOR_RGB2BGR)
    
    pre_gray = cv2.cvtColor(pre_cv, cv2.COLOR_BGR2GRAY)
    post_gray = cv2.cvtColor(post_cv, cv2.COLOR_BGR2GRAY)
    
    # 差分を計算
    diff = cv2.absdiff(pre_gray, post_gray)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    
    # ノイズ除去と結合（文字の隙間を埋めて一つのテキストボックス矩形にする）
    kernel = np.ones((9, 9), np.uint8)
    thresh = cv2.dilate(thresh, kernel, iterations=3)
    
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if not contours:
        return None
        
    x_min, y_min = pre_img.width, pre_img.height
    x_max, y_max = 0, 0
    
    valid_contours = 0
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w * h < 25: # 小さすぎる変化はノイズとして無視
            continue
        valid_contours += 1
        x_min = min(x_min, x)
        y_min = min(y_min, y)
        x_max = max(x_max, x + w)
        y_max = max(y_max, y + h)
        
    if valid_contours == 0:
        return None
        
    # 周囲に余白をつける
    padding = 20
    x_min = max(0, x_min - padding)
    y_min = max(0, y_min - padding)
    x_max = min(pre_img.width, x_max + padding)
    y_max = min(pre_img.height, y_max + padding)
    
    # 全体の50%以上変化している場合は画面全体遷移とみなし、UI部品としての切り抜きを行わない
    area = (x_max - x_min) * (y_max - y_min)
    if area > (pre_img.width * pre_img.height * 0.5):
        return None
        
    crop_img = post_img.crop((x_min, y_min, x_max, y_max))
    ui_path = images_dir / f"evt_{event_no}_crop.png"
    crop_img.save(ui_path)
    
    return {
        "ui_image_ref": to_macro_relative_path(ui_path),
        "detection_method": "diff_between_pre_and_post"
    }


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


# =========================
# 表示内容トリミング
# =========================

def trim_crop_to_visible_content(
    pil_img: Image.Image,
    padding: int = VISIBLE_TRIM_PADDING,
    threshold: int = VISIBLE_TRIM_THRESHOLD,
) -> tuple[Image.Image, dict]:
    img = np.array(pil_img.convert("RGB"))

    if img.size == 0:
        return pil_img, {
            "trimmed": False,
            "reason": "empty_image",
            "x": 0,
            "y": 0,
            "w": pil_img.width,
            "h": pil_img.height,
        }

    h, w, _ = img.shape

    if w <= 3 or h <= 3:
        return pil_img, {
            "trimmed": False,
            "reason": "too_small",
            "x": 0,
            "y": 0,
            "w": pil_img.width,
            "h": pil_img.height,
        }

    corner_size = max(2, min(10, w // 5, h // 5))

    corners = np.concatenate([
        img[0:corner_size, 0:corner_size].reshape(-1, 3),
        img[0:corner_size, w - corner_size:w].reshape(-1, 3),
        img[h - corner_size:h, 0:corner_size].reshape(-1, 3),
        img[h - corner_size:h, w - corner_size:w].reshape(-1, 3),
    ], axis=0)

    background_color = np.median(corners, axis=0)

    diff = np.linalg.norm(
        img.astype(np.int16) - background_color.astype(np.int16),
        axis=2
    )

    mask = (diff >= threshold).astype(np.uint8) * 255

    kernel_open = np.ones((2, 2), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_open, iterations=1)

    kernel_dilate = np.ones((3, 3), np.uint8)
    mask = cv2.dilate(mask, kernel_dilate, iterations=1)

    contours, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    valid_rects = []

    for contour in contours:
        x, y, rw, rh = cv2.boundingRect(contour)
        area = rw * rh

        if area < 8:
            continue

        valid_rects.append((x, y, rw, rh))

    if not valid_rects:
        return pil_img, {
            "trimmed": False,
            "reason": "no_visible_content",
            "x": 0,
            "y": 0,
            "w": pil_img.width,
            "h": pil_img.height,
        }

    left = min(r[0] for r in valid_rects)
    top = min(r[1] for r in valid_rects)
    right = max(r[0] + r[2] for r in valid_rects)
    bottom = max(r[1] + r[3] for r in valid_rects)

    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(w, right + padding)
    bottom = min(h, bottom + padding)

    if right <= left or bottom <= top:
        return pil_img, {
            "trimmed": False,
            "reason": "invalid_rect",
            "x": 0,
            "y": 0,
            "w": pil_img.width,
            "h": pil_img.height,
        }

    trimmed = pil_img.crop((left, top, right, bottom))

    return trimmed, {
        "trimmed": True,
        "reason": "success",
        "x": int(left),
        "y": int(top),
        "w": int(right - left),
        "h": int(bottom - top),
    }


def is_trimmed_image_valid(pil_img: Image.Image) -> bool:
    if pil_img.width < MIN_TRIMMED_WIDTH:
        return False
    if pil_img.height < MIN_TRIMMED_HEIGHT:
        return False
    return True


# =========================
# UIA矩形切り抜き
# =========================

def save_ui_crop_by_rect(
    event_no: str,
    rect: dict,
    full_img: Image.Image,
    monitor: dict,
) -> dict:
    images_dir = get_images_dir()

    screen_left = int(monitor["left"])
    screen_top = int(monitor["top"])

    left = int(rect["left"] - screen_left)
    top = int(rect["top"] - screen_top)
    right = int(rect["right"] - screen_left)
    bottom = int(rect["bottom"] - screen_top)

    left = max(0, min(full_img.width, left))
    top = max(0, min(full_img.height, top))
    right = max(0, min(full_img.width, right))
    bottom = max(0, min(full_img.height, bottom))

    if right <= left or bottom <= top:
        return {
            "ui_image_ref": None,
            "detection_method": "uia_invalid_rect",
        }

    ui_img = full_img.crop((left, top, right, bottom))
    detection_method = "uia_element_rect_from_pre"

    if ENABLE_VISIBLE_CONTENT_TRIM:
        trimmed_img, trim_info = trim_crop_to_visible_content(
            ui_img,
            padding=VISIBLE_TRIM_PADDING,
            threshold=VISIBLE_TRIM_THRESHOLD
        )

        if trim_info.get("trimmed") and is_trimmed_image_valid(trimmed_img):
            ui_img = trimmed_img
            detection_method = "uia_element_rect_trimmed_from_pre"
        else:
            if not is_trimmed_image_valid(ui_img):
                return {
                    "ui_image_ref": None,
                    "detection_method": "uia_trim_failed",
                }

    ui_path = images_dir / f"evt_{event_no}_crop.png"
    ui_img.save(ui_path)

    return {
        "ui_image_ref": to_macro_relative_path(ui_path),
        "detection_method": detection_method,
    }


# =========================
# OpenCV UI切り抜き
# =========================

def crop_around_click(
    full_img: Image.Image,
    monitor: dict,
    click_x: int,
    click_y: int
):
    screen_left = int(monitor["left"])
    screen_top = int(monitor["top"])

    img_x = int(click_x - screen_left)
    img_y = int(click_y - screen_top)

    img_x = max(0, min(full_img.width - 1, img_x))
    img_y = max(0, min(full_img.height - 1, img_y))

    left = img_x - AROUND_WIDTH // 2
    top = img_y - AROUND_HEIGHT // 2
    right = img_x + AROUND_WIDTH // 2
    bottom = img_y + AROUND_HEIGHT // 2

    left = max(0, left)
    top = max(0, top)
    right = min(full_img.width, right)
    bottom = min(full_img.height, bottom)

    if right <= left:
        right = min(full_img.width, left + 1)

    if bottom <= top:
        bottom = min(full_img.height, top + 1)

    around_img = full_img.crop((left, top, right, bottom))

    local_click_x = img_x - left
    local_click_y = img_y - top

    local_click_x = max(0, min(around_img.width - 1, local_click_x))
    local_click_y = max(0, min(around_img.height - 1, local_click_y))

    crop_info = {
        "around_left": int(left),
        "around_top": int(top),
        "around_right": int(right),
        "around_bottom": int(bottom),
        "local_click_x": int(local_click_x),
        "local_click_y": int(local_click_y),
    }

    return around_img, crop_info


def detect_ui_contours(pil_img: Image.Image) -> list[dict]:
    img = np.array(pil_img)
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)

    edges = cv2.Canny(blurred, 50, 150)

    kernel = np.ones((3, 3), np.uint8)
    dilated = cv2.dilate(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(
        dilated,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    candidates = []

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)

        if w < MIN_UI_WIDTH or h < MIN_UI_HEIGHT:
            continue

        if w > MAX_UI_WIDTH or h > MAX_UI_HEIGHT:
            continue

        candidates.append({
            "x": int(x),
            "y": int(y),
            "w": int(w),
            "h": int(h),
            "area": int(w * h),
        })

    return candidates


def contains_point(rect: dict, px: int, py: int) -> bool:
    x = rect["x"]
    y = rect["y"]
    w = rect["w"]
    h = rect["h"]
    return x <= px <= x + w and y <= py <= y + h


def select_clicked_ui(candidates: list[dict], click_x: int, click_y: int) -> dict | None:
    containing = [
        rect for rect in candidates
        if contains_point(rect, click_x, click_y)
    ]
    if not containing:
        return None
    containing.sort(key=lambda r: r["area"])
    return containing[0]


def crop_ui_element(pil_img: Image.Image, rect: dict):
    left = max(0, rect["x"] - PADDING)
    top = max(0, rect["y"] - PADDING)
    right = min(pil_img.width, rect["x"] + rect["w"] + PADDING)
    bottom = min(pil_img.height, rect["y"] + rect["h"] + PADDING)

    cropped = pil_img.crop((left, top, right, bottom))

    padded_rect = {
        "x": int(left),
        "y": int(top),
        "w": int(right - left),
        "h": int(bottom - top),
    }

    return cropped, padded_rect


def save_ui_crop(
    event_no: str,
    click_x: int,
    click_y: int,
    full_img: Image.Image,
    monitor: dict,
) -> dict:
    images_dir = get_images_dir()

    around_img, crop_info = crop_around_click(
        full_img,
        monitor,
        click_x,
        click_y
    )

    local_click_x = crop_info["local_click_x"]
    local_click_y = crop_info["local_click_y"]

    candidates = detect_ui_contours(around_img)

    selected_rect = select_clicked_ui(
        candidates,
        local_click_x,
        local_click_y
    )

    if selected_rect is None:
        return {
            "ui_image_ref": None,
            "detection_method": "opencv_no_ui_rect_from_pre",
        }

    ui_img, _ = crop_ui_element(around_img, selected_rect)

    if ENABLE_VISIBLE_CONTENT_TRIM:
        trimmed_img, trim_info = trim_crop_to_visible_content(
            ui_img,
            padding=VISIBLE_TRIM_PADDING,
            threshold=VISIBLE_TRIM_THRESHOLD
        )

        if trim_info.get("trimmed") and is_trimmed_image_valid(trimmed_img):
            ui_img = trimmed_img

    if not is_trimmed_image_valid(ui_img):
        return {
            "ui_image_ref": None,
            "detection_method": "opencv_trim_failed",
        }

    ui_path = images_dir / f"evt_{event_no}_crop.png"
    ui_img.save(ui_path)

    return {
        "ui_image_ref": to_macro_relative_path(ui_path),
        "detection_method": "opencv_contour_rect_trimmed_from_pre",
    }
