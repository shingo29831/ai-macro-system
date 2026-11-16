"""Module: @role: UIダイアログ向けに現在開いているユーザーウィンドウ一覧とGDIサムネイル画像を取得する。"""

import ctypes
import logging
import platform
from typing import Any, Dict, List

from core.shared.window_utils import IGNORED_SYSTEM_WINDOW_TITLES, SYSTEM_WINDOW_KEYWORDS

logger = logging.getLogger(__name__)


def get_open_windows_info() -> List[Dict[str, Any]]:
    """現在開いている可視ウィンドウのHWND・タイトル・矩形・サムネイル画像一覧を返す。"""
    if platform.system() != "Windows":
        return []

    import win32gui
    import win32ui
    from PIL import Image

    windows_info: List[Dict[str, Any]] = []

    dwmwa_cloaked = 14
    gwl_exstyle = -20
    ws_ex_toolwindow = 0x00000080
    ws_ex_appwindow = 0x00040000
    ws_ex_transparent = 0x00000020
    ws_ex_layered = 0x00080000
    gw_owner = 4
    ga_rootowner = 3

    def is_user_visible_window(hwnd: int) -> bool:
        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            return False
        if win32gui.GetWindowTextLength(hwnd) == 0:
            return False

        cloaked = ctypes.c_int(0)
        try:
            hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, dwmwa_cloaked, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            )
            if hr == 0 and cloaked.value != 0:
                return False
        except Exception as e:
            logger.debug(f"DwmGetWindowAttribute failed for hwnd={hwnd}: {e}")

        try:
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, gwl_exstyle)
        except Exception as e:
            logger.debug(f"GetWindowLongW failed for hwnd={hwnd}: {e}")
            ex_style = 0

        if (ex_style & ws_ex_toolwindow) and not (ex_style & ws_ex_appwindow):
            return False
        if (ex_style & ws_ex_transparent) and (ex_style & ws_ex_layered):
            return False

        try:
            owner = ctypes.windll.user32.GetWindow(hwnd, gw_owner)
            if owner != 0 and not (ex_style & ws_ex_appwindow):
                return False
        except Exception as e:
            logger.debug(f"GetWindow(GW_OWNER) failed for hwnd={hwnd}: {e}")

        try:
            root_owner = ctypes.windll.user32.GetAncestor(hwnd, ga_rootowner)
            if root_owner != 0 and root_owner != hwnd:
                if ctypes.windll.user32.GetLastActivePopup(root_owner) != hwnd:
                    return False
        except Exception as e:
            logger.debug(f"GetAncestor/GetLastActivePopup failed for hwnd={hwnd}: {e}")

        return True

    def enum_windows_proc(hwnd, _lparam):
        if is_user_visible_window(hwnd):
            title = win32gui.GetWindowText(hwnd)
            title_lower = title.lower()
            system_ignored = IGNORED_SYSTEM_WINDOW_TITLES + SYSTEM_WINDOW_KEYWORDS + [
                "マクロ生成中", "aiマクロ生成中", "ウィンドウの紐付け"
            ]
            if not any(ignored.lower() in title_lower for ignored in system_ignored):
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w > 0 and h > 0:
                    windows_info.append({
                        "hwnd": hwnd,
                        "title": title,
                        "rect": rect,
                    })
        return True

    win32gui.EnumWindows(enum_windows_proc, 0)

    for info in windows_info:
        hwnd = info["hwnd"]
        hwnd_dc = None
        mfc_dc = None
        save_dc = None
        save_bitmap = None
        try:
            left, top, right, bottom = info["rect"]
            width = right - left
            height = bottom - top

            hwnd_dc = win32gui.GetWindowDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            save_bitmap = win32ui.CreateBitmap()
            save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(save_bitmap)

            ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)

            bmpinfo = save_bitmap.GetInfo()
            bmpstr = save_bitmap.GetBitmapBits(True)

            img = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr,
                "raw",
                "BGRX",
                0,
                1,
            )
            img.thumbnail((200, 200))
            info["thumbnail"] = img
        except Exception as e:
            logger.debug(f"Thumbnail capture failed for hwnd={hwnd}: {e}")
            info["thumbnail"] = None
        finally:
            if save_bitmap is not None:
                try:
                    win32gui.DeleteObject(save_bitmap.GetHandle())
                except Exception as e:
                    logger.debug(f"DeleteObject failed: {e}")
            if save_dc is not None:
                try:
                    save_dc.DeleteDC()
                except Exception as e:
                    logger.debug(f"save_dc.DeleteDC failed: {e}")
            if mfc_dc is not None:
                try:
                    mfc_dc.DeleteDC()
                except Exception as e:
                    logger.debug(f"mfc_dc.DeleteDC failed: {e}")
            if hwnd_dc is not None:
                try:
                    win32gui.ReleaseDC(hwnd, hwnd_dc)
                except Exception as e:
                    logger.debug(f"ReleaseDC failed: {e}")

    return windows_info
