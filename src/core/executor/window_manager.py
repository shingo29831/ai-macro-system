# Role: マクロ実行時の対象ウィンドウの探索・自動起動・最前面化・サイズ復元および開いているウィンドウの一覧・サムネイル取得を担当する。

import platform
import ctypes
import re
import time
import subprocess
import logging

# 既存モジュールからの直接呼出し互換性維持のため、OS環境制御シンボルを re-export
from core.executor.os_env_controller import set_dpi_awareness, set_ime_state

logger = logging.getLogger(__name__)

# =========================
# 設定定数
# =========================

IGNORED_SYSTEM_WINDOW_TITLES = [
    "記録中",
    "停止中",
    "AI Macro System",
    "設定",
    "AIマクロ生成中...",
    "実行中",
    "実行中...",
]

SYSTEM_WINDOW_KEYWORDS = ["program manager", "ジャンプ リスト", "taskbar", "cortana", "検索", "geforce overlay", "nvidia share", "overlay"]

SUPPORTED_BROWSERS = ["firefox", "chrome", "edge", "brave", "opera"]

_browser_activated_once = False


def reset_browser_activation_flag():
    global _browser_activated_once
    _browser_activated_once = False

def get_system_window_rects():
    rects = []
    if platform.system() != "Windows":
        return rects
    
    from ctypes import wintypes
    
    user32 = ctypes.windll.user32
    
    def enum_windows_proc(hwnd, lParam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                
                if any(ignored in title for ignored in IGNORED_SYSTEM_WINDOW_TITLES):
                    rect = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        rects.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    user32.EnumWindows(EnumWindowsProc(enum_windows_proc), 0)
    return rects


def get_open_windows_info():
    if platform.system() != "Windows":
        return []
    
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image
    
    windows_info = []
    
    DWMWA_CLOAKED = 14
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_APPWINDOW = 0x00040000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_LAYERED = 0x00080000
    GW_OWNER = 4
    GA_ROOTOWNER = 3

    def is_user_visible_window(hwnd: int) -> bool:
        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            return False
        if win32gui.GetWindowTextLength(hwnd) == 0:
            return False

        # 不可視オーバーレイやサスペンド窓をDWM属性で排除
        cloaked = ctypes.c_int(0)
        try:
            hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            )
            if hr == 0 and cloaked.value != 0:
                return False
        except Exception:
            pass

        try:
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        except Exception:
            ex_style = 0

        # ツールウィンドウおよび透明オーバーレイを除外
        if (ex_style & WS_EX_TOOLWINDOW) and not (ex_style & WS_EX_APPWINDOW):
            return False
        if (ex_style & WS_EX_TRANSPARENT) and (ex_style & WS_EX_LAYERED):
            return False

        # 所有されている従属ウィンドウを除外
        try:
            owner = ctypes.windll.user32.GetWindow(hwnd, GW_OWNER)
            if owner != 0 and not (ex_style & WS_EX_APPWINDOW):
                return False
        except Exception:
            pass

        # ポップアップチェーンを検証しAlt+Tab対象外を除外
        try:
            root_owner = ctypes.windll.user32.GetAncestor(hwnd, GA_ROOTOWNER)
            if root_owner != 0 and root_owner != hwnd:
                if ctypes.windll.user32.GetLastActivePopup(root_owner) != hwnd:
                    return False
        except Exception:
            pass

        return True

    def enum_windows_proc(hwnd, lParam):
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
                        "rect": rect
                    })
        return True

    win32gui.EnumWindows(enum_windows_proc, 0)
    
    for info in windows_info:
        hwnd = info["hwnd"]
        try:
            left, top, right, bottom = info["rect"]
            width = right - left
            height = bottom - top
            
            hwndDC = win32gui.GetWindowDC(hwnd)
            mfcDC  = win32ui.CreateDCFromHandle(hwndDC)
            saveDC = mfcDC.CreateCompatibleDC()
            
            saveBitMap = win32ui.CreateBitmap()
            saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
            saveDC.SelectObject(saveBitMap)
            
            ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 3)
            
            bmpinfo = saveBitMap.GetInfo()
            bmpstr = saveBitMap.GetBitmapBits(True)
            
            img = Image.frombuffer(
                'RGB',
                (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
                bmpstr, 'raw', 'BGRX', 0, 1
            )
            
            img.thumbnail((200, 200))
            info["thumbnail"] = img
            
            win32gui.DeleteObject(saveBitMap.GetHandle())
            saveDC.DeleteDC()
            mfcDC.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwndDC)
        except Exception:
            info["thumbnail"] = None
            
    return windows_info

def _restore_window_position_and_monitor(hwnd: int, win_x: int, win_y: int, win_w: int, win_h: int, is_maximized: bool = None) -> None:
    if platform.system() != "Windows" or not hwnd:
        return

    import ctypes
    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

    target_rect = RECT(win_x, win_y, win_x + win_w, win_y + win_h)
    h_target_mon = user32.MonitorFromRect(ctypes.byref(target_rect), 2)
    target_mi = MONITORINFO()
    target_mi.cbSize = ctypes.sizeof(MONITORINFO)
    has_target_mon = bool(user32.GetMonitorInfoW(h_target_mon, ctypes.byref(target_mi)))

    h_curr_mon = user32.MonitorFromWindow(hwnd, 2)
    curr_mi = MONITORINFO()
    curr_mi.cbSize = ctypes.sizeof(MONITORINFO)
    has_curr_mon = bool(user32.GetMonitorInfoW(h_curr_mon, ctypes.byref(curr_mi)))

    should_maximize = is_maximized
    if should_maximize is None and has_target_mon:
        t_w = target_mi.rcMonitor.right - target_mi.rcMonitor.left
        t_h = target_mi.rcMonitor.bottom - target_mi.rcMonitor.top
        near_top_left = (abs(win_x - (target_mi.rcMonitor.left - 8)) <= 25 and abs(win_y - (target_mi.rcMonitor.top - 8)) <= 25)
        near_full_size = (win_w >= t_w - 20 and win_h >= t_h - 20)
        legacy_max = (win_x <= -8 and win_y <= -8 and win_w >= 1900)
        should_maximize = (near_top_left and near_full_size) or legacy_max

    is_diff_monitor = False
    if has_target_mon and has_curr_mon:
        is_diff_monitor = (
            target_mi.rcMonitor.left != curr_mi.rcMonitor.left or
            target_mi.rcMonitor.top != curr_mi.rcMonitor.top
        )

    currently_zoomed = bool(user32.IsZoomed(hwnd))

    if should_maximize:
        if is_diff_monitor or not currently_zoomed:
            if currently_zoomed:
                user32.ShowWindow(hwnd, 9)
                time.sleep(0.05)
            if has_target_mon:
                temp_x = target_mi.rcWork.left + 50
                temp_y = target_mi.rcWork.top + 50
                temp_w = max(400, min(win_w, (target_mi.rcWork.right - target_mi.rcWork.left) - 100))
                temp_h = max(300, min(win_h, (target_mi.rcWork.bottom - target_mi.rcWork.top) - 100))
                user32.SetWindowPos(hwnd, 0, temp_x, temp_y, temp_w, temp_h, 0x0044)
                time.sleep(0.05)
            user32.ShowWindow(hwnd, 3)
    else:
        if currently_zoomed:
            user32.ShowWindow(hwnd, 9)
            time.sleep(0.05)
        user32.SetWindowPos(hwnd, 0, win_x, win_y, win_w, win_h, 0x0044)


def activate_and_restore_window(window_title: str, win_x: int, win_y: int, win_w: int, win_h: int, workflow_id: str, launch_cmd: str = "", mapped_hwnd: int = None, is_maximized: bool = None):
    # Why: pywinauto排除とWin32API直接探索によりCOMデッドロックによる実行フリーズを完全根絶
    global _browser_activated_once
    if not window_title or platform.system() != "Windows":
        return None

    is_system_window = any(sw in window_title.lower() for sw in SYSTEM_WINDOW_KEYWORDS)
    if is_system_window:
        return None

    import win32gui
    import win32con

    app_name = window_title.split("—")[-1].split("-")[-1].strip()
    is_target_browser = any(b in app_name.lower() for b in SUPPORTED_BROWSERS)
    force_new = (mapped_hwnd == -1)

    matched_hwnds = []

    if mapped_hwnd and not force_new:
        if win32gui.IsWindow(mapped_hwnd) and win32gui.IsWindowVisible(mapped_hwnd):
            matched_hwnds.append(mapped_hwnd)

    if not matched_hwnds and not force_new:
        def _enum_proc(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and not win32gui.GetParent(hwnd):
                txt = win32gui.GetWindowText(hwnd)
                if txt:
                    txt_lower = txt.lower()
                    if is_target_browser:
                        if any(b in txt_lower for b in SUPPORTED_BROWSERS):
                            matched_hwnds.append(hwnd)
                    elif app_name.lower() in txt_lower or window_title.lower() in txt_lower:
                        matched_hwnds.append(hwnd)
            return True

        for _ in range(6):
            win32gui.EnumWindows(_enum_proc, None)
            if matched_hwnds:
                break
            time.sleep(0.2)

    target_hwnd = matched_hwnds[0] if matched_hwnds else None

    if not target_hwnd:
        lower_app_name = app_name.lower()
        lower_title = window_title.lower()
        if not launch_cmd:
            if "firefox" in lower_app_name:
                launch_cmd = "start firefox -new-window" if force_new else "start firefox"
            elif "chrome" in lower_app_name:
                launch_cmd = "start chrome --new-window" if force_new else "start chrome"
            elif "edge" in lower_app_name:
                launch_cmd = "start msedge --new-window" if force_new else "start msedge"
            elif "excel" in lower_app_name:
                launch_cmd = "start excel"

        if launch_cmd:
            existing_hwnds = set()
            def _collect(h, _):
                if win32gui.IsWindowVisible(h): existing_hwnds.add(h)
                return True
            win32gui.EnumWindows(_collect, None)

            use_shell = launch_cmd.startswith("start ")
            subprocess.Popen(launch_cmd, shell=use_shell, creationflags=0x08000000)

            for _ in range(15):
                time.sleep(0.3)
                new_hwnds = []
                def _find_new(h, _):
                    if win32gui.IsWindowVisible(h) and not win32gui.GetParent(h) and h not in existing_hwnds:
                        txt = win32gui.GetWindowText(h).lower()
                        if (is_target_browser and any(b in txt for b in SUPPORTED_BROWSERS)) or (app_name.lower() in txt):
                            new_hwnds.append(h)
                    return True
                win32gui.EnumWindows(_find_new, None)
                if new_hwnds:
                    target_hwnd = new_hwnds[0]
                    break

    if target_hwnd:
        user32 = ctypes.windll.user32
        if win32gui.IsIconic(target_hwnd):
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)

        user32.SetForegroundWindow(target_hwnd)
        user32.BringWindowToTop(target_hwnd)

        if win_w > 0 and win_h > 0:
            try:
                _restore_window_position_and_monitor(target_hwnd, win_x, win_y, win_w, win_h, is_maximized)
            except Exception as e:
                logger.warning(f"Failed to resize window: {e}")

        time.sleep(0.2)
        return target_hwnd

    return None
