# Role: OSプロセスのDPIスケーリング（DPI Awareness）およびIME入力モードの状態制御を担当する。

import platform
import ctypes
import time
import logging
import struct
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# =========================
# 設定定数
# =========================

WM_IME_CONTROL = 0x0283
IMC_SETOPENSTATUS = 0x0006
TARGET_SYSTEM_CURSORS = [32512, 32513, 32649]  # OCR_NORMAL, OCR_IBEAM, OCR_HAND
SPI_SETCURSORS = 0x0057

_cursor_changed = False


def _build_cur_file(file_path: Path, mode: str):
    # Why: Windows標準.curバイナリ(ICONDIR+DIB)を直接生成しLoadCursorFromFileWに完全適合
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    arrow_poly = [(0, 0), (0, 18), (5, 14), (9, 22), (12, 21), (8, 13), (14, 13)]
    draw.polygon(arrow_poly, fill=(255, 255, 255, 255), outline=(10, 10, 10, 255))

    if mode == "record":
        draw.ellipse([(14, 14), (30, 30)], fill=(220, 38, 38, 255), outline=(255, 255, 255, 255), width=2)
        draw.ellipse([(19, 19), (25, 25)], fill=(255, 255, 255, 255))
    elif mode == "run":
        draw.ellipse([(14, 14), (30, 30)], fill=(37, 99, 235, 255), outline=(255, 255, 255, 255), width=2)
        draw.polygon([(20, 18), (20, 26), (27, 22)], fill=(255, 255, 255, 255))

    width, height = 32, 32
    hotspot_x, hotspot_y = 0, 0

    pixels = img.load()
    xor_data = bytearray()
    and_mask = bytearray()

    for y in reversed(range(height)):
        and_row = 0
        for x in range(width):
            r, g, b, a = pixels[x, y]
            xor_data.extend([b, g, r, a])
            bit = 1 if a < 128 else 0
            and_row = (and_row << 1) | bit
        and_mask.extend(and_row.to_bytes(4, byteorder="big"))

    header = struct.pack(
        "<IIIHHIIIIII",
        40, width, height * 2, 1, 32, 0, len(xor_data) + len(and_mask), 0, 0, 0, 0
    )
    image_data = header + bytes(xor_data) + bytes(and_mask)
    icondir = struct.pack("<HHH", 0, 2, 1)
    direntry = struct.pack(
        "<BBBBHHII",
        width, height, 0, 0, hotspot_x, hotspot_y, len(image_data), 22
    )

    with open(file_path, "wb") as f:
        f.write(icondir + direntry + image_data)


def _get_app_cur_path(mode: str) -> Path:
    temp_dir = Path(tempfile.gettempdir())
    cur_path = temp_dir / f"aimacro_{mode}.cur"
    if not cur_path.exists():
        _build_cur_file(cur_path, mode)
    return cur_path


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
        cur_path = _get_app_cur_path(mode)

        # Why: SetSystemCursorは渡されたハンドルを内部破棄するためターゲット毎に個別ロード
        for cursor_id in TARGET_SYSTEM_CURSORS:
            h_cur = user32.LoadCursorFromFileW(str(cur_path))
            if h_cur:
                if user32.SetSystemCursor(h_cur, cursor_id):
                    _cursor_changed = True
    except Exception as e:
        logger.warning(f"Failed to set custom system cursor ({mode}): {e}")


# Why: プロセス異常終了時もOSシステムカーソルを確実に標準へ復元
atexit.register(restore_system_cursor)
