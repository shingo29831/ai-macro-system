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

def activate_and_restore_window(window_title: str, win_x: int, win_y: int, win_w: int, win_h: int, workflow_id: str, launch_cmd: str = "", mapped_hwnd: int = None):
    global _browser_activated_once
    if not window_title or platform.system() != "Windows":
        return

    is_system_window = any(sw in window_title.lower() for sw in SYSTEM_WINDOW_KEYWORDS)
    
    if is_system_window:
        return

    import pywinauto
    desktop = pywinauto.Desktop(backend="uia")
    
    browser_names = SUPPORTED_BROWSERS
    app_name = window_title.split("—")[-1].split("-")[-1].strip()
    is_target_browser = any(b in app_name.lower() for b in SUPPORTED_BROWSERS)
    
    windows = []
    force_new = (mapped_hwnd == -1)
    
    if mapped_hwnd and not force_new:
        try:
            app = pywinauto.Application(backend="uia").connect(handle=mapped_hwnd)
            win = app.window(handle=mapped_hwnd)
            if win.exists():
                # Why: 渡されたHWNDが目的のアプリ名と一致するか検証し異種アプリの誤リサイズを防止
                actual_text = win.window_text().lower()
                target_app_lower = app_name.lower()
                if is_target_browser:
                    is_valid = any(b in actual_text for b in SUPPORTED_BROWSERS)
                else:
                    is_valid = (target_app_lower in actual_text) or not any(b in actual_text for b in SUPPORTED_BROWSERS)
                if is_valid:
                    windows = [win]
                else:
                    logger.warning(f"mapped_hwnd {mapped_hwnd} ({actual_text}) does not match target app '{app_name}'. Re-searching...")
        except Exception as e:
            logger.warning(f"Failed to connect to mapped_hwnd {mapped_hwnd}: {e}")

    if not windows and not force_new:
        safe_title = re.escape(window_title)
        # Why: 新規ブックの日英表記ゆれ(Book/ブック)を相互許容して正しく検索
        if "book" in window_title.lower() or "ブック" in window_title:
            pattern_title = re.sub(r"(?:book|ブック)\s*(\d+)", r"(?:Book|ブック)\s*\1", safe_title, flags=re.IGNORECASE)
        else:
            pattern_title = safe_title

        for _ in range(10):
            all_matched = desktop.windows(title_re=f".*{pattern_title}.*", visible_only=True)
            if all_matched:
                if is_target_browser:
                    windows = all_matched
                else:
                    windows = [w for w in all_matched if not any(b in w.window_text().lower() for b in browser_names)]
            if windows:
                break
            time.sleep(0.5)
    
    before_hwnds = set()
    if not windows and not force_new and app_name:
        safe_app_name = re.escape(app_name)
        is_generic_excel = ("excel" in app_name.lower()) and ("book" in window_title.lower() or "ブック" in window_title)
        
        for _ in range(4):
            # Why: 新規ブック検索時に既存の名前付き別ファイルを誤爆しないよう制限
            if is_generic_excel:
                all_matched = desktop.windows(title_re=r".*(?:Book|ブック)\s*\d+.*Excel.*", visible_only=True)
            else:
                all_matched = desktop.windows(title_re=f".*{safe_app_name}\\s*$", visible_only=True)
            if all_matched:
                if is_target_browser:
                    windows = all_matched
                else:
                    windows = [w for w in all_matched if not any(b in w.window_text().lower() for b in browser_names)]
            
            if not windows and not is_generic_excel:
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
                    # Why: 既存プロセス存在時も確実に独立した新規ウィンドウを起動
                    launch_cmd = "start firefox -new-window"
            elif "chrome" in lower_app_name:
                if "シークレット" in lower_title or "incognito" in lower_title:
                    launch_cmd = "start chrome --incognito"
                else:
                    # Why: 既存プロセス存在時も確実に独立した新規ウィンドウを起動
                    launch_cmd = "start chrome --new-window"
            elif "edge" in lower_app_name:
                if "inprivate" in lower_title:
                    launch_cmd = "start msedge --inprivate"
                else:
                    # Why: 既存プロセス存在時も確実に独立した新規ウィンドウを起動
                    launch_cmd = "start msedge --new-window"
            elif "excel" in lower_app_name:
                launch_cmd = "start excel"
            elif "visual studio code" in lower_app_name or lower_app_name == "code":
                launch_cmd = "code"
            
        if launch_cmd:
            before_hwnds.update(w.handle for w in desktop.windows(visible_only=True))
            
            creationflags = 0x08000000
            use_shell = launch_cmd.startswith("start ")
            subprocess.Popen(launch_cmd, shell=use_shell, creationflags=creationflags)
            is_newly_launched = True
            
            safe_app_name = re.escape(app_name)
            for _ in range(20):
                time.sleep(0.5)
                current_windows = desktop.windows(visible_only=True)
                new_windows = [w for w in current_windows if w.handle not in before_hwnds]
                
                if new_windows:
                    matched_new = [w for w in new_windows if re.search(f".*{safe_app_name}.*", w.window_text(), re.IGNORECASE)]
                    if matched_new:
                        windows = matched_new
                        break
                    else:
                        windows = new_windows
                        break
            
        is_browser = any(b in lower_app_name for b in SUPPORTED_BROWSERS)
        if is_browser:
            _browser_activated_once = True
            
    if windows:
        win = windows[0]
        
        if "excel" in app_name.lower() and is_newly_launched:
            try:
                win.set_focus()
                time.sleep(0.5)
                win_text = win.window_text()
                # Why: タイトルにブック名が含まれていないスタート画面状態のみEnterで空白ブックを選択
                if not ("book" in win_text.lower() or "ブック" in win_text.lower()):
                    from pynput.keyboard import Controller as KeyboardController, Key
                    keyboard = KeyboardController()
                    keyboard.press(Key.enter)
                    keyboard.release(Key.enter)
                    time.sleep(1.0)
                    newly_opened = [w for w in desktop.windows(title_re=f".*{re.escape(app_name)}.*", visible_only=True) if w.handle not in before_hwnds]
                    if newly_opened:
                        win = newly_opened[0]
            except Exception as e:
                logger.warning(f"Failed to send Enter key to Excel start screen: {e}")

        if win.is_minimized():
            win.restore()
            
        try:
            user32 = ctypes.windll.user32
            hwnd = win.handle
            
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            # Why: Alt押下によるOfficeリボンのキーヒント待機をEscで解除し入力阻害を防ぐ
            user32.keybd_event(0x1B, 0, 0, 0)
            user32.keybd_event(0x1B, 0, 2, 0)
            
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            win.set_focus()

            # Why: Excel親ウィンドウフォーカス後にEXCEL7子ウィンドウへ入力フォーカスを確立
            if "excel" in app_name.lower():
                import win32gui
                def _restore_excel7_focus(child, _):
                    if win32gui.GetClassName(child) == "EXCEL7":
                        user32.SetFocus(child)
                    return True
                win32gui.EnumChildWindows(hwnd, _restore_excel7_focus, None)
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
        # Why: アクティベートした正確なウィンドウハンドルを呼び出し元へ返し誤爆を防止
        return win.handle
    else:
        logger.error(f"[{workflow_id}] Failed to find or launch window: {window_title}")
        raise RuntimeError(f"対象のアプリ（{app_name}）が起動できず、ウィンドウが見つかりません。")
