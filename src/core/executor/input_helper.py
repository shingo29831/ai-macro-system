"""Module: @role: 実行エンジン用の低レベルUnicode入力・クリップボード転記・マウス移動・画面安定待機および変数展開を担う。"""

import ctypes
import logging
import platform
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from pynput.mouse import Controller as MouseController

logger = logging.getLogger(__name__)


def send_unicode_string(text: str) -> None:
    # Why: クリップボードやIMEに依存せずあらゆるUnicode文字を直接確実に入力
    if platform.system() != "Windows" or not text:
        return
    try:
        from ctypes import wintypes
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]
        class INPUT_UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]
        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]

        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_UNICODE = 0x0004
        user32 = ctypes.windll.user32

        for ch in text:
            code = ord(ch)
            inp_down = INPUT()
            inp_down.type = 1
            inp_down.union.ki.wVk = 0
            inp_down.union.ki.wScan = code
            inp_down.union.ki.dwFlags = KEYEVENTF_UNICODE

            inp_up = INPUT()
            inp_up.type = 1
            inp_up.union.ki.wVk = 0
            inp_up.union.ki.wScan = code
            inp_up.union.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP

            inputs = (INPUT * 2)(inp_down, inp_up)
            user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
            time.sleep(0.01)
    except Exception as e:
        logger.warning(f"SendInput UNICODE failed: {e}")


def set_clipboard_text(text: str) -> bool:
    # Why: 64bit完全対応の型定義とCOM初期化によりクリップボード転記エラーを根絶
    if platform.system() != "Windows":
        return False
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.restype = wintypes.BOOL

        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL

        for _ in range(10):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.03)
        else:
            raise TimeoutError("OpenClipboard failed")

        user32.EmptyClipboard()
        data_bytes = (text + "\0").encode("utf-16le")
        h_mem = kernel32.GlobalAlloc(0x0002, len(data_bytes))
        if not h_mem:
            user32.CloseClipboard()
            raise MemoryError("GlobalAlloc failed")

        p_mem = kernel32.GlobalLock(h_mem)
        if not p_mem:
            kernel32.GlobalFree(h_mem)
            user32.CloseClipboard()
            raise MemoryError("GlobalLock failed")

        ctypes.memmove(p_mem, data_bytes, len(data_bytes))
        kernel32.GlobalUnlock(h_mem)
        user32.SetClipboardData(13, h_mem)
        user32.CloseClipboard()
        return True
    except Exception as e:
        logger.warning(f"WinAPI SetClipboardData failed ({e})")
        return False


def resolve_variables(data: Any, variables: Dict[str, Any]) -> Any:
    # Why: 空白混在({{ A }})や日本語列名、大文字小文字の差異を包括的に吸収して展開
    if not variables:
        return data
    if isinstance(data, str):
        res = data
        for k, v in variables.items():
            res = res.replace(f"{{{{{k}}}}}", str(v)).replace(f"${{{k}}}", str(v))
        var_lower_map = {str(k).strip().lower(): v for k, v in variables.items()}
        pattern = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}|\$\{([^{}]+?)\}")

        def _get_nested_val(root_key: str, sub_path: List[str]) -> Any:
            cur = variables.get(root_key)
            if cur is None and root_key.lower() in var_lower_map:
                cur = var_lower_map[root_key.lower()]
            for p in sub_path:
                if isinstance(cur, dict):
                    val = cur.get(p)
                    if val is not None:
                        return val
                    val = cur.get(p.lower())
                    if val is not None:
                        return val
                    # Why: 「列A」「A列」等のUI表示名を正規の列名「A」へ相互解決
                    clean_col = re.sub(r"[列\s]", "", p).upper()
                    if clean_col and clean_col in cur:
                        return cur[clean_col]
                    if f"列{p}" in cur:
                        return cur[f"列{p}"]
                    if f"{p}列" in cur:
                        return cur[f"{p}列"]
                else:
                    return None
            return cur

        def _repl(match: re.Match) -> str:
            raw_key = (match.group(1) or match.group(2)).strip()
            if "." in raw_key:
                parts = raw_key.split(".")
                val = _get_nested_val(parts[0].strip(), [p.strip() for p in parts[1:]])
                if val is not None:
                    return str(val)
            if raw_key in variables:
                return str(variables[raw_key])
            if raw_key.lower() in var_lower_map:
                return str(var_lower_map[raw_key.lower()])
            # Why: ネストなし「{{列A}}」等も列文字「A」やrow辞書から包括解決
            clean_k = re.sub(r"[列\s]", "", raw_key).upper()
            if clean_k and clean_k in variables:
                return str(variables[clean_k])
            if "row" in variables and isinstance(variables["row"], dict):
                r_dict = variables["row"]
                if raw_key in r_dict:
                    return str(r_dict[raw_key])
                if clean_k and clean_k in r_dict:
                    return str(r_dict[clean_k])
            logger.warning(f"Unresolved macro variable detected: {match.group(0)}")
            return match.group(0)

        return pattern.sub(_repl, res)
    elif isinstance(data, dict):
        return {k: resolve_variables(v, variables) for k, v in data.items()}
    elif isinstance(data, list):
        return [resolve_variables(elem, variables) for elem in data]
    return data


def smooth_move(target_x: int, target_y: int, steps: int = 10, duration: float = 0.12) -> None:
    # Why: ホバー位置から子メニューへのワープ移動によるドロップダウン消滅を連続軌跡で完全防止
    if platform.system() == "Windows":
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        start_x, start_y = pt.x, pt.y
        dist = ((target_x - start_x) ** 2 + (target_y - start_y) ** 2) ** 0.5
        if dist < 5:
            ctypes.windll.user32.SetCursorPos(int(target_x), int(target_y))
            ctypes.windll.user32.mouse_event(0x0001, 0, 0, 0, 0)
            return

        actual_steps = max(4, min(steps, int(dist / 12)))
        step_delay = duration / actual_steps
        for s in range(1, actual_steps + 1):
            t = s / actual_steps
            ease_t = 1.0 - (1.0 - t) ** 2
            cx = int(start_x + (target_x - start_x) * ease_t)
            cy = int(start_y + (target_y - start_y) * ease_t)
            ctypes.windll.user32.SetCursorPos(cx, cy)
            ctypes.windll.user32.mouse_event(0x0001, 0, 0, 0, 0)
            time.sleep(step_delay)
        ctypes.windll.user32.SetCursorPos(int(target_x), int(target_y))
        ctypes.windll.user32.mouse_event(0x0001, 0, 0, 0, 0)
    else:
        mouse = MouseController()
        mouse.position = (target_x, target_y)


def get_window_offset(hwnd: int, rec_x: int, rec_y: int) -> Tuple[int, int]:
    # Why: 記録時と実行時のウィンドウ配置の差分をオフセットとして補正
    if not hwnd or platform.system() != "Windows":
        return 0, 0
    # Why: 記録時原点が未計測(0,0)の場合はオフセット誤加算による枠外ズレを防止
    if rec_x == 0 and rec_y == 0:
        return 0, 0
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect.left - rec_x, rect.top - rec_y
    except Exception:
        pass
    return 0, 0


def wait_for_screen_settle(timeout: float = 6.0, settle_threshold: float = 0.003) -> bool:
    # Why: スピナー回転やページロードが完了し画面が静止するまで動的適応待機
    from core.recorder.screen_capturer import take_screenshot
    start_t = time.time()
    last_img, _ = take_screenshot()
    time.sleep(0.15)
    while (time.time() - start_t) < timeout:
        curr_img, _ = take_screenshot()
        diff = (np.array(last_img) != np.array(curr_img)).mean()
        if diff <= settle_threshold:
            return True
        last_img = curr_img
        time.sleep(0.2)
    return False


def detect_unexpected_dialog(expected_hwnd: Optional[int] = None, auto_dismiss: bool = True) -> Tuple[bool, str]:
    # Why: エラーダイアログの割り込み検知時に自動でEscを送信しUIのフリーズ状態を脱出
    if platform.system() != "Windows":
        return False, ""
    try:
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not fg_hwnd or fg_hwnd == expected_hwnd:
            return False, ""
        cls_buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(fg_hwnd, cls_buf, 256)
        txt_len = ctypes.windll.user32.GetWindowTextLengthW(fg_hwnd)
        txt_buf = ctypes.create_unicode_buffer(txt_len + 1)
        ctypes.windll.user32.GetWindowTextW(fg_hwnd, txt_buf, txt_len + 1)
        title = txt_buf.value
        cls_name = cls_buf.value
        is_dialog = cls_name == "#32770" or any(kw in title for kw in ["エラー", "警告", "確認", "Notice", "Alert", "Error", "Warning"])
        if is_dialog:
            if auto_dismiss:
                ctypes.windll.user32.PostMessageW(fg_hwnd, 0x0100, 0x1B, 0)
                time.sleep(0.04)
                ctypes.windll.user32.PostMessageW(fg_hwnd, 0x0101, 0x1B, 0)
                time.sleep(0.15)
            return True, f"[{cls_name}] {title}"
    except Exception:
        pass
    return False, ""
