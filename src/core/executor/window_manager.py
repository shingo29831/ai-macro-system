"""Module: @role: マクロ実行時の対象ウィンドウの探索・自動起動・最前面化およびマルチモニタ配置復元を担当する。"""

import ctypes
import logging
import platform
import subprocess
import time

from core.shared.window_utils import BROWSER_KEYWORDS, SYSTEM_WINDOW_KEYWORDS, extract_app_name

logger = logging.getLogger(__name__)

SUPPORTED_BROWSERS = list(BROWSER_KEYWORDS)


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
    if not window_title or platform.system() != "Windows":
        return None

    is_system_window = any(sw in window_title.lower() for sw in SYSTEM_WINDOW_KEYWORDS)
    if is_system_window:
        return None

    import win32gui
    import win32con

    app_name = extract_app_name(window_title)
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
                        # Why: ブラウザ名だけでなくターゲットタイトルの合致度を最優先
                        if window_title.lower() in txt_lower:
                            matched_hwnds.insert(0, hwnd)
                        elif any(b in txt_lower for b in SUPPORTED_BROWSERS):
                            matched_hwnds.append(hwnd)
                    elif window_title.lower() in txt_lower:
                        matched_hwnds.insert(0, hwnd)
                    elif app_name.lower() in txt_lower:
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
