# Role: OSプロセスのDPIスケーリング（DPI Awareness）およびIME入力モードの状態制御を担当する。

import platform
import ctypes
import time
import logging
import io
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# =========================
# 設定定数
# =========================

import atexit

WM_IME_CONTROL = 0x0283
IMC_SETOPENSTATUS = 0x0006
OCR_NORMAL = 32512
SPI_SETCURSORS = 0x0057

_cursor_changed = False


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", ctypes.c_bool),
        ("xHotspot", ctypes.c_uint32),
        ("yHotspot", ctypes.c_uint32),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]


def _generate_app_cursor_image(mode: str) -> tuple[Image.Image, tuple[int, int]]:
    # Why: 外部ファイル依存ゼロで高視認性の独自矢印+状態バッジカーソルを動的生成
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    arrow_poly = [(0, 0), (0, 17), (4, 13), (8, 20), (11, 19), (7, 12), (13, 12)]
    draw.polygon(arrow_poly, fill=(255, 255, 255, 255), outline=(20, 20, 20, 255))

    if mode == "record":
        # 録画バッジ: 赤いREC発光リングと中央ドット
        draw.ellipse([(13, 13), (29, 29)], fill=(220, 38, 38, 240), outline=(255, 255, 255, 255), width=1)
        draw.ellipse([(18, 18), (24, 24)], fill=(255, 255, 255, 255))
    elif mode == "run":
        # 実行バッジ: 青いAI実行リングと再生三角シンボル
        draw.ellipse([(13, 13), (29, 29)], fill=(37, 99, 235, 240), outline=(255, 255, 255, 255), width=1)
        draw.polygon([(19, 17), (19, 25), (26, 21)], fill=(255, 255, 255, 255))

    return img, (0, 0)


def _create_custom_cursor_from_image(img: Image.Image, hotspot: tuple[int, int]) -> int | None:
    # Why: PNGバイト列からGDIビットマップを経由してホットスポット付きHCURSORを生成
    if platform.system() != "Windows":
        return None
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        h_icon = user32.CreateIconFromResourceEx(
            png_bytes, len(png_bytes), True, 0x00030000, img.width, img.height, 0
        )
        if not h_icon:
            return None

        icon_info = ICONINFO()
        if not user32.GetIconInfo(h_icon, ctypes.byref(icon_info)):
            user32.DestroyIcon(h_icon)
            return None

        icon_info.fIcon = False
        icon_info.xHotspot = hotspot[0]
        icon_info.yHotspot = hotspot[1]

        h_cursor = user32.CreateIconIndirect(ctypes.byref(icon_info))

        if icon_info.hbmColor:
            gdi32.DeleteObject(icon_info.hbmColor)
        if icon_info.hbmMask:
            gdi32.DeleteObject(icon_info.hbmMask)
        user32.DestroyIcon(h_icon)

        return h_cursor
    except Exception as e:
        logger.warning(f"Failed to create custom HCURSOR: {e}")
        return None


def set_dpi_awareness():
    """WindowsプロセスのDPI Awarenessを高DPI対応モードに設定する"""
    if platform.system() == "Windows":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def is_running_as_admin() -> bool:
    """現在のプロセスがWindows管理者権限で実行されているかを判定する"""
    if platform.system() != "Windows":
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def set_ime_state(text: str = "", target_state: bool | None = None):
    """アクティブウィンドウのIME状態（全角/半角）を制御する"""
    if platform.system() != "Windows":
        return
    try:
        # Why: 日本語を含む場合はIMEをON、ASCIIのみは強制OFFにして半角英数入力の破壊を防止
        if target_state is None:
            has_japanese = any(ord(c) > 0x7F for c in text)
            open_status = 1 if has_japanese else 0
        else:
            open_status = 1 if target_state else 0

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return
        default_ime_wnd = ctypes.windll.imm32.ImmGetDefaultIMEWnd(hwnd)
        if default_ime_wnd:
            ctypes.windll.user32.SendMessageW(default_ime_wnd, WM_IME_CONTROL, IMC_SETOPENSTATUS, open_status)
            time.sleep(0.05)
    except Exception as e:
        logger.warning(f"Failed to set IME state: {e}")


def restore_system_cursor():
    """OSシステムカーソルを標準設定へ復元する"""
    global _cursor_changed
    if platform.system() != "Windows" or not _cursor_changed:
        return
    try:
        ctypes.windll.user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
        _cursor_changed = False
    except Exception as e:
        logger.warning(f"Failed to restore system cursor: {e}")


def set_system_cursor(mode: str = "default"):
    """
    システムの標準カーソルをアプリオリジナルの録画・実行カーソルへ動的に変更する。
    mode: 'record' (オリジナル録画バッジ), 'run' (オリジナルAI実行バッジ), 'default' (復元)
    """
    global _cursor_changed
    if platform.system() != "Windows":
        return
    if mode == "default":
        restore_system_cursor()
        return

    try:
        user32 = ctypes.windll.user32
        img, hotspot = _generate_app_cursor_image(mode)
        h_cursor = _create_custom_cursor_from_image(img, hotspot)

        if not h_cursor:
            # フォールバック: 組み込みカーソル
            cursor_map = {"record": 32515, "run": 32650}
            cid = cursor_map.get(mode)
            if cid:
                h_sys = user32.LoadCursorW(0, cid)
                if h_sys:
                    h_cursor = user32.CopyIcon(h_sys)

        if h_cursor:
            # Why: SetSystemCursorがハンドルの破棄を担うためリークなく安全に差し替え
            if user32.SetSystemCursor(h_cursor, OCR_NORMAL):
                _cursor_changed = True
    except Exception as e:
        logger.warning(f"Failed to set custom system cursor ({mode}): {e}")


# Why: プロセス異常終了時もOSシステムカーソルを確実に標準へ復元
atexit.register(restore_system_cursor)
