# @role: スクリーンショット取得・UI切り抜き・画像保存を担当する。
#
# 保存先:
#   ../../../macros/wf_連番/temp
#   ../../../macros/wf_連番/images
#
# 画像名:
#   evt_001_pre.png
#   evt_001_crop.png

from pathlib import Path

import mss
from PIL import Image
import cv2
import numpy as np


# =========================
# 設定
# =========================

AROUND_WIDTH = 240
AROUND_HEIGHT = 240

MIN_UI_WIDTH = 20
MIN_UI_HEIGHT = 15

MAX_UI_WIDTH = 200
MAX_UI_HEIGHT = 200

PADDING = 8

FALLBACK_WIDTH = 200
FALLBACK_HEIGHT = 180
ENABLE_FALLBACK_TRIM = True


# =========================
# 状態
# =========================

_current_macro_dir: Path | None = None
_temp_dir: Path | None = None
_images_dir: Path | None = None


# =========================
# ディレクトリ管理
# =========================

def get_macros_root() -> Path:
    return (Path(__file__).resolve().parent / "../../../macros").resolve()


def make_directory() -> dict:
    """
    ../../../macros/wf_1, wf_2, wf_3... のように連番で一意フォルダを作成する。
    """
    global _current_macro_dir
    global _temp_dir
    global _images_dir

    macros_root = get_macros_root()
    macros_root.mkdir(parents=True, exist_ok=True)

    index = 1

    while True:
        macro_dir = macros_root / f"wf_{index}"
        if not macro_dir.exists():
            break
        index += 1

    temp_dir = macro_dir / "temp"
    images_dir = macro_dir / "images"

    temp_dir.mkdir(parents=True, exist_ok=False)
    images_dir.mkdir(parents=True, exist_ok=False)

    _current_macro_dir = macro_dir
    _temp_dir = temp_dir
    _images_dir = images_dir

    return {
        "macro_name": macro_dir.name,
        "macro_dir": str(macro_dir),
        "temp_dir": str(temp_dir),
        "images_dir": str(images_dir),
    }


def get_temp_dir() -> Path:
    if _temp_dir is None:
        raise RuntimeError("temp_dir が未作成です。start_recording() を先に呼んでください。")
    return _temp_dir


def get_images_dir() -> Path:
    if _images_dir is None:
        raise RuntimeError("images_dir が未作成です。start_recording() を先に呼んでください。")
    return _images_dir


# =========================
# 全画面スクリーンショット
# =========================

def take_screenshot() -> tuple[Image.Image, dict]:
    with mss.MSS() as sct:
        monitor = sct.monitors[0]
        screenshot = sct.grab(monitor)

        img = Image.frombytes(
            "RGB",
            screenshot.size,
            screenshot.rgb
        )

        return img, monitor


def save_event_pre_image(event_no: str) -> str:
    """
    アクション直前、または録画終了タイミングの全画面画像を保存する。
    例:
      evt_001_pre.png
    """
    images_dir = get_images_dir()

    img, _ = take_screenshot()

    path = images_dir / f"evt_{event_no}_pre.png"
    img.save(path)

    return str(path)


def save_pre_image_from_pil(event_no: str, img: Image.Image) -> str:
    """
    mouse down 時点など、すでに取得済みの画像を evt_XXX_pre.png として保存する。
    """
    images_dir = get_images_dir()

    path = images_dir / f"evt_{event_no}_pre.png"
    img.save(path)

    return str(path)


# =========================
# UI切り抜き
# =========================

def crop_around_click(full_img: Image.Image, monitor: dict, click_x: int, click_y: int):
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
        "screen_left": int(screen_left),
        "screen_top": int(screen_top),
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


def trim_ui_from_fallback_crop(pil_img: Image.Image, padding: int = 8):
    img = np.array(pil_img)

    if img.size == 0:
        return pil_img, {
            "x": 0,
            "y": 0,
            "w": pil_img.width,
            "h": pil_img.height,
            "trimmed": False,
            "reason": "empty_image",
        }

    h, w, _ = img.shape

    if h < 5 or w < 5:
        return pil_img, {
            "x": 0,
            "y": 0,
            "w": pil_img.width,
            "h": pil_img.height,
            "trimmed": False,
            "reason": "too_small_image",
        }

    corner_size = min(10, h // 3, w // 3)

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

    mask = (diff > 18).astype(np.uint8) * 255

    kernel_open = np.ones((2, 2), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_open, iterations=1)

    kernel_dilate = np.ones((5, 5), np.uint8)
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

        if area < 20:
            continue

        valid_rects.append((x, y, rw, rh))

    if not valid_rects:
        return pil_img, {
            "x": 0,
            "y": 0,
            "w": pil_img.width,
            "h": pil_img.height,
            "trimmed": False,
            "reason": "no_valid_rects",
        }

    left = min(r[0] for r in valid_rects)
    top = min(r[1] for r in valid_rects)
    right = max(r[0] + r[2] for r in valid_rects)
    bottom = max(r[1] + r[3] for r in valid_rects)

    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(pil_img.width, right + padding)
    bottom = min(pil_img.height, bottom + padding)

    if right <= left or bottom <= top:
        return pil_img, {
            "x": 0,
            "y": 0,
            "w": pil_img.width,
            "h": pil_img.height,
            "trimmed": False,
            "reason": "invalid_trim_rect",
        }

    cropped = pil_img.crop((left, top, right, bottom))

    rect = {
        "x": int(left),
        "y": int(top),
        "w": int(right - left),
        "h": int(bottom - top),
        "trimmed": True,
        "reason": "success",
    }

    return cropped, rect


def crop_fallback_around_click(pil_img: Image.Image, click_x: int, click_y: int):
    left = max(0, int(click_x - FALLBACK_WIDTH // 2))
    top = max(0, int(click_y - FALLBACK_HEIGHT // 2))
    right = min(pil_img.width, int(click_x + FALLBACK_WIDTH // 2))
    bottom = min(pil_img.height, int(click_y + FALLBACK_HEIGHT // 2))

    if right <= left:
        right = min(pil_img.width, left + 1)

    if bottom <= top:
        bottom = min(pil_img.height, top + 1)

    fallback_img = pil_img.crop((left, top, right, bottom))

    fallback_original_rect = {
        "x": int(left),
        "y": int(top),
        "w": int(right - left),
        "h": int(bottom - top),
    }

    if not ENABLE_FALLBACK_TRIM:
        rect = {
            **fallback_original_rect,
            "trimmed": False,
            "fallback_original_rect": fallback_original_rect,
        }
        return fallback_img, rect

    trimmed_img, trim_rect = trim_ui_from_fallback_crop(
        fallback_img,
        padding=8
    )

    rect = {
        "x": int(left + trim_rect["x"]),
        "y": int(top + trim_rect["y"]),
        "w": int(trim_rect["w"]),
        "h": int(trim_rect["h"]),
        "trimmed": bool(trim_rect.get("trimmed", False)),
        "trim_reason": trim_rect.get("reason", ""),
        "fallback_original_rect": fallback_original_rect,
        "trim_rect_in_fallback": trim_rect,
    }

    return trimmed_img, rect


def save_ui_crop(event_no: str, click_x: int, click_y: int) -> dict:
    """
    クリック時のみUI切り抜き画像を保存する。
    例:
      evt_001_crop.png
    """
    images_dir = get_images_dir()

    full_img, monitor = take_screenshot()

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

    fallback_rect = None

    if selected_rect is not None:
        ui_img, ui_rect = crop_ui_element(around_img, selected_rect)
        detection_method = "contour"
    else:
        ui_img, fallback_rect = crop_fallback_around_click(
            around_img,
            local_click_x,
            local_click_y
        )
        ui_rect = fallback_rect
        detection_method = "fallback_center_crop_and_trim"

    ui_path = images_dir / f"evt_{event_no}_crop.png"
    ui_img.save(ui_path)

    screen_bbox = None
    if ui_rect is not None:
        screen_bbox = {
            "x": int(crop_info["around_left"] + ui_rect["x"]),
            "y": int(crop_info["around_top"] + ui_rect["y"]),
            "w": int(ui_rect["w"]),
            "h": int(ui_rect["h"]),
        }

    return {
        "ui_image_ref": str(ui_path),
        "detection_method": detection_method,
        "ui_rect_in_around": ui_rect,
        "screen_bbox": screen_bbox,
        "candidate_count": len(candidates),
        "selected_rect": selected_rect,
        "fallback_rect": fallback_rect,
    }