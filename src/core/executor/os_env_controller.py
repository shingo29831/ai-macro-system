# Role: OSプロセスのDPIスケーリング（DPI Awareness）およびIME入力モードの状態制御を担当する。

import platform
import ctypes
import time
import logging
import atexit
import struct
import tempfile
import threading
from pathlib import Path
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# =========================
# 設定定数
# =========================

WM_IME_CONTROL = 0x0283
IMC_SETOPENSTATUS = 0x0006
SPI_SETCURSORS = 0x0057
SPI_GETWHEELSCROLLLINES = 0x0068
SPI_GETWHEELSCROLLCHARS = 0x006C
WHEEL_PAGESCROLL = 0xFFFFFFFF
SPIF_UPDATEINIFILE = 0x0001
SPIF_SENDCHANGE = 0x0002


def get_wheel_scroll_settings() -> dict:
    """Why: Windows設定から1ノッチあたりのスクロール行数/文字数を取得し環境差による乖離を根絶"""
    if platform.system() != "Windows":
        return {"scroll_lines": 3, "scroll_chars": 3, "is_page_scroll": False}
    try:
        user32 = ctypes.windll.user32
        lines = ctypes.c_uint()
        chars = ctypes.c_uint()
        user32.SystemParametersInfoW(SPI_GETWHEELSCROLLLINES, 0, ctypes.byref(lines), 0)
        user32.SystemParametersInfoW(SPI_GETWHEELSCROLLCHARS, 0, ctypes.byref(chars), 0)
        is_page = (lines.value == WHEEL_PAGESCROLL)
        return {
            "scroll_lines": 3 if is_page else max(1, lines.value),
            "scroll_chars": max(1, chars.value),
            "is_page_scroll": is_page
        }
    except Exception:
        return {"scroll_lines": 3, "scroll_chars": 3, "is_page_scroll": False}

# Why: 待機時(WAIT)・起動時(APPSTARTING)・文字選択(IBEAM)等でのユーザーカーソルチラつきを完全排除
# Why: 通常矢印・リンク手・作業中を最適化置換し、フック遅延ゼロとチラつき解消を両立
RUN_RECORD_CURSOR_IDS = [
    32512,  # OCR_NORMAL
    32649,  # OCR_HAND
    32650,  # OCR_APPSTARTING
]

import queue

_current_cursor_mode: str = "default"
_cur_cache: dict[str, Path] = {}
_cursor_lock = threading.RLock()
_auto_revert_timer: threading.Timer | None = None
_cursor_queue: queue.Queue = queue.Queue()
_cursor_worker_thread: threading.Thread | None = None
_cursor_worker_lock = threading.Lock()


def _generate_cursor_image_and_hotspot(mode: str) -> tuple[Image.Image, tuple[int, int]]:
    # Why: 右下バッジを排除し文字やUI境界を覆い隠さないシャープなイベント別デザインを描画
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if mode in ("record_idle", "record"):
        # 通常移動: 赤枠の精密ポインタ矢印 (ホットスポット: 先端 0, 0)
        arrow = [(0, 0), (0, 18), (5, 14), (9, 22), (12, 21), (8, 13), (14, 13)]
        draw.polygon(arrow, fill=(255, 255, 255, 255), outline=(220, 38, 38, 255))
        draw.line([(0, 0), (0, 18)], fill=(185, 28, 28, 255), width=1)
        return img, (0, 0)

    elif mode in ("record_down", "click_down"):
        # クリックDOWN: 押下点を示すターゲットレティクル (ホットスポット: 中心 15, 15)
        cx, cy = 15, 15
        draw.ellipse([(cx - 8, cy - 8), (cx + 8, cy + 8)], outline=(220, 38, 38, 255), width=2)
        draw.line([(cx - 12, cy), (cx - 3, cy)], fill=(220, 38, 38, 255), width=2)
        draw.line([(cx + 3, cy), (cx + 12, cy)], fill=(220, 38, 38, 255), width=2)
        draw.line([(cx, cy - 12), (cx, cy - 3)], fill=(220, 38, 38, 255), width=2)
        draw.line([(cx, cy + 3), (cx, cy + 12)], fill=(220, 38, 38, 255), width=2)
        draw.ellipse([(cx - 1, cy - 1), (cx + 1, cy + 1)], fill=(255, 255, 255, 255))
        return img, (cx, cy)

    elif mode in ("record_drag", "drag"):
        # ドラッグ中: 掴み手（ホールド）アイコン (ホットスポット: 中心 15, 15)
        cx, cy = 15, 15
        draw.rounded_rectangle([(cx - 7, cy - 5), (cx + 7, cy + 8)], radius=3, fill=(245, 158, 11, 240), outline=(20, 20, 20, 255))
        draw.ellipse([(cx - 6, cy - 9), (cx - 2, cy - 4)], fill=(245, 158, 11, 240), outline=(20, 20, 20, 255))
        draw.ellipse([(cx - 2, cy - 10), (cx + 2, cy - 4)], fill=(245, 158, 11, 240), outline=(20, 20, 20, 255))
        draw.ellipse([(cx + 2, cy - 9), (cx + 6, cy - 4)], fill=(245, 158, 11, 240), outline=(20, 20, 20, 255))
        return img, (cx, cy)

    elif mode in ("record_hover", "hover"):
        # 一定時間停止（静止検知）: エメラルドグリーンの精密クロスヘア (ホットスポット: 15, 15)
        cx, cy = 15, 15
        draw.ellipse([(cx - 6, cy - 6), (cx + 6, cy + 6)], outline=(16, 185, 129, 255), width=1)
        draw.line([(cx - 11, cy), (cx - 2, cy)], fill=(16, 185, 129, 255), width=2)
        draw.line([(cx + 2, cy), (cx + 11, cy)], fill=(16, 185, 129, 255), width=2)
        draw.line([(cx, cy - 11), (cx, cy - 2)], fill=(16, 185, 129, 255), width=2)
        draw.line([(cx, cy + 2), (cx, cy + 11)], fill=(16, 185, 129, 255), width=2)
        return img, (cx, cy)

    elif mode in ("run_idle", "run"):
        # 実行中通常: 青枠の自動実行ポインタ矢印 (ホットスポット: 0, 0)
        arrow = [(0, 0), (0, 18), (5, 14), (9, 22), (12, 21), (8, 13), (14, 13)]
        draw.polygon(arrow, fill=(255, 255, 255, 255), outline=(37, 99, 235, 255))
        draw.line([(0, 0), (0, 18)], fill=(29, 78, 216, 255), width=1)
        return img, (0, 0)

    elif mode in ("run_down", "run_click"):
        # 実行クリック時: 青のターゲット照準 (ホットスポット: 15, 15)
        cx, cy = 15, 15
        draw.ellipse([(cx - 8, cy - 8), (cx + 8, cy + 8)], outline=(37, 99, 235, 255), width=2)
        draw.ellipse([(cx - 2, cy - 2), (cx + 2, cy + 2)], fill=(37, 99, 235, 255))
        return img, (cx, cy)

    # デフォルトフォールバック
    arrow = [(0, 0), (0, 17), (4, 13), (8, 20), (11, 19), (7, 12), (13, 12)]
    draw.polygon(arrow, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    return img, (0, 0)


def _get_or_create_cur_file(mode: str) -> Path:
    if mode in _cur_cache and _cur_cache[mode].exists():
        return _cur_cache[mode]

    temp_dir = Path(tempfile.gettempdir()) / "aimacro_cursors"
    temp_dir.mkdir(parents=True, exist_ok=True)
    file_path = temp_dir / f"cursor_{mode}.cur"

    img, (hx, hy) = _generate_cursor_image_and_hotspot(mode)
    width, height = 32, 32

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

    header = struct.pack("<IIIHHIIIIII", 40, width, height * 2, 1, 32, 0, len(xor_data) + len(and_mask), 0, 0, 0, 0)
    image_data = header + bytes(xor_data) + bytes(and_mask)
    icondir = struct.pack("<HHH", 0, 2, 1)
    direntry = struct.pack("<BBBBHHII", width, height, 0, 0, hx, hy, len(image_data), 22)

    with open(file_path, "wb") as f:
        f.write(icondir + direntry + image_data)

    _cur_cache[mode] = file_path
    return file_path


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


def is_fullwidth(text: str) -> bool:
    """文字列に全角文字が含まれているかを判定する"""
    import unicodedata
    return any(unicodedata.east_asian_width(c) in ('F', 'W', 'A') for c in text)


def should_input_as_halfwidth(text: str) -> bool:
    """テキストがリンク、URL、ドメイン、英数コード等、半角で入力すべきかを判定"""
    if is_link_or_url(text):
        return True
    import unicodedata
    has_fullwidth = any(unicodedata.east_asian_width(c) in ('F', 'W', 'A') for c in text)
    has_japanese = any('\u3040' <= c <= '\u309F' or '\u30A0' <= c <= '\u30FF' or '\u4E00' <= c <= '\u9FFF' for c in text)
    return not has_japanese and not has_fullwidth


def should_input_as_fullwidth(text: str) -> bool:
    """日本語文字（ひらがな、カタカナ、漢字）を含む全角文字入力であるかを判定"""
    return any('\u3040' <= c <= '\u309F' or '\u30A0' <= c <= '\u30FF' or '\u4E00' <= c <= '\u9FFF' for c in text)


def is_link_or_url(text: str) -> bool:
    """テキストがリンク（URL・ドメイン）であるかを判定する（全角・半角両対応）"""
    if not text:
        return False
    import unicodedata
    import re
    norm = unicodedata.normalize('NFKC', text).strip()
    norm_lower = norm.lower()
    if norm_lower.startswith(('http://', 'https://', 'ftp://', 'file://', 'www.')):
        return True
    domain_pattern = r'^[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z0-9][-a-zA-Z0-9.]*(/[^\s]*)?$'
    if bool(re.match(domain_pattern, norm_lower)):
        return True
    # Why: パス付きURLやパラメータ、全角混じりのWebアドレスを包括検知
    url_chars_pattern = r'^[a-zA-Z0-9\-._~:/?#\[\]@!$&\'()*+,;%=]+$'
    if ('.' in norm_lower or '/' in norm_lower) and bool(re.match(url_chars_pattern, norm_lower)):
        return True
    return False


def normalize_text_width(text: str) -> str:
    """Why: リンクや英数記号の全角混入を検知し正規の半角形式へ自動変換"""
    if not text:
        return ""
    import unicodedata
    if is_link_or_url(text):
        return unicodedata.normalize('NFKC', text).strip()
    norm = unicodedata.normalize('NFKC', text)
    if any(prefix in norm.lower() for prefix in ['http://', 'https://', 'www.', '.html', '.php', '.com', '.jp', '.ac.jp', '.co.jp', '.org']):
        return norm.strip()
    return text


def get_focused_hwnd() -> int:
    """フォアグラウンドウィンドウまたは現在フォーカスのある子コントロールのHWNDを取得"""
    if platform.system() != "Windows":
        return 0
    try:
        user32 = ctypes.windll.user32
        fg_hwnd = user32.GetForegroundWindow()
        if not fg_hwnd:
            return 0
        thread_id = user32.GetWindowThreadProcessId(fg_hwnd, None)

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("hwndActive", ctypes.c_void_p),
                ("hwndFocus", ctypes.c_void_p),
                ("hwndCapture", ctypes.c_void_p),
                ("hwndMenuOwner", ctypes.c_void_p),
                ("hwndMoveSize", ctypes.c_void_p),
                ("hwndCaret", ctypes.c_void_p),
                ("rcCaret", RECT)
            ]

        gui_info = GUITHREADINFO()
        gui_info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(thread_id, ctypes.byref(gui_info)) and gui_info.hwndFocus:
            return int(gui_info.hwndFocus)
        return fg_hwnd
    except Exception:
        return ctypes.windll.user32.GetForegroundWindow() if platform.system() == "Windows" else 0


def get_ime_state(hwnd: int = 0) -> bool:
    """対象ウィンドウのIMEがON(全角)かOFF(半角)かを判定する"""
    if platform.system() != "Windows":
        return False
    target_hwnd = hwnd or get_focused_hwnd()
    if not target_hwnd:
        return False
    try:
        imm32 = ctypes.windll.imm32
        himc = imm32.ImmGetContext(target_hwnd)
        if himc:
            status = imm32.ImmGetOpenStatus(himc) != 0
            imm32.ImmReleaseContext(target_hwnd, himc)
            return status

        default_ime = imm32.ImmGetDefaultIMEWnd(target_hwnd)
        if default_ime:
            res = ctypes.windll.user32.SendMessageW(default_ime, WM_IME_CONTROL, 0x0005, 0)
            return res != 0
    except Exception as e:
        logger.debug(f"Failed to get IME state: {e}")
    return False


def ensure_ime_state(target_state: bool, timeout: float = 0.5) -> bool:
    """Why: IMEの状況を監視し、指定された状態(全角:True, 半角:False)になるまで確実に制御"""
    if platform.system() != "Windows":
        return True
    start_t = time.time()

    while time.time() - start_t < timeout:
        hwnd = get_focused_hwnd()
        if get_ime_state(hwnd) == target_state:
            return True

        try:
            imm32 = ctypes.windll.imm32
            himc = imm32.ImmGetContext(hwnd)
            if himc:
                imm32.ImmSetOpenStatus(himc, 1 if target_state else 0)
                imm32.ImmReleaseContext(hwnd, himc)

            default_ime = imm32.ImmGetDefaultIMEWnd(hwnd)
            if default_ime:
                ctypes.windll.user32.SendMessageW(default_ime, WM_IME_CONTROL, IMC_SETOPENSTATUS, 1 if target_state else 0)
        except Exception:
            pass

        time.sleep(0.04)
        if get_ime_state(hwnd) == target_state:
            return True

    # タイムアウト時にキーボード送信による強制反転フォールバック
    hwnd = get_focused_hwnd()
    if get_ime_state(hwnd) != target_state:
        try:
            user32 = ctypes.windll.user32
            key_code = 0x1D if not target_state else 0x1C  # 無変換(0x1D) / 変換(0x1C)
            user32.keybd_event(key_code, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(key_code, 0, 2, 0)
            time.sleep(0.04)
        except Exception:
            pass

    return get_ime_state(hwnd) == target_state


def set_ime_state(text: str = "", target_state: bool | None = None):
    """アクティブウィンドウのIME状態（全角/半角）を制御する"""
    if platform.system() != "Windows":
        return
    if target_state is not None:
        ensure_ime_state(target_state, timeout=0.4)
    else:
        if is_link_or_url(text) or should_input_as_halfwidth(text):
            ensure_ime_state(False, timeout=0.4)
        elif should_input_as_fullwidth(text):
            ensure_ime_state(True, timeout=0.4)


def _schedule_auto_revert(target_idle_mode: str, delay: float = 0.25):
    # Why: クリック照準等が後半ずっと残留するバグを防止する安全タイマー
    global _auto_revert_timer
    with _cursor_lock:
        if _auto_revert_timer is not None:
            _auto_revert_timer.cancel()
        def _revert():
            set_system_cursor(target_idle_mode)
        _auto_revert_timer = threading.Timer(delay, _revert)
        _auto_revert_timer.daemon = True
        _auto_revert_timer.start()


def _force_cursor_update():
    # Why: mouse_event発行はフックの再入デッドロックを招くためSetCursorPosのみで安全に再描画
    if platform.system() != "Windows":
        return
    try:
        user32 = ctypes.windll.user32
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            user32.SetCursorPos(pt.x, pt.y)
    except Exception:
        pass


def restore_system_cursor(force: bool = True):
    """OSシステムカーソルを標準設定へ完全復元する（レジストリ・フォールバック網羅）"""
    global _current_cursor_mode, _auto_revert_timer
    if platform.system() != "Windows":
        return
    # Why: カーソルワーカースレッド滞留時もメインスレッドのデッドロックを防ぐタイムアウト付き排他制御
    acquired = _cursor_lock.acquire(timeout=1.0)
    try:
        while not _cursor_queue.empty():
            try:
                _cursor_queue.get_nowait()
            except Exception:
                break
        if _auto_revert_timer is not None:
            _auto_revert_timer.cancel()
            _auto_revert_timer = None
        if not force and _current_cursor_mode == "default":
            return
        try:
            user32 = ctypes.windll.user32
            # Why: SPIF_SENDCHANGEによる全窓ブロードキャスト同期ブロックをフラグ0で完全排除
            user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
            _force_cursor_update()
            _current_cursor_mode = "default"
        except Exception as e:
            logger.warning(f"Failed to restore system cursor: {e}")
    finally:
        if acquired:
            _cursor_lock.release()


def _apply_system_cursor_internal(mode: str):
    global _current_cursor_mode
    with _cursor_lock:
        if mode == _current_cursor_mode:
            return

        if mode == "default":
            restore_system_cursor(force=True)
            return

        try:
            cur_file = _get_or_create_cur_file(mode)
            user32 = ctypes.windll.user32
            for cid in RUN_RECORD_CURSOR_IDS:
                hcur = user32.LoadImageW(None, str(cur_file), 2, 0, 0, 0x0010)
                if hcur:
                    user32.SetSystemCursor(hcur, cid)

            _current_cursor_mode = mode
            _force_cursor_update()

            # Why: 一時的照準デザインが後半ずっと残留する問題を0.2秒タイマーで確実に防止
            if mode in ("run_down", "run_click"):
                _schedule_auto_revert("run_idle", delay=0.2)
            elif mode in ("record_down", "click_down"):
                _schedule_auto_revert("record_idle", delay=0.2)
            elif mode in ("record_hover", "hover"):
                _schedule_auto_revert("record_idle", delay=0.5)
        except Exception as e:
            logger.warning(f"Failed to set system cursor to {mode}: {e}")


def _cursor_update_worker():
    while True:
        try:
            mode = _cursor_queue.get()
            if mode is None:
                break
            # Why: 連続するカーソル更新要求を間引き最新の要求のみを適用
            while not _cursor_queue.empty():
                try:
                    mode = _cursor_queue.get_nowait()
                except queue.Empty:
                    break
            _apply_system_cursor_internal(mode)
        except Exception as e:
            logger.warning(f"Error in cursor update worker: {e}")


def _ensure_cursor_worker():
    global _cursor_worker_thread
    with _cursor_worker_lock:
        if _cursor_worker_thread is None or not _cursor_worker_thread.is_alive():
            _cursor_worker_thread = threading.Thread(target=_cursor_update_worker, name="CursorWorker", daemon=True)
            _cursor_worker_thread.start()


def set_system_cursor(mode: str = "default", async_exec: bool = False):
    """
    Why: 専用ワーカースレッドのキューへ委譲し、フックや実行スレッドのフリーズ・競合を完全防止
    """
    if platform.system() != "Windows":
        return

    _ensure_cursor_worker()
    _cursor_queue.put(mode)


# Why: 起動時に前回の異常終了等で残った青カーソルを即座にOS標準白矢印へ強制リセット
restore_system_cursor(force=True)
atexit.register(lambda: restore_system_cursor(force=True))
