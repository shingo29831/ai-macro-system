# Role: OSレベルのウィンドウ操作、IME制御、システムウィンドウ判定を担当するモジュール

import platform
import ctypes
import re
import time
import subprocess
import logging

logger = logging.getLogger(__name__)

_browser_activated_once = False

def reset_browser_activation_flag():
    global _browser_activated_once
    _browser_activated_once = False

def set_dpi_awareness():
    if platform.system() == "Windows":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

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
                
                ignored_titles = ["記録中", "停止中", "AI Macro System", "設定", "AIマクロ生成中...", "実行中", "実行中..."]
                if any(ignored in title for ignored in ignored_titles):
                    rect = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        rects.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    user32.EnumWindows(EnumWindowsProc(enum_windows_proc), 0)
    return rects

def set_ime_state(text: str):
    if platform.system() != "Windows":
        return
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        default_ime_wnd = ctypes.windll.imm32.ImmGetDefaultIMEWnd(hwnd)
        if default_ime_wnd:
            WM_IME_CONTROL = 0x0283
            IMC_SETOPENSTATUS = 0x0006
            ctypes.windll.user32.SendMessageW(default_ime_wnd, WM_IME_CONTROL, IMC_SETOPENSTATUS, 0)
            time.sleep(0.15)
    except Exception as e:
        logger.warning(f"Failed to set IME state: {e}")

def get_open_windows_info():
    if platform.system() != "Windows":
        return []
    
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image
    
    windows_info = []
    
    def enum_windows_proc(hwnd, lParam):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowTextLength(hwnd) > 0:
            title = win32gui.GetWindowText(hwnd)
            ignored_titles = ["記録中", "停止中", "AI Macro System", "設定", "AIマクロ生成中...", "実行中", "実行中...", "Program Manager"]
            if not any(ignored in title for ignored in ignored_titles):
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

def activate_and_restore_window(window_title: str, win_x: int, win_y: int, win_w: int, win_h: int, workflow_id: str, launch_cmd: str = "", mapped_hwnd: int = None):
    global _browser_activated_once
    if not window_title or platform.system() != "Windows":
        return

    system_windows = ["program manager", "ジャンプ リスト", "taskbar", "cortana", "検索"]
    is_system_window = any(sw in window_title.lower() for sw in system_windows)
    
    if is_system_window:
        return

    import pywinauto
    desktop = pywinauto.Desktop(backend="uia")
    
    app_name = window_title.split("—")[-1].split("-")[-1].strip()
    browser_names = ["firefox", "chrome", "edge", "brave", "opera"]
    is_target_browser = any(b in app_name.lower() for b in browser_names)
    
    windows = []
    force_new = (mapped_hwnd == -1)
    
    if mapped_hwnd and not force_new:
        try:
            app = pywinauto.Application(backend="uia").connect(handle=mapped_hwnd)
            win = app.window(handle=mapped_hwnd)
            if win.exists():
                windows = [win]
        except Exception as e:
            logger.warning(f"Failed to connect to mapped_hwnd {mapped_hwnd}: {e}")

    if not windows and not force_new:
        safe_title = re.escape(window_title)
        for _ in range(10):
            all_matched = desktop.windows(title_re=f".*{safe_title}.*", visible_only=True)
            if all_matched:
                if is_target_browser:
                    windows = all_matched
                else:
                    windows = [w for w in all_matched if not any(b in w.window_text().lower() for b in browser_names)]
            if windows:
                break
            time.sleep(0.5)
    
    if not windows and app_name:
        safe_app_name = re.escape(app_name)
        
        for _ in range(4):
            all_matched = desktop.windows(title_re=f".*{safe_app_name}\\s*$", visible_only=True)
            if all_matched:
                if is_target_browser:
                    windows = all_matched
                else:
                    windows = [w for w in all_matched if not any(b in w.window_text().lower() for b in browser_names)]
            
            if not windows:
                all_matched = desktop.windows(title_re=f".*{safe_app_name}.*", visible_only=True)
                if all_matched:
                    if is_target_browser:
                        windows = all_matched
                    else:
                        windows = [w for w in all_matched if not any(b in w.window_text().lower() for b in browser_names)]
                    
            if windows:
                break
            time.sleep(0.5)
            
    is_newly_launched = False
    if not windows:
        logger.warning(f"[{workflow_id}] Window not found: {window_title}. Attempting to launch...")
        lower_app_name = app_name.lower()
        lower_title = window_title.lower()
        
        if not launch_cmd:
            if "firefox" in lower_app_name:
                if "プライベート" in lower_title or "private" in lower_title:
                    launch_cmd = "start firefox -private-window"
                else:
                    launch_cmd = "start firefox"
            elif "chrome" in lower_app_name:
                if "シークレット" in lower_title or "incognito" in lower_title:
                    launch_cmd = "start chrome --incognito"
                else:
                    launch_cmd = "start chrome"
            elif "edge" in lower_app_name:
                if "inprivate" in lower_title:
                    launch_cmd = "start msedge --inprivate"
                else:
                    launch_cmd = "start msedge"
            elif "excel" in lower_app_name:
                launch_cmd = "start excel"
            
        if launch_cmd:
            creationflags = 0x08000000
            use_shell = launch_cmd.startswith("start ")
            subprocess.Popen(launch_cmd, shell=use_shell, creationflags=creationflags)
            is_newly_launched = True
            
            for _ in range(10):
                time.sleep(1.0)
                windows = desktop.windows(title_re=f".*{safe_app_name}.*", visible_only=True)
                if windows:
                    break
            
        is_browser = any(b in lower_app_name for b in ["firefox", "chrome", "edge", "brave", "opera"])
        if is_browser:
            _browser_activated_once = True
            
    if windows:
        win = windows[0]
        
        if "excel" in app_name.lower() and is_newly_launched:
            try:
                win.set_focus()
                time.sleep(0.5)
                from pynput.keyboard import Controller as KeyboardController, Key
                keyboard = KeyboardController()
                keyboard.press(Key.enter)
                keyboard.release(Key.enter)
                time.sleep(1.0)
                all_matched = desktop.windows(title_re=f".*{re.escape(app_name)}.*", visible_only=True)
                if all_matched:
                    win = all_matched[0]
            except Exception as e:
                logger.warning(f"Failed to send Enter key to Excel start screen: {e}")

        if win.is_minimized():
            win.restore()
            
        try:
            user32 = ctypes.windll.user32
            hwnd = win.handle
            
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            win.set_focus()
        except Exception as e:
            logger.warning(f"Failed to set focus aggressively: {e}")
            try:
                win.set_focus()
            except Exception:
                pass
        
        if win_w > 0 and win_h > 0:
            try:
                hwnd = win.handle
                if win_x <= -8 and win_y <= -8 and win_w >= 1900:
                    if not win.is_maximized():
                        win.maximize()
                else:
                    if win.is_maximized():
                        win.restore()
                    ctypes.windll.user32.SetWindowPos(hwnd, 0, win_x, win_y, win_w, win_h, 0x0040)
            except Exception as e:
                logger.warning(f"Failed to resize window: {e}")
                
        time.sleep(0.5)
    else:
        logger.error(f"[{workflow_id}] Failed to find or launch window: {window_title}")
        raise RuntimeError(f"対象のアプリ（{app_name}）が起動できず、ウィンドウが見つかりません。")
