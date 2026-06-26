# @role: スクリーンショット取得・UI切り抜き・画像保存・スクリーンショット差分率計算を担当する。
#
# Crop:
#   - クリック後に再スクリーンショットを撮らない。
#   - os_hook.py から渡されたPre画像を元にUI切り抜きを作成する。
#   - UIAでExplorerの行全体などが取れた場合でも、見えている内容だけに再トリミングする。
#
# Path:
#   - JSONに書く画像パスは macrosフォルダ基準の相対パスで返す。
#   - 例: wf_1/images/evt_001_pre.png
#
# Diff:
#   - 前回スクリーンショットと今回スクリーンショットで、
#     一定以上変化したピクセルの割合を%表記で返す。

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

MAX_UI_WIDTH = 400
MAX_UI_HEIGHT = 300

PADDING = 8

DIFF_PIXEL_THRESHOLD = 25

# UIAで行全体が取れた時、見えているアイコン・文字部分だけに詰める設定
ENABLE_VISIBLE_CONTENT_TRIM = True
VISIBLE_TRIM_PADDING = 6
VISIBLE_TRIM_THRESHOLD = 18

# トリミング後がこれ未満なら失敗扱い
MIN_TRIMMED_WIDTH = 8
MIN_TRIMMED_HEIGHT = 8


# =========================
# 状態
# =========================

_current_macro_dir: Path | None = None
_temp_dir: Path | None = None
_images_dir: Path | None = None
_last_screenshot_gray: np.ndarray | None = None


# =========================
# ディレクトリ管理
# =========================

def get_macros_root() -> Path:
    """
    このファイル位置から ../../../macros を保存先にする。
    """
    return (Path(__file__).resolve().parent / "../../../macros").resolve()


def make_directory() -> dict:
    global _current_macro_dir
    global _temp_dir
    global _images_dir
    global _last_screenshot_gray

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
    _last_screenshot_gray = None

    return {
        "macro_name": macro_dir.name,
        "macro_dir": str(macro_dir),
        "temp_dir": str(temp_dir),
        "images_dir": str(images_dir),
    }


def get_current_macro_dir() -> Path:
    if _current_macro_dir is None:
        raise RuntimeError("macro_dir が未作成です。start_recording() を先に呼んでください。")
    return _current_macro_dir


def get_temp_dir() -> Path:
    if _temp_dir is None:
        raise RuntimeError("temp_dir が未作成です。start_recording() を先に呼んでください。")
    return _temp_dir


def get_images_dir() -> Path:
    if _images_dir is None:
        raise RuntimeError("images_dir が未作成です。start_recording() を先に呼んでください。")
    return _images_dir


def to_macro_relative_path(path: Path) -> str:
    """
    JSONに保存するため、macrosフォルダ基準の相対パスに変換する。

    例:
      C:/.../macros/wf_1/images/evt_001_pre.png
      -> wf_1/images/evt_001_pre.png
    """
    macros_root = get_macros_root()

    try:
        relative = path.resolve().relative_to(macros_root.resolve())
        return relative.as_posix()
    except Exception:
        return path.name


# =========================
# 変動率計算
# =========================

def calculate_diff_ratio(current_img: Image.Image) -> float:
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
    """
    全モニターを含むスクリーンショットを取得する。
    戻り値:
      - PIL.Image
      - monitor情報
    """
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
    現在の全画面画像を evt_XXX_pre.png として保存する。
    """
    images_dir = get_images_dir()

    img, _ = take_screenshot()

    path = images_dir / f"evt_{event_no}_pre.png"
    img.save(path)

    return to_macro_relative_path(path)


def save_pre_image_from_pil(event_no: str, img: Image.Image) -> str:
    """
    すでに取得済みの画像を evt_XXX_pre.png として保存する。
    """
    images_dir = get_images_dir()

    path = images_dir / f"evt_{event_no}_pre.png"
    img.save(path)

    return to_macro_relative_path(path)


# =========================
# 差分率計算
# =========================

def calculate_diff_percent(previous_img: Image.Image | None, current_img: Image.Image) -> str:
    """
    前回スクリーンショットと今回スクリーンショットで、
    一定以上変化したピクセルの割合を%表記で返す。

    仕様:
      - previous_img が None の場合は "100%"
      - 画像サイズが違う場合は current_img を previous_img のサイズに合わせる
      - RGB平均差分が DIFF_PIXEL_THRESHOLD 以上のピクセルを「変化あり」とする
    """
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
    """
    切り抜き画像の中から、背景色と違う部分だけを残して再トリミングする。

    目的:
      - Explorer詳細表示の「行全体」切り抜きから、
        フォルダアイコン + 文字だけを残す。
      - UIAで大きめに取れた要素を、実際に見えている内容に詰める。

    戻り値:
      - trimmed image
      - trim info
    """
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
    """
    トリミング後の画像が最低限使えるサイズか判定する。
    """
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
    """
    UIAなどで取得したUI要素矩形を、Pre画像から切り抜く。

    そのままだとExplorerの詳細表示で行全体が取れることがあるため、
    切り抜いた後に表示内容だけへ再トリミングする。
    """
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
            # トリミングできなかった場合でも、元のUIA切り抜きが有効なら保存する
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
    """
    OpenCV検出用にクリック周辺だけを一時的に切り抜く。
    この画像自体は保存しない。
    """
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
    """
    OpenCVでUIらしい矩形を検出する。
    """
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
    """
    クリック座標を含む矩形のうち、最も小さい矩形を選ぶ。
    """
    containing = [
        rect for rect in candidates
        if contains_point(rect, click_x, click_y)
    ]

    if not containing:
        return None

    containing.sort(key=lambda r: r["area"])
    return containing[0]


def crop_ui_element(pil_img: Image.Image, rect: dict):
    """
    検出されたUI矩形を少し余白付きで切り抜く。
    """
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
    """
    UIAで取れなかった場合のOpenCV切り抜き。
    クリック後に撮り直さず、Pre画像からUIらしい矩形だけを切り抜く。
    UIらしい矩形が取れない場合はCrop=None。
    """
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