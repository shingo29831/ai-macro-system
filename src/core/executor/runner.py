# src/core/executor/runner.py
# @role: 生成されたExecutable Macro (executable_macro.json) を読み込み、ローカルで自律実行する実行エンジン。

import json
import logging
import time
import platform
import ctypes
import re
import subprocess
from pathlib import Path
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key, Listener as KeyboardListener

from models.data_types import AppConfig

logger = logging.getLogger(__name__)

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120

_is_running = False
_stop_requested = False

def _set_dpi_awareness():
    if platform.system() == "Windows":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

_set_dpi_awareness()

def _get_system_window_rects():
    """AI Macro System 自身のウィンドウ矩形を取得し、画像比較から除外するためのリストを返す"""
    rects = []
    if platform.system() != "Windows":
        return rects
    
    import ctypes
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

def _calculate_ssim(img1, img2, mask=None):
    """OpenCVを用いてSSIM (Structural Similarity Index) を計算する"""
    import cv2
    import numpy as np
    C1 = 6.5025
    C2 = 58.5225
    i1 = img1.astype(np.float32)
    i2 = img2.astype(np.float32)
    mu1 = cv2.GaussianBlur(i1, (11, 11), 1.5)
    mu2 = cv2.GaussianBlur(i2, (11, 11), 1.5)
    mu1_sq = mu1 ** 2
    mu2_sq = mu2 ** 2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = cv2.GaussianBlur(i1 ** 2, (11, 11), 1.5) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(i2 ** 2, (11, 11), 1.5) - mu2_sq
    sigma12 = cv2.GaussianBlur(i1 * i2, (11, 11), 1.5) - mu1_mu2
    ssim_map = ((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / ((mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2))
    
    if mask is not None:
        valid_pixels = mask > 0
        if not np.any(valid_pixels):
            return 0.0
        return float(ssim_map[valid_pixels].mean())
    return float(ssim_map.mean())

def _calculate_orb_match(img1, img2, mask=None):
    """ORB特徴点マッチングにより、画像間の特徴一致率を計算する"""
    import cv2
    orb = cv2.ORB_create(nfeatures=500)
    kp1, des1 = orb.detectAndCompute(img1, mask)
    kp2, des2 = orb.detectAndCompute(img2, mask)
    if des1 is None or des2 is None or len(kp1) == 0 or len(kp2) == 0:
        return 0.0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(des1, des2)
    # 距離が近い（似ている）特徴点のみを抽出
    good_matches = [m for m in matches if m.distance < 50]
    return float(len(good_matches) / max(len(kp1), 1))

def _set_ime_state(text: str):
    """テキスト入力前にWindowsのIMEを確実にオフにする。
    pynputのkeyboard.typeはUnicodeで直接文字を送信するため、
    IMEがオンだと逆にキー入力が横取りされて文字化け（「ごおｇぇ」等）の原因となる。"""
    if platform.system() != "Windows":
        return
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        default_ime_wnd = ctypes.windll.imm32.ImmGetDefaultIMEWnd(hwnd)
        if default_ime_wnd:
            WM_IME_CONTROL = 0x0283
            IMC_SETOPENSTATUS = 0x0006
            # 常にIMEをオフ(0)にする
            ctypes.windll.user32.SendMessageW(default_ime_wnd, WM_IME_CONTROL, IMC_SETOPENSTATUS, 0)
            time.sleep(0.15)
    except Exception as e:
        logger.warning(f"Failed to set IME state: {e}")

def _activate_and_restore_window(window_title: str, win_x: int, win_y: int, win_w: int, win_h: int, keyboard, workflow_id: str, launch_cmd: str = ""):
    """対象のウィンドウをアクティブにし、必要に応じてアプリを起動・サイズ復元を行う"""
    global _browser_activated_once
    if not window_title or platform.system() != "Windows":
        return

    # システムウィンドウは起動やエラー判定、フォーカス操作を完全にスキップ
    system_windows = ["program manager", "ジャンプ リスト", "taskbar", "cortana", "検索"]
    is_system_window = any(sw in window_title.lower() for sw in system_windows)
    
    if is_system_window:
        return

    import pywinauto
    desktop = pywinauto.Desktop(backend="uia")
    safe_title = re.escape(window_title)
    
    # ウィンドウが現れるまで少し待機する（最大5秒）
    windows = []
    for _ in range(10):
        windows = desktop.windows(title_re=f".*{safe_title}.*", visible_only=True)
        if windows:
            break
        time.sleep(0.5)
    
    app_name = window_title.split("—")[-1].split("-")[-1].strip()
    
    if not windows and app_name:
        safe_app_name = re.escape(app_name)
        for _ in range(4):
            windows = desktop.windows(title_re=f".*{safe_app_name}.*", visible_only=True)
            if windows:
                break
            time.sleep(0.5)
            
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
            creationflags = 0x08000000 # CREATE_NO_WINDOW (cmd画面を非表示)
            use_shell = launch_cmd.startswith("start ")
            subprocess.Popen(launch_cmd, shell=use_shell, creationflags=creationflags)
            
            # 起動を待機
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
        
        # 最小化されている場合は元に戻す
        if win.is_minimized():
            win.restore()
            
        win.set_focus()
        
        if win_w > 0 and win_h > 0:
            try:
                hwnd = win.handle
                # ★修正: Windowsの最大化状態の典型的な座標（-8, -8）を検知して最大化コマンドを送る
                # これを行わないと、枠線（ボーダー）の分だけUIのY座標が絶妙にずれる
                if win_x <= -8 and win_y <= -8 and win_w >= 1900:
                    if not win.is_maximized():
                        win.maximize()
                else:
                    if win.is_maximized():
                        win.restore()
                    # SWP_NOZORDER = 0x0004 (Zオーダーを変更しない)
                    ctypes.windll.user32.SetWindowPos(hwnd, 0, win_x, win_y, win_w, win_h, 0x0004)
            except Exception as e:
                logger.warning(f"Failed to resize window: {e}")
                
        time.sleep(0.5)
    else:
        logger.error(f"[{workflow_id}] Failed to find or launch window: {window_title}")
        raise RuntimeError(f"対象のアプリ（{app_name}）が起動できず、ウィンドウが見つかりません。")

def _wait_for_screen_match(target_dir: Path, raw_event_id: str, win_x: int, win_y: int, win_w: int, win_h: int, workflow_id: str, status_callback, step_index: int, timeout: float = 30.0) -> dict:
    """記録時のスクリーンショットと現在の画面を比較し、変化率が閾値以下になるまで待機する"""
    global _stop_requested
    result_info = {"matched": False, "time_taken": 0.0, "scores": {}}
    if not raw_event_id or win_w <= 0 or win_h <= 0:
        return result_info

    pre_image_path = target_dir / "images" / f"{raw_event_id}_pre.png"
    if not pre_image_path.exists():
        return result_info
        
    exec_logs_dir = target_dir / "execution_logs"
    exec_logs_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        import shutil
        target_copy_path = exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_target.png"
        if not target_copy_path.exists():
            shutil.copy2(pre_image_path, target_copy_path)
    except Exception as e:
        logger.warning(f"Failed to copy target image: {e}")

    def update_ui(text, is_warning):
        if status_callback:
            status_callback(text, is_warning)
        else:
            try:
                from ui.views.running_dialog import RunningDialog
                RunningDialog.set_status(text, is_warning)
            except Exception:
                pass

    try:
        import cv2
        import numpy as np
        from core.recorder.screen_capturer import take_screenshot
        
        pre_img_cv = cv2.imread(str(pre_image_path), cv2.IMREAD_GRAYSCALE)
        if pre_img_cv is None:
            return result_info
            
        img_h, img_w = pre_img_cv.shape
        
        # マルチモニターのオフセットを考慮するため、ダミー呼び出しでモニター情報を取得
        _, monitor_info = take_screenshot()
        offset_x = monitor_info.get("left", 0) if isinstance(monitor_info, dict) else 0
        offset_y = monitor_info.get("top", 0) if isinstance(monitor_info, dict) else 0
        
        # ウィンドウの枠線や影をノイズとしないよう、内側にマージンを設ける
        margin = 8
        x1 = max(0, win_x - offset_x + margin)
        y1 = max(0, win_y - offset_y + margin)
        x2 = min(img_w, win_x - offset_x + win_w - margin)
        y2 = min(img_h, win_y - offset_y + win_h - margin)
        
        if x2 <= x1 or y2 <= y1:
            x1, y1 = max(0, win_x + margin), max(0, win_y + margin)
            x2, y2 = min(img_w, win_x + win_w - margin), min(img_h, win_y + win_h - margin)
            if x2 <= x1 or y2 <= y1:
                return result_info
            
        pre_crop = pre_img_cv[y1:y2, x1:x2]
        
        scale = min(1.0, 512.0 / max(pre_crop.shape[0], pre_crop.shape[1]))
        if scale < 1.0:
            pre_crop_eval = cv2.resize(pre_crop, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        else:
            pre_crop_eval = pre_crop.copy()
            
        waiting_logged = False
        frame_buffer = [] 
        start_time = time.time()
        last_frame_crop = None
        
        while not _stop_requested:
            if time.time() - start_time > timeout:
                logger.warning(f"[{workflow_id}] Screen match timeout ({timeout}s). Proceeding to next action.")
                if waiting_logged:
                    update_ui("タイムアウトしました。マクロを再開します。", False)
                
                try:
                    if 'curr_crop_color' in locals() and 'video_mask' in locals() and 'text_mask' in locals():
                        if video_mask.shape[:2] != curr_crop_color.shape[:2]:
                            video_mask_resized = cv2.resize(video_mask, (curr_crop_color.shape[1], curr_crop_color.shape[0]), interpolation=cv2.INTER_NEAREST)
                            text_mask_resized = cv2.resize(text_mask, (curr_crop_color.shape[1], curr_crop_color.shape[0]), interpolation=cv2.INTER_NEAREST)
                        else:
                            video_mask_resized = video_mask
                            text_mask_resized = text_mask
                        
                        overlay = curr_crop_color.copy()
                        # 動画領域は赤色 (BGR: 0, 0, 255)
                        overlay[video_mask_resized == 255] = [0, 0, 255]
                        # テキスト領域は緑色 (BGR: 0, 255, 0)
                        overlay[text_mask_resized == 255] = [0, 255, 0]
                        
                        alpha = 0.5
                        highlighted_img = cv2.addWeighted(overlay, alpha, curr_crop_color, 1 - alpha, 0)
                        
                        cv2.imwrite(str(exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_timeout_masked.png"), highlighted_img)
                        logger.info(f"[{workflow_id}] Saved timeout masked image (Video: Red, Text: Green).")
                except Exception as e:
                    logger.warning(f"Failed to save timeout masked image: {e}")
                    
                break

            curr_img_pil, curr_monitor = take_screenshot()
            curr_img_color = cv2.cvtColor(np.array(curr_img_pil), cv2.COLOR_RGB2BGR)
            curr_img_cv = cv2.cvtColor(curr_img_color, cv2.COLOR_BGR2GRAY)
            
            c_offset_x = curr_monitor.get("left", 0) if isinstance(curr_monitor, dict) else 0
            c_offset_y = curr_monitor.get("top", 0) if isinstance(curr_monitor, dict) else 0
            
            curr_h, curr_w = curr_img_cv.shape
            cx1 = max(0, win_x - c_offset_x + margin)
            cy1 = max(0, win_y - c_offset_y + margin)
            cx2 = min(curr_w, win_x - c_offset_x + win_w - margin)
            cy2 = min(curr_h, win_y - c_offset_y + win_h - margin)
            
            if cx2 <= cx1 or cy2 <= cy1:
                cx1, cy1 = max(0, win_x), max(0, win_y)
                cx2, cy2 = min(curr_w, win_x + win_w), min(curr_h, win_y + win_h)
                
            if cx2 > cx1 and cy2 > cy1:
                curr_crop = curr_img_cv[cy1:cy2, cx1:cx2]
                curr_crop_color = curr_img_color[cy1:cy2, cx1:cx2]
                if scale < 1.0:
                    curr_crop_eval = cv2.resize(curr_crop, (pre_crop_eval.shape[1], pre_crop_eval.shape[0]), interpolation=cv2.INTER_AREA)
                else:
                    curr_crop_eval = curr_crop.copy()
                
                frame_buffer.append(curr_crop_eval)
                if len(frame_buffer) > 5:
                    frame_buffer.pop(0)
                
                video_mask = np.zeros_like(curr_crop_eval, dtype=np.uint8)
                if len(frame_buffer) >= 3:
                    std_dev = np.std(frame_buffer, axis=0)
                    # 膨張(dilate)を行わず、本当に変化が激しいピクセルのみを厳密にマスクする
                    video_mask = (std_dev > 20).astype(np.uint8) * 255
                    
                    # 静的なエッジ（UIの枠線や文字など）を保護する
                    # 複数フレームで共通して存在するエッジを抽出し、背景動画の変動から守る
                    edges_list = [cv2.Canny(f, 50, 150) for f in frame_buffer]
                    static_edges = edges_list[0]
                    for e in edges_list[1:]:
                        static_edges = cv2.bitwise_and(static_edges, e)
                    
                    kernel_protect = np.ones((3, 3), np.uint8)
                    static_edges_dilated = cv2.dilate(static_edges, kernel_protect, iterations=1)
                    
                    # 静的なUI構造部分はマスクしない
                    video_mask[static_edges_dilated == 255] = 0
                    
                    # 動的マスクが広すぎる（画面の30%以上）場合は、過剰マスクとみなして破棄する
                    # これにより、動画が大部分を占めるページで有効ピクセルが消滅し、ORB/SSIMが0になるのを防ぐ
                    if np.count_nonzero(video_mask) / video_mask.size > 0.3:
                        video_mask = np.zeros_like(video_mask)
                
               # --- テキスト領域のマスク処理（文字の違いや背景色変化による不一致を防ぐ） ---
                diff_for_mask = cv2.absdiff(pre_crop_eval, curr_crop_eval)
                _, diff_thresh = cv2.threshold(diff_for_mask, 30, 255, cv2.THRESH_BINARY)

                kernel_text = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
                diff_dilated = cv2.dilate(diff_thresh, kernel_text, iterations=1)

                text_mask = np.zeros_like(curr_crop_eval, dtype=np.uint8)
                contours, _ = cv2.findContours(diff_dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                for cnt in contours:
                    x, y, w, h = cv2.boundingRect(cnt)
                    # 変化領域が文字、カーソル、入力フィールド程度のサイズであるか確認
                    if 2 <= w < curr_crop_eval.shape[1] * 0.8 and 5 <= h < curr_crop_eval.shape[0] * 0.5:
                        region_curr = curr_crop_eval[y:y+h, x:x+w]
                        region_pre = pre_crop_eval[y:y+h, x:x+w]
                        region_diff = diff_thresh[y:y+h, x:x+w]

                        # エッジを抽出して文字らしさ（複雑さ）を判定
                        edges_curr = cv2.Canny(region_curr, 50, 150)
                        edges_pre = cv2.Canny(region_pre, 50, 150)

                        # 差分領域内のエッジのみを評価対象とする
                        valid_edges_curr = cv2.bitwise_and(edges_curr, edges_curr, mask=region_diff)
                        valid_edges_pre = cv2.bitwise_and(edges_pre, edges_pre, mask=region_diff)

                        area = w * h
                        # 入力前・入力後のどちらかに文字特有のエッジが含まれていればテキスト領域とみなす
                        edge_count = max(np.count_nonzero(valid_edges_curr), np.count_nonzero(valid_edges_pre))
                        edge_density = edge_count / area if area > 0 else 0
                        # 文字は線が多いためエッジ密度が比較的高くなる（5%以上を文字やカーソルとみなす）
                        # 背景色が変化した場合でも、文字が含まれていればエッジ密度で救済される
                        if edge_density > 0.05:
                            # 矩形で塗りつぶすのではなく、実際の変化ピクセルのみを厳密にマスクする
                            text_mask[y:y+h, x:x+w] = cv2.bitwise_or(text_mask[y:y+h, x:x+w], region_diff)

                            # 参考用：検出した文字領域の画像を保存する
                            try:
                                debug_dir = exec_logs_dir / "text_detection_debug"
                                debug_dir.mkdir(parents=True, exist_ok=True)
                                timestamp = int(time.time() * 1000)
                                cv2.imwrite(str(debug_dir / f"step_{step_index:03d}_{raw_event_id}_{timestamp}_curr.png"), region_curr)
                                cv2.imwrite(str(debug_dir / f"step_{step_index:03d}_{raw_event_id}_{timestamp}_pre.png"), region_pre)
                                cv2.imwrite(str(debug_dir / f"step_{step_index:03d}_{raw_event_id}_{timestamp}_diff.png"), region_diff)
                            except Exception as e:
                                logger.warning(f"Failed to save text detection debug image: {e}")

                dynamic_mask = cv2.bitwise_or(video_mask, text_mask)
                # ------------------------------------
                
                # --- システムウィンドウのマスク処理 ---
                system_rects = _get_system_window_rects()
                for (sl, st, sr, sb) in system_rects:
                    abs_cx1 = c_offset_x + cx1
                    abs_cy1 = c_offset_y + cy1
                    ml = max(0, int((sl - abs_cx1) * scale))
                    mt = max(0, int((st - abs_cy1) * scale))
                    mr = min(curr_crop_eval.shape[1], int((sr - abs_cx1) * scale))
                    mb = min(curr_crop_eval.shape[0], int((sb - abs_cy1) * scale))
                    if mr > ml and mb > mt:
                        dynamic_mask[mt:mb, ml:mr] = 255
                # ------------------------------------
                
                static_mask = cv2.bitwise_not(dynamic_mask)
                valid_area = np.count_nonzero(static_mask)

                is_screen_changing = False
                if last_frame_crop is not None:
                    diff_with_last = cv2.absdiff(last_frame_crop, curr_crop_eval)
                    _, thresh_last = cv2.threshold(diff_with_last, 30, 255, cv2.THRESH_BINARY)
                    
                    # 動的マスク（動画など）を除外して画面遷移を判定
                    thresh_last = cv2.bitwise_and(thresh_last, thresh_last, mask=static_mask)
                    
                    if valid_area > 500:
                        change_ratio = np.count_nonzero(thresh_last) / valid_area
                        # 微小なノイズで遷移中と判定されないよう閾値を緩和
                        if change_ratio > 0.05:
                            is_screen_changing = True
                last_frame_crop = curr_crop_eval.copy()
                
                pre_crop_static = cv2.bitwise_and(pre_crop_eval, pre_crop_eval, mask=static_mask)
                curr_crop_static = cv2.bitwise_and(curr_crop_eval, curr_crop_eval, mask=static_mask)
                
                diff = cv2.absdiff(pre_crop_static, curr_crop_static)
                _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
                
                if valid_area > 500: 
                    diff_ratio = np.count_nonzero(thresh) / valid_area
                else:
                    diff_full = cv2.absdiff(pre_crop_eval, curr_crop_eval)
                    _, thresh_full = cv2.threshold(diff_full, 30, 255, cv2.THRESH_BINARY)
                    diff_ratio = np.count_nonzero(thresh_full) / (curr_crop_eval.shape[0] * curr_crop_eval.shape[1])
                
                # 閾値を再調整（厳格すぎると開始時にマッチしないため、少し緩和）
                is_pixel_match = diff_ratio <= 0.05
                
                res = cv2.matchTemplate(curr_crop_static, pre_crop_static, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)
                is_struct_match = (max_val >= 0.88) and (diff_ratio <= 0.10)
                
                pre_edges = cv2.Canny(pre_crop_static, 50, 150)
                curr_edges = cv2.Canny(curr_crop_static, 50, 150)
                pre_edge_count = np.count_nonzero(pre_edges)
                
                is_edge_match = False
                max_val_edges = 0.0
                if pre_edge_count > 50:
                    res_edges = cv2.matchTemplate(curr_edges, pre_edges, cv2.TM_CCOEFF_NORMED)
                    _, max_val_edges, _, _ = cv2.minMaxLoc(res_edges)
                    if max_val_edges >= 0.75 and diff_ratio <= 0.15:
                        is_edge_match = True

                # 動画などの動的領域を除外してSSIMとORBを計算
                ssim_val = _calculate_ssim(pre_crop_eval, curr_crop_eval, mask=static_mask)
                orb_score = _calculate_orb_match(pre_crop_eval, curr_crop_eval, mask=static_mask)

                result_info["scores"] = {
                    "diff_ratio": float(diff_ratio),
                    "sim": float(max_val),
                    "edge_sim": float(max_val_edges),
                    "ssim": ssim_val,
                    "orb": orb_score
                }

                # 検索結果画面などの変動を考慮し、閾値を緩和
                is_ssim_match = ssim_val >= 0.75
                # 動画ページなどでdiff_ratioが高い場合でも、特徴点と大まかな構造が一致していればOKとする
                is_orb_match = orb_score >= 0.15 or (orb_score >= 0.08 and max_val >= 0.6)

                if is_pixel_match or is_struct_match or is_edge_match or is_ssim_match or is_orb_match:
                    # マッチ成功時の画像を保存
                    try:
                        cv2.imwrite(str(exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_match_curr.png"), curr_crop)
                        cv2.imwrite(str(exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_match_pre.png"), pre_crop)
                    except Exception:
                        pass
                        
                    if waiting_logged:
                        # 簡易比較（ピクセル一致）ではなく、マスク適用後の詳細比較で一致した場合
                        if not is_pixel_match and (is_ssim_match or is_orb_match or is_struct_match):
                            update_ui("動的領域・テキストを除外して一致を確認しました。\nマクロを再開します。", False)
                        else:
                            update_ui("画面の一致を確認しました。\nマクロを再開します。", False)
                            
                        logger.info(f"[{workflow_id}] Screen matched (diff: {diff_ratio:.1%}, sim: {max_val:.2f}, ssim: {ssim_val:.2f}, orb: {orb_score:.2f}). Resuming.")
                        time.sleep(1.5)
                    result_info["matched"] = True
                    break
                else:
                    if is_screen_changing:
                        update_ui("画面遷移を待機しています...", False)
                        waiting_logged = False
                    else:
                        if time.time() - start_time > 3.0:
                            # リアルタイムに検証スコアをUIへ表示し続ける
                            detail_msg = f"記録時と同じ画面にしてください。\n差分: {diff_ratio:.1%} / 構造: {ssim_val:.2f} / 特徴: {orb_score:.2f}"
                            update_ui(detail_msg, True)
                            if not waiting_logged:
                                logger.info(f"[{workflow_id}] Waiting for screen to match... (diff: {diff_ratio:.1%}, sim: {max_val:.2f}, ssim: {ssim_val:.2f}, orb: {orb_score:.2f})")
                                waiting_logged = True
                        elif not waiting_logged:
                            update_ui("画面の応答を待機しています...", False)
            else:
                if time.time() - start_time > 3.0:
                    update_ui("記録時と同じ画面にしてください。\n(ウィンドウサイズが異なります)", True)
                    if not waiting_logged:
                        logger.info(f"[{workflow_id}] Waiting for screen to match... (size mismatch)")
                        waiting_logged = True
                elif not waiting_logged:
                    update_ui("画面の応答を待機しています...", False)
            
            time.sleep(0.5)
            
        result_info["time_taken"] = time.time() - start_time
    except Exception as e:
        logger.warning(f"Error during screen match waiting: {e}")
        
    return result_info

def _is_screen_match(pre_image_path: Path, curr_img_cv, win_x: int, win_y: int, win_w: int, win_h: int, offset_x: int, offset_y: int, global_dynamic_mask=None) -> tuple[bool, dict]:
    import cv2
    import numpy as np
    
    scores = {"diff_ratio": 1.0, "sim": 0.0, "edge_sim": 1.0, "ssim": 0.0, "orb": 0.0}

    if not pre_image_path.exists():
        return False, scores

    pre_img_cv = cv2.imread(str(pre_image_path), cv2.IMREAD_GRAYSCALE)
    if pre_img_cv is None:
        return False, scores

    img_h, img_w = pre_img_cv.shape
    margin = 8
    
    x1 = max(0, win_x - offset_x + margin)
    y1 = max(0, win_y - offset_y + margin)
    x2 = min(img_w, win_x - offset_x + win_w - margin)
    y2 = min(img_h, win_y - offset_y + win_h - margin)
    
    if x2 <= x1 or y2 <= y1:
        x1, y1 = max(0, win_x + margin), max(0, win_y + margin)
        x2, y2 = min(img_w, win_x + win_w - margin), min(img_h, win_y + win_h - margin)
        if x2 <= x1 or y2 <= y1:
            return False, scores
            
    pre_crop = pre_img_cv[y1:y2, x1:x2]
    curr_h, curr_w = curr_img_cv.shape
    cx1 = max(0, win_x - offset_x + margin)
    cy1 = max(0, win_y - offset_y + margin)
    cx2 = min(curr_w, win_x - offset_x + win_w - margin)
    cy2 = min(curr_h, win_y - offset_y + win_h - margin)
    
    if cx2 <= cx1 or cy2 <= cy1:
        cx1, cy1 = max(0, win_x + margin), max(0, win_y + margin)
        cx2, cy2 = min(curr_w, win_x + win_w - margin), min(curr_h, win_y + win_h - margin)
        if cx2 <= cx1 or cy2 <= cy1:
            return False, scores
            
    curr_crop = curr_img_cv[cy1:cy2, cx1:cx2]
    
    scale = min(1.0, 512.0 / max(pre_crop.shape[0], pre_crop.shape[1]))
    if scale < 1.0:
        pre_crop_eval = cv2.resize(pre_crop, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        curr_crop_eval = cv2.resize(curr_crop, (pre_crop_eval.shape[1], pre_crop_eval.shape[0]), interpolation=cv2.INTER_AREA)
    else:
        pre_crop_eval = pre_crop.copy()
        if pre_crop.shape != curr_crop.shape:
            curr_crop_eval = cv2.resize(curr_crop, (pre_crop.shape[1], pre_crop.shape[0]), interpolation=cv2.INTER_AREA)
        else:
            curr_crop_eval = curr_crop.copy()

    # --- 動画などの動的領域マスクの適用 ---
    static_mask = np.ones_like(curr_crop_eval, dtype=np.uint8) * 255
    if global_dynamic_mask is not None:
        dynamic_crop = global_dynamic_mask[cy1:cy2, cx1:cx2]
        if scale < 1.0:
            dynamic_crop_eval = cv2.resize(dynamic_crop, (curr_crop_eval.shape[1], curr_crop_eval.shape[0]), interpolation=cv2.INTER_NEAREST)
        else:
            dynamic_crop_eval = dynamic_crop.copy()
            if dynamic_crop.shape != curr_crop_eval.shape:
                dynamic_crop_eval = cv2.resize(dynamic_crop, (curr_crop_eval.shape[1], curr_crop_eval.shape[0]), interpolation=cv2.INTER_NEAREST)
        
        curr_crop_eval[dynamic_crop_eval == 255] = 0
        pre_crop_eval[dynamic_crop_eval == 255] = 0
        static_mask[dynamic_crop_eval == 255] = 0
    # ------------------------------------

    # --- システムウィンドウのマスク処理 ---
    system_rects = _get_system_window_rects()
    for (sl, st, sr, sb) in system_rects:
        abs_cx1 = offset_x + cx1
        abs_cy1 = offset_y + cy1
        ml = max(0, int((sl - abs_cx1) * scale))
        mt = max(0, int((st - abs_cy1) * scale))
        mr = min(curr_crop_eval.shape[1], int((sr - abs_cx1) * scale))
        mb = min(curr_crop_eval.shape[0], int((sb - abs_cy1) * scale))
        if mr > ml and mb > mt:
            curr_crop_eval[mt:mb, ml:mr] = 0
            pre_crop_eval[mt:mb, ml:mr] = 0
            static_mask[mt:mb, ml:mr] = 0
    # ------------------------------------

    # --- テキスト領域のマスク処理（文字の違いや背景色変化による不一致を防ぐ） ---
    diff_for_mask = cv2.absdiff(pre_crop_eval, curr_crop_eval)
    _, diff_thresh = cv2.threshold(diff_for_mask, 30, 255, cv2.THRESH_BINARY)

    kernel_text = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    diff_dilated = cv2.dilate(diff_thresh, kernel_text, iterations=1)

    text_mask = np.zeros_like(curr_crop_eval, dtype=np.uint8)
    contours, _ = cv2.findContours(diff_dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if 2 <= w < curr_crop_eval.shape[1] * 0.8 and 5 <= h < curr_crop_eval.shape[0] * 0.5:
            region_curr = curr_crop_eval[y:y+h, x:x+w]
            region_pre = pre_crop_eval[y:y+h, x:x+w]
            region_diff = diff_thresh[y:y+h, x:x+w]

            edges_curr = cv2.Canny(region_curr, 50, 150)
            edges_pre = cv2.Canny(region_pre, 50, 150)

            valid_edges_curr = cv2.bitwise_and(edges_curr, edges_curr, mask=region_diff)
            valid_edges_pre = cv2.bitwise_and(edges_pre, edges_pre, mask=region_diff)

            area = w * h
            edge_count = max(np.count_nonzero(valid_edges_curr), np.count_nonzero(valid_edges_pre))
            edge_density = edge_count / area if area > 0 else 0
            if edge_density > 0.05:
                cv2.rectangle(text_mask, (x, y), (x+w, y+h), 255, -1)

    curr_crop_eval[text_mask == 255] = 0
    pre_crop_eval[text_mask == 255] = 0
    static_mask[text_mask == 255] = 0
    # ------------------------------------

    # 1. ピクセル差分の計算
    diff_full = cv2.absdiff(pre_crop_eval, curr_crop_eval)
    _, thresh_full = cv2.threshold(diff_full, 30, 255, cv2.THRESH_BINARY)
    
    valid_area = np.count_nonzero(static_mask)
    if valid_area > 500:
        diff_ratio = np.count_nonzero(thresh_full) / valid_area
    else:
        diff_ratio = np.count_nonzero(thresh_full) / (pre_crop_eval.shape[0] * pre_crop_eval.shape[1])
    
    # 2. エッジの比較
    pre_edges = cv2.Canny(pre_crop_eval, 50, 150)
    curr_edges = cv2.Canny(curr_crop_eval, 50, 150)
    
    edge_diff = cv2.absdiff(pre_edges, curr_edges)
    if valid_area > 500:
        edge_diff_ratio = np.count_nonzero(cv2.bitwise_and(edge_diff, edge_diff, mask=static_mask)) / valid_area
    else:
        edge_diff_ratio = np.count_nonzero(edge_diff) / (pre_crop_eval.shape[0] * pre_crop_eval.shape[1])
    
    # 3. テンプレートマッチング
    res = cv2.matchTemplate(curr_crop_eval, pre_crop_eval, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)

    ssim_val = _calculate_ssim(pre_crop_eval, curr_crop_eval, mask=static_mask)
    orb_score = _calculate_orb_match(pre_crop_eval, curr_crop_eval, mask=static_mask)

    scores = {
        "diff_ratio": float(diff_ratio),
        "sim": float(max_val),
        "edge_sim": float(edge_diff_ratio),
        "ssim": ssim_val,
        "orb": orb_score
    }

    # 完全に同じ画面
    is_exact_match = (diff_ratio <= 0.05) and (edge_diff_ratio <= 0.03) and (max_val >= 0.92)
    
    # ほぼ同じ画面（少しのノイズやカーソルの点滅、広告の変化などを許容）
    is_high_match = (diff_ratio <= 0.15) and (edge_diff_ratio <= 0.10) and (max_val >= 0.85)

     # 構造的・特徴的な一致（広告やサジェストでピクセル差分が大きくても、基本UIが同じなら一致とする）
    is_structural_match = (ssim_val >= 0.75) or (orb_score >= 0.15) or (orb_score >= 0.08 and max_val >= 0.6)
    
    return (is_exact_match or is_high_match or is_structural_match), scores

def run_workflow(workflow_id: str, config: AppConfig, status_callback=None):
    global _is_running, _stop_requested, _browser_activated_once
    _is_running = True
    _stop_requested = False
    _browser_activated_once = False
    
    logger.info(f"[{workflow_id}] Starting executable macro execution...")
    mouse = MouseController()
    keyboard = KeyboardController()

    _pressed_keys_for_stop = set()

    def on_press(key):
        try:
            key_name = ""
            if hasattr(key, 'char') and key.char is not None:
                key_name = str(key.char).lower()
            else:
                key_name = str(key).replace("Key.", "").lower()

            _pressed_keys_for_stop.add(key_name)

            has_ctrl = any(k in {"ctrl", "ctrl_l", "ctrl_r"} for k in _pressed_keys_for_stop)
            vk = getattr(key, 'vk', None)
            is_backslash = key_name in {"\\", "¥", "yen", "\x1c"} or vk in {220, 226}
            
            if has_ctrl and is_backslash:
                logger.warning("Emergency stop shortcut (Ctrl+\\) triggered.")
                stop_workflow()
                return False
        except Exception:
            pass

    def on_release(key):
        try:
            key_name = ""
            if hasattr(key, 'char') and key.char is not None:
                key_name = str(key.char).lower()
            else:
                key_name = str(key).replace("Key.", "").lower()
            _pressed_keys_for_stop.discard(key_name)
        except Exception:
            pass

    listener = KeyboardListener(on_press=on_press, on_release=on_release)
    listener.start()
    
    def update_ui(text, is_warning=False):
        if status_callback:
            status_callback(text, is_warning)
        else:
            try:
                from ui.views.running_dialog import RunningDialog
                RunningDialog.set_status(text, is_warning)
            except Exception:
                pass

    try:
        from core.recorder.screen_capturer import get_macros_root
        from datetime import datetime
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id
        
        execution_log = {
            "workflow_id": workflow_id,
            "start_time": datetime.now().isoformat(),
            "start_step": 0,
            "steps": [],
            "status": "running"
        }
        
        executable_macro_path = target_dir / "executable_macro.json"
        variables_path = target_dir / "variables.json"
        
        if not executable_macro_path.exists():
            raise FileNotFoundError(f"Missing executable_macro.json in {target_dir}")
            
        with open(executable_macro_path, 'r', encoding='utf-8') as f:
            macro_data = json.load(f)

        variables = {}
        if variables_path.exists():
            try:
                with open(variables_path, 'r', encoding='utf-8') as f:
                    variables = json.load(f)
            except Exception as e:
                logger.warning(f"[{workflow_id}] Failed to load variables.json: {e}")
            
        commands = macro_data.get("commands", [])
        macro_needs_save = False
        
        # --- スマートレジューム（途中からの実行）の判定 ---
        start_index = 0
        screen_matched = False
        is_browser_target = False
        force_skip_match_until_enter = False
        current_win_x, current_win_y, current_win_w, current_win_h = 0, 0, 0, 0
        last_win_args = None
        
        try:
            first_activate_cmd = next((cmd for cmd in commands if cmd.get("method") == "activate_window"), None)
            if first_activate_cmd:
                args = first_activate_cmd.get("args", {})
                window_title = args.get("window_title", "")
                app_name = window_title.split("—")[-1].split("-")[-1].strip().lower()
                is_browser_target = any(b in app_name for b in ["firefox", "chrome", "edge", "brave", "opera"])
                
                _activate_and_restore_window(
                    window_title,
                    args.get("x", 0),
                    args.get("y", 0),
                    args.get("width", 0),
                    args.get("height", 0),
                    keyboard,
                    workflow_id,
                    args.get("launch_cmd", "")
                )
                time.sleep(1.0)
                
                import cv2
                import numpy as np
                from core.recorder.screen_capturer import take_screenshot
                
                # スマートレジュームのための初期フレームバッファリング（動画領域の特定）
                logger.info(f"[{workflow_id}] Buffering initial frames to detect dynamic regions (e.g., videos)...")
                initial_frames = []
                for _ in range(5):
                    img_pil, curr_monitor = take_screenshot()
                    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2GRAY)
                    initial_frames.append(img_cv)
                    time.sleep(0.1)
                
                curr_img_cv = initial_frames[-1]
                offset_x = curr_monitor.get("left", 0) if isinstance(curr_monitor, dict) else 0
                offset_y = curr_monitor.get("top", 0) if isinstance(curr_monitor, dict) else 0

                # 画面全体の動的マスクを生成
                std_dev_global = np.std(initial_frames, axis=0)
                # 膨張(dilate)を行わず、本当に変化が激しいピクセルのみを厳密にマスクする
                global_dynamic_mask = (std_dev_global > 20).astype(np.uint8) * 255
                
                # 静的なエッジ（UIの枠線や文字など）を保護する
                edges_list = [cv2.Canny(f, 50, 150) for f in initial_frames]
                static_edges = edges_list[0]
                for e in edges_list[1:]:
                    static_edges = cv2.bitwise_and(static_edges, e)
                
                kernel_protect = np.ones((3, 3), np.uint8)
                static_edges_dilated = cv2.dilate(static_edges, kernel_protect, iterations=1)
                
                # 静的なUI構造部分はマスクしない
                global_dynamic_mask[static_edges_dilated == 255] = 0
                
                # 動的マスクが広すぎる場合は破棄する
                if np.count_nonzero(global_dynamic_mask) / global_dynamic_mask.size > 0.3:
                    global_dynamic_mask = np.zeros_like(global_dynamic_mask)

                last_win_args = args
                for i, cmd in enumerate(commands):
                    if cmd.get("method") == "activate_window":
                        last_win_args = cmd.get("args", {})
                    
                    raw_event_id = cmd.get("args", {}).get("raw_event_id")
                    if raw_event_id and last_win_args:
                        pre_image_path = target_dir / "images" / f"{raw_event_id}_pre.png"
                        if pre_image_path.exists():
                            win_x = last_win_args.get("x", 0)
                            win_y = last_win_args.get("y", 0)
                            win_w = last_win_args.get("width", 0)
                            win_h = last_win_args.get("height", 0)
                            
                            is_match, scores = _is_screen_match(pre_image_path, curr_img_cv, win_x, win_y, win_w, win_h, offset_x, offset_y, global_dynamic_mask)
                            if is_match:
                                logger.info(f"[{workflow_id}] Current screen matches step {i+1} (event: {raw_event_id}). Starting from here. Scores: {scores}")
                                start_index = i
                                screen_matched = True
                                execution_log["start_step"] = start_index
                                execution_log["initial_match_scores"] = scores
                                break
                                
            if screen_matched and start_index > 0:
                # 実行開始位置より前にある最後の activate_window を適用しておく
                last_activation = None
                for j in range(start_index):
                    if commands[j].get("method") == "activate_window":
                        last_activation = commands[j]
                
                if last_activation:
                    args = last_activation.get("args", {})
                    _activate_and_restore_window(
                        args.get("window_title", ""),
                        args.get("x", 0),
                        args.get("y", 0),
                        args.get("width", 0),
                        args.get("height", 0),
                        keyboard,
                        workflow_id,
                        args.get("launch_cmd", "")
                    )
                    time.sleep(0.5)
            elif not screen_matched and is_browser_target:
                # どのスクリーンショットとも一致せず、かつブラウザが対象の場合のみ新規タブを開く
                logger.info(f"[{workflow_id}] Screen did not match any recorded steps. Opening new tab for fresh browser search.")
                keyboard.press(Key.ctrl)
                keyboard.press('t')
                keyboard.release('t')
                keyboard.release(Key.ctrl)
                time.sleep(0.5)
                force_skip_match_until_enter = True
                
        except Exception as e:
            logger.warning(f"[{workflow_id}] Failed to determine start step by screen match: {e}")
            
        if last_win_args:
            current_win_x = last_win_args.get("x", 0)
            current_win_y = last_win_args.get("y", 0)
            current_win_w = last_win_args.get("width", 0)
            current_win_h = last_win_args.get("height", 0)
        # --------------------------------------------------
        
        i = start_index
        loop_stack = []
        
        while i < len(commands):
            cmd = commands[i]
            if _stop_requested:
                logger.warning(f"[{workflow_id}] Execution aborted by user emergency stop.")
                break
                
            method = cmd.get("method")
            args = cmd.get("args", {}).copy()
            
            if loop_stack and method in ["click", "move", "activate_window"]:
                current_loop = loop_stack[-1]
                iteration = current_loop["current_iteration"]
                variables = current_loop["variables"]
                
                if "y_offset" in variables and "y" in args:
                    args["y"] += variables["y_offset"] * iteration
                if "x_offset" in variables and "x" in args:
                    args["x"] += variables["x_offset"] * iteration
            
            step_log = {
                "step_index": i,
                "method": method,
                "args": args,
                "match_info": None,
                "recovery_info": None,
                "timestamp": datetime.now().isoformat()
            }
            
            step_msg = f"Step {i+1}/{len(commands)}: {method}"
            if loop_stack:
                step_msg += f" (Loop {loop_stack[-1]['current_iteration']+1}/{loop_stack[-1]['total_count']})"
            logger.info(f"[{workflow_id}] {step_msg}")
            update_ui(step_msg, False)
            
            raw_event_id = args.get("raw_event_id")
            target_id = args.get("target_id")

            if method == "loop_start":
                loop_count = args.get("loop_count", 10)
                loop_variables = args.get("loop_variables", {})
                loop_stack.append({
                    "start_index": i,
                    "total_count": loop_count,
                    "current_iteration": 0,
                    "variables": loop_variables
                })
                i += 1
                continue
                
            elif method == "loop_end":
                if loop_stack:
                    current_loop = loop_stack[-1]
                    current_loop["current_iteration"] += 1
                    if current_loop["current_iteration"] < current_loop["total_count"]:
                        i = current_loop["start_index"] + 1
                        continue
                    else:
                        loop_stack.pop()
                i += 1
                continue

            if method == "activate_window":
                current_win_x = args.get("x", 0)
                current_win_y = args.get("y", 0)
                current_win_w = args.get("width", 0)
                current_win_h = args.get("height", 0)

            # 次のアクション時の画面との一致率で待機する
            if method not in ["wait", "activate_window", "loop_start", "loop_end"] and raw_event_id:
                if force_skip_match_until_enter:
                    logger.info(f"[{workflow_id}] Skipping screen match for fresh browser search.")
                    if method == "press_key" and args.get("key") == "enter":
                        force_skip_match_until_enter = False
                elif loop_stack:
                    # ループ中は画面比較を一時的になしにする（ループ直前と最後のみ行う）
                    logger.info(f"[{workflow_id}] Skipping screen match inside loop.")
                    time.sleep(0.5)
                else:
                    # タイムアウトを10秒に短縮し、画面が多少異なっても進行を妨げないようにする
                    match_info = _wait_for_screen_match(target_dir, raw_event_id, current_win_x, current_win_y, current_win_w, current_win_h, workflow_id, status_callback, i, timeout=10.0)
                    step_log["match_info"] = match_info
                    update_ui(step_msg, False) # 待機から復帰した後に再度ステップ表示を更新
            
            if raw_event_id and target_id and method in ["click", "move"]:
                needs_recovery = False
                crop_image_path = target_dir / "images" / f"{raw_event_id}_crop.png"
                
                try:
                    from core.recorder.screen_capturer import take_screenshot
                    from core.healer.recovery_manager import attempt_recovery
                    import cv2
                    import numpy as np

                    current_img_pil, _ = take_screenshot()

                    if crop_image_path.exists():
                        current_img_cv = cv2.cvtColor(np.array(current_img_pil), cv2.COLOR_RGB2BGR)
                        template_cv = cv2.imread(str(crop_image_path), cv2.IMREAD_COLOR)

                        if template_cv is not None:
                            res = cv2.matchTemplate(current_img_cv, template_cv, cv2.TM_CCOEFF_NORMED)
                            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                            
                            if max_val < 0.8:
                                logger.warning(f"[{workflow_id}] Template match failed (Confidence: {max_val:.2f}). Initiating Healer...")
                                needs_recovery = True
                            else:
                                match_center_x = max_loc[0] + template_cv.shape[1] // 2
                                match_center_y = max_loc[1] + template_cv.shape[0] // 2
                                
                                expected_x = args.get("x", 0)
                                expected_y = args.get("y", 0)
                                
                                dist = ((match_center_x - expected_x)**2 + (match_center_y - expected_y)**2)**0.5
                                
                                if dist > 20:
                                    logger.warning(f"[{workflow_id}] Target UI drifted by {dist:.1f} pixels. Initiating Healer...")
                                    needs_recovery = True
                        else:
                            needs_recovery = True
                    else:
                        logger.warning(f"[{workflow_id}] No crop image available. Initiating Healer...")
                        needs_recovery = True

                    if needs_recovery:
                        logger.warning(f"[{workflow_id}] Healer is disabled temporarily. Bypassing recovery and continuing.")
                        needs_recovery = False

                    if needs_recovery:
                        if status_callback:
                            status_callback("自己修復中...", True)
                            
                        recovery_result = attempt_recovery(workflow_id, target_id)
                        
                        step_log["recovery_info"] = recovery_result
                        if recovery_result.get("success"):
                            new_coords = recovery_result.get("new_coordinates")
                            if new_coords:
                                args["x"] = new_coords["x"]
                                args["y"] = new_coords["y"]
                                logger.info(f"[{workflow_id}] Healer successfully updated coordinates to ({args['x']}, {args['y']}).")
                                macro_needs_save = True
                        else:
                            logger.error(f"[{workflow_id}] Healer failed to recover target '{target_id}'. Aborting execution.")
                            if status_callback:
                                status_callback("実行中...", False)
                            execution_log["status"] = "failed"
                            execution_log["error"] = "Healer failed to recover target"
                            raise RuntimeError("対象のUIが見つからず、自己修復にも失敗したためマクロを安全停止しました。")
                        
                        if status_callback:
                            status_callback("実行中...", False)
                            
                except Exception as e:
                    logger.error(f"[{workflow_id}] Error during image validation/recovery: {e}")
                    if status_callback:
                        status_callback("実行中...", False)
                    if "安全のため" in str(e):
                        raise e
            
            if method == "wait":
                # 画面マッチングによる待機を優先するため、次に画像判定可能なアクションが控えている場合は固定待機をスキップ
                next_has_event = False
                for j in range(i + 1, len(commands)):
                    if commands[j].get("method") not in ["wait", "activate_window", "loop_start", "loop_end"]:
                        if commands[j].get("args", {}).get("raw_event_id"):
                            next_has_event = True
                        break
                
                if next_has_event:
                    logger.info(f"[{workflow_id}] Skipping fixed wait in favor of screen matching for the next action.")
                    i += 1
                    continue

                duration = args.get("duration", 0.0)
                sleep_intervals = int(duration * 10)
                for _ in range(sleep_intervals):
                    if _stop_requested:
                        break
                    time.sleep(0.1)
                remainder = duration - (sleep_intervals * 0.1)
                if remainder > 0 and not _stop_requested:
                    time.sleep(remainder)
                    
            elif method == "activate_window":
                last_win_args = args
                window_title = args.get("window_title", "")
                win_x = args.get("x", 0)
                win_y = args.get("y", 0)
                win_w = args.get("width", 0)
                win_h = args.get("height", 0)
                launch_cmd = args.get("launch_cmd", "")
                
                _activate_and_restore_window(window_title, win_x, win_y, win_w, win_h, keyboard, workflow_id, launch_cmd)

            elif method in ["click", "move", "scroll", "type_text", "press_key"]:
                # 操作アクションの直前に、対象ウィンドウが最前面にあるか確認し、違えばアクティブにする
                if last_win_args:
                    window_title = last_win_args.get("window_title", "")
                    app_name = window_title.split("—")[-1].split("-")[-1].strip()
                    if app_name and platform.system() == "Windows":
                        hwnd = ctypes.windll.user32.GetForegroundWindow()
                        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                        buff = ctypes.create_unicode_buffer(length + 1)
                        ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                        current_fg_title = buff.value
                        
                        if app_name.lower() not in current_fg_title.lower():
                            logger.info(f"[{workflow_id}] Window '{app_name}' is not in foreground. Activating...")
                            _activate_and_restore_window(
                                window_title,
                                last_win_args.get("x", 0),
                                last_win_args.get("y", 0),
                                last_win_args.get("width", 0),
                                last_win_args.get("height", 0),
                                keyboard,
                                workflow_id,
                                last_win_args.get("launch_cmd", "")
                            )

                if method == "click":
                    x = args.get("x", 0)
                    y = args.get("y", 0)
                    button_str = args.get("button", "left")
                    clicks = args.get("clicks", 1)
                    excel_dest_cell = args.get("excel_dest_cell")
                    
                    if excel_dest_cell and platform.system() == "Windows":
                        try:
                            import win32com.client
                            excel = win32com.client.GetActiveObject("Excel.Application")
                            sheet = excel.ActiveSheet
                            sheet.Range(excel_dest_cell).Select()
                            time.sleep(0.1)
                        except Exception as e:
                            logger.warning(f"[{workflow_id}] Failed to select Excel dest cell {excel_dest_cell}: {e}")
                    
                    btn = Button.right if button_str == "right" else Button.middle if button_str == "middle" else Button.left
                    
                    if platform.system() == "Windows":
                        ctypes.windll.user32.SetCursorPos(int(x), int(y))
                    else:
                        mouse.position = (x, y)
                        
                    time.sleep(0.05)
                    mouse.click(btn, clicks)

                elif method == "move":
                    x = args.get("x", 0)
                    y = args.get("y", 0)
                    excel_dest_cell = args.get("excel_dest_cell")
                    
                    if excel_dest_cell and platform.system() == "Windows":
                        try:
                            import win32com.client
                            excel = win32com.client.GetActiveObject("Excel.Application")
                            sheet = excel.ActiveSheet
                            sheet.Range(excel_dest_cell).Select()
                            time.sleep(0.1)
                        except Exception as e:
                            logger.warning(f"[{workflow_id}] Failed to select Excel dest cell {excel_dest_cell}: {e}")
                    
                    if platform.system() == "Windows":
                        ctypes.windll.user32.SetCursorPos(int(x), int(y))
                    else:
                        mouse.position = (x, y)
                        
                    time.sleep(0.5)
                    
                elif method == "scroll":
                    dx = args.get("dx", 0.0)
                    dy = args.get("dy", 0.0)
                    x = args.get("x")
                    y = args.get("y")
                    
                    if x is not None and y is not None and (x != 0 or y != 0):
                        if platform.system() == "Windows":
                            ctypes.windll.user32.SetCursorPos(int(x), int(y))
                        else:
                            mouse.position = (x, y)
                        time.sleep(0.01)
                    
                    if platform.system() == "Windows":
                        if dy != 0.0:
                            scroll_amount = int(dy * WHEEL_DELTA)
                            ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, scroll_amount, 0)
                        if dx != 0.0:
                            scroll_amount_x = int(dx * WHEEL_DELTA)
                            ctypes.windll.user32.mouse_event(MOUSEEVENTF_HWHEEL, 0, 0, scroll_amount_x, 0)
                    else:
                        mouse.scroll(dx, dy)
                        
                elif method == "type_text":
                    text = args.get("text", "")
                    seq_val = args.get("sequence_value")
                    excel_cell = args.get("excel_cell")
                    
                    if seq_val and loop_stack:
                        current_loop = loop_stack[-1]
                        iteration = current_loop["current_iteration"]
                        start_val = seq_val.get("start", 1)
                        step_val = seq_val.get("step", 1)
                        text = str(start_val + step_val * iteration)
                        
                    if text:
                        for key, val in variables.items():
                            placeholder = f"{{{{{key}}}}}"
                            if placeholder in text:
                                text = text.replace(placeholder, str(val))
                                
                        if excel_cell and platform.system() == "Windows":
                            try:
                                import win32com.client
                                excel = win32com.client.GetActiveObject("Excel.Application")
                                sheet = excel.ActiveSheet
                                sheet.Range(excel_cell).Select()
                                time.sleep(0.1)
                            except Exception as e:
                                logger.warning(f"[{workflow_id}] Failed to select Excel cell {excel_cell}: {e}")
                                
                        _set_ime_state(text)
                        for char in text:
                            if _stop_requested:
                                break
                            keyboard.type(char)
                            time.sleep(0.03)
                        time.sleep(0.2)
                        
                elif method == "press_key":
                    key_str = args.get("key", "")
                    if key_str:
                        try:
                            if "+" in key_str:
                                keys = key_str.split("+")
                                pressed = []
                                for k in keys:
                                    k_name = k.lower().replace("key.", "")
                                    if k_name in ["win", "windows"]: k_name = "cmd"
                                    key_obj = getattr(Key, k_name, k_name)
                                    keyboard.press(key_obj)
                                    pressed.append(key_obj)
                                for key_obj in reversed(pressed):
                                    keyboard.release(key_obj)
                            else:
                                key_name = key_str.lower()
                                if key_name in ["win", "windows"]:
                                    key_name = "cmd"
                                    
                                if hasattr(Key, key_name):
                                    special_key = getattr(Key, key_name)
                                    keyboard.press(special_key)
                                    keyboard.release(special_key)
                                else:
                                    keyboard.press(key_str)
                                    keyboard.release(key_str)
                        except Exception as e:
                            logger.warning(f"Failed to press key {key_str}: {e}")
            else:
                logger.warning(f"Unknown method: {method}")
                
            execution_log["steps"].append(step_log)
            i += 1
                
        if not _stop_requested:
            if macro_needs_save:
                try:
                    with open(executable_macro_path, 'w', encoding='utf-8') as f:
                        json.dump(macro_data, f, indent=4, ensure_ascii=False)
                    logger.info(f"[{workflow_id}] Successfully saved healed coordinates to executable_macro.json for future runs.")
                except Exception as e:
                    logger.error(f"[{workflow_id}] Failed to save healed macro to file: {e}")

            execution_log["status"] = "success"
            logger.info(f"[{workflow_id}] Macro execution finished successfully.")
        else:
            execution_log["status"] = "stopped"
        
    except Exception as e:
        execution_log["status"] = "failed"
        execution_log["error"] = str(e)
        logger.error(f"[{workflow_id}] Execution failed: {e}")
        raise
    finally:
        try:
            execution_log["end_time"] = datetime.now().isoformat()
            log_dir = target_dir / "execution_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"run_log_{int(time.time())}.json"
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump(execution_log, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save execution log: {e}")
            
        listener.stop()
        _is_running = False
        _stop_requested = False

def stop_workflow():
    global _stop_requested
    _stop_requested = True
    logger.warning("Emergency stop signal activated by user.")